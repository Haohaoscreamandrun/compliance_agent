# cSpell:words httpx asyncpg sqlalchemy Opendata Forbid
"""On-call + PostgreSQL-cached client for Taiwan APHIA (pesticide.aphia.gov.tw).

Queries official announcements (農藥公告), banned/restricted/deleted/carcinogen lists
(Forbid1List..3List, OpendataList?code=3), and ACRI/toxicology risk assessments
(OpendataList?code=6,8), using stdlib HTMLParser and strict origin/redirect guards.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AphiaQueryCache, AphiaRecord

log = logging.getLogger(__name__)

APHIA_ORIGIN = "https://pesticide.aphia.gov.tw"
APHIA_TIMEOUT = httpx.Timeout(5.0)
CACHE_TTL = timedelta(hours=6)

_FORMULATION_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*%|\b(?:SC|WP|EC|EW|SL|WG|GR|DP|SP|FS|ME|SE|OD|CS|WS|SG|DF)\b",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"[+＋・·/、與;()（）]+|,\s+")
_LATIN_TOKEN_RE = re.compile(r"^[A-Za-z0-9.,_-]+$")

# (endpoint_path, extra_params, source_kind)
_TABLE_ENDPOINTS: tuple[tuple[str, dict[str, str], str], ...] = (
    ("/information/Data/Forbid1List/", {"pagesize": "50", "newquery": "true"}, "forbid_banned"),
    ("/information/Data/Forbid2List/", {"pagesize": "50", "newquery": "true"}, "forbid_restricted"),
    ("/information/Data/Forbid3List/", {"pagesize": "50", "newquery": "true"}, "forbid_deleted"),
    ("/information/Data/OpendataList/", {"code": "3", "newquery": "true"}, "carcinogen"),
    ("/information/Data/OpendataList/", {"code": "6", "newquery": "true"}, "assessment"),
    ("/information/Data/OpendataList/", {"code": "8", "newquery": "true"}, "assessment"),
)


def validate_aphia_url(raw_href: str | None) -> str | None:
    """Resolve relative links against APHIA_ORIGIN and reject any non-APHIA/unsafe URL."""
    if not raw_href or not isinstance(raw_href, str):
        return None
    cleaned = raw_href.strip()
    if not cleaned or cleaned.startswith("#"):
        return None
    lower = cleaned.casefold()
    if lower.startswith(("javascript:", "data:", "vbscript:")):
        return None
    resolved = urljoin(f"{APHIA_ORIGIN}/", cleaned)
    parsed = urlsplit(resolved)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "pesticide.aphia.gov.tw"
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.port or 443) != 443
    ):
        return None
    return resolved


def extract_ingredient_tokens(*names: str | None) -> list[str]:
    """Extract clean, >=2-char ingredient search tokens from MOA chinese/english names."""
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in names:
        if not raw or not isinstance(raw, str):
            continue
        cleaned = _FORMULATION_RE.sub(" ", raw)
        for part in _SPLIT_RE.split(cleaned):
            tok = part.strip(" \t\r\n.-")
            if len(tok) < 2:
                continue
            key = tok.casefold()
            if key not in seen:
                seen.add(key)
                tokens.append(tok)
    return tokens


def _matches_token(text: str, token: str) -> bool:
    """Boundary-aware match preventing '2,4-D' from matching '2,4-DB' or noisy 1-char hits."""
    if not text or not token or len(token) < 2:
        return False
    if _LATIN_TOKEN_RE.match(token):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])"
        return bool(re.search(pattern, text, re.IGNORECASE))
    return token.casefold() in text.casefold()


class _TableParser(HTMLParser):
    """Minimal stdlib HTML table extractor yielding row dicts + first link."""

    def __init__(self) -> None:
        super().__init__()
        self.headers: list[str] = []
        self.rows: list[dict[str, Any]] = []
        self._in_thead = False
        self._in_row = False
        self._cell_tag: str | None = None
        self._cell_buf: list[str] = []
        self._cur_cells: list[str] = []
        self._cur_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "thead":
            self._in_thead = True
        elif t == "tr":
            self._in_row = True
            self._cur_cells = []
            self._cur_href = None
        elif t in ("th", "td") and self._in_row:
            self._cell_tag = t
            self._cell_buf = []
        elif t == "a" and self._in_row and self._cur_href is None:
            for k, v in attrs:
                if k.lower() == "href" and v:
                    self._cur_href = v
                    break

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "thead":
            self._in_thead = False
        elif t in ("th", "td") and self._cell_tag == t:
            text = re.sub(r"\s+", " ", "".join(self._cell_buf)).strip()
            self._cur_cells.append(text)
            self._cell_tag = None
        elif t == "tr" and self._in_row:
            self._in_row = False
            if self._in_thead and not self.headers and self._cur_cells:
                self.headers = self._cur_cells
            elif not self._in_thead and self._cur_cells:
                if not self.headers and all(len(c) < 30 for c in self._cur_cells):
                    self.headers = self._cur_cells
                else:
                    fields = {
                        (self.headers[i] if i < len(self.headers) else f"col_{i}"): val
                        for i, val in enumerate(self._cur_cells)
                    }
                    self.rows.append({"fields": fields, "href": self._cur_href})

    def handle_data(self, data: str) -> None:
        if self._cell_tag is not None:
            self._cell_buf.append(data)


class _NewsListParser(HTMLParser):
    """Minimal stdlib extractor for APHIA NewsList `<ul class='news-list'>` items."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, str | None]] = []
        self._in_list = False
        self._in_link = False
        self._cur_href: str | None = None
        self._span_class: str | None = None
        self._date_buf: list[str] = []
        self._title_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        attr_map = dict(attrs)
        cls = attr_map.get("class") or ""
        if t == "ul" and "news-list" in cls:
            self._in_list = True
        elif t == "a" and self._in_list:
            self._in_link = True
            self._cur_href = attr_map.get("href")
            self._date_buf = []
            self._title_buf = []
        elif t == "span" and self._in_link:
            self._span_class = cls

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "ul" and self._in_list:
            self._in_list = False
        elif t == "span" and self._in_link:
            self._span_class = None
        elif t == "a" and self._in_link:
            self._in_link = False
            title = re.sub(r"\s+", " ", "".join(self._title_buf)).strip()
            pub_date = re.sub(r"\s+", " ", "".join(self._date_buf)).strip() or None
            if title:
                self.items.append(
                    {"title": title, "published_date": pub_date, "href": self._cur_href}
                )

    def handle_data(self, data: str) -> None:
        if not self._in_link:
            return
        if self._span_class and "news-date" in self._span_class:
            self._date_buf.append(data)
        else:
            self._title_buf.append(data)


def parse_table_records(html_text: str, source_kind: str) -> list[dict[str, Any]]:
    """Parse APHIA table HTML fragment into normalized record dicts."""
    parser = _TableParser()
    parser.feed(html_text)
    records: list[dict[str, Any]] = []
    for row in parser.rows:
        fields: dict[str, str] = row["fields"]
        values = [v for v in fields.values() if v]
        if not values:
            continue
        common_name = next(
            (v for k, v in fields.items() if any(h in k for h in ("普通名稱", "農藥名稱", "成分", "藥劑"))),
            values[0],
        )
        title = " — ".join(values[:3])
        pub_date = next(
            (v for k, v in fields.items() if any(h in k for h in ("公告日期", "日期", "生效日", "年度"))),
            None,
        )
        detail_url = validate_aphia_url(row.get("href"))
        key_src = f"{source_kind}|{common_name}|{title}"
        records.append(
            {
                "source_kind": source_kind,
                "source_key": hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32],
                "title": title,
                "common_name": common_name,
                "published_date": pub_date[:32] if pub_date else None,
                "detail_url": detail_url,
                "raw_fields_json": fields,
            }
        )
    return records


def parse_news_records(html_text: str) -> list[dict[str, Any]]:
    """Parse APHIA NewsList HTML fragment into normalized announcement dicts."""
    parser = _NewsListParser()
    parser.feed(html_text)
    records: list[dict[str, Any]] = []
    for item in parser.items:
        title = str(item["title"])
        detail_url = validate_aphia_url(item.get("href"))
        source_key = (
            detail_url.rsplit("/", 1)[-1][:64]
            if detail_url
            else hashlib.sha256(title.encode("utf-8")).hexdigest()[:32]
        )
        records.append(
            {
                "source_kind": "announcement",
                "source_key": source_key,
                "title": title,
                "common_name": None,
                "published_date": (item.get("published_date") or "")[:32] or None,
                "detail_url": detail_url,
                "raw_fields_json": {"title": title, "published_date": item.get("published_date")},
            }
        )
    return records


def _build_query_key(tokens: list[str]) -> str:
    joined = "|".join(sorted(t.casefold() for t in tokens))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:64]


def _categorize_records(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    forbid_matches: list[dict[str, Any]] = []
    assessment_matches: list[dict[str, Any]] = []
    announcements: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for rec in records:
        key = (rec["source_kind"], rec["source_key"])
        if key in seen:
            continue
        seen.add(key)
        kind = rec["source_kind"]
        if kind in ("forbid_banned", "forbid_restricted", "forbid_deleted", "carcinogen"):
            forbid_matches.append(rec)
        elif kind == "assessment":
            assessment_matches.append(rec)
        else:
            announcements.append(rec)
    return {
        "forbid_matches": forbid_matches,
        "assessment_matches": assessment_matches,
        "announcements": announcements,
    }


async def _fetch_html(http: httpx.AsyncClient, path: str, params: dict[str, str]) -> str:
    query = "&".join(f"{quote(k)}={quote(v)}" for k, v in params.items())
    url = f"{APHIA_ORIGIN}{path}?{query}"
    resp = await http.get(
        url,
        headers={"User-Agent": "ComplianceAgent/1.0"},
        follow_redirects=False,
        timeout=APHIA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


async def _upsert_records(session: AsyncSession, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    now = datetime.now(timezone.utc)
    for rec in records:
        stmt = insert(AphiaRecord).values(**rec, fetched_at=now)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_aphia_source",
            set_={
                "title": stmt.excluded.title,
                "common_name": stmt.excluded.common_name,
                "published_date": stmt.excluded.published_date,
                "detail_url": stmt.excluded.detail_url,
                "raw_fields_json": stmt.excluded.raw_fields_json,
                "fetched_at": now,
            },
        )
        await session.execute(stmt)


async def lookup_aphia_for_tokens(
    session: AsyncSession,
    http: httpx.AsyncClient,
    tokens: list[str],
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Query APHIA announcements, forbid lists & assessments with 6h DB cache + stale fallback."""
    now = datetime.now(timezone.utc)
    if not tokens:
        return {
            "keywords": [],
            "forbid_matches": [],
            "assessment_matches": [],
            "announcements": [],
            "stale": False,
            "fetched_at": now,
        }

    qkey = _build_query_key(tokens)
    cached_row = (
        await session.execute(select(AphiaQueryCache).where(AphiaQueryCache.query_key == qkey))
    ).scalar_one_or_none()

    if (
        not force_refresh
        and cached_row is not None
        and cached_row.fetched_at.astimezone(timezone.utc) + CACHE_TTL > now
    ):
        data = cached_row.result_json
        return {
            "keywords": tokens,
            "forbid_matches": data.get("forbid_matches", []),
            "assessment_matches": data.get("assessment_matches", []),
            "announcements": data.get("announcements", []),
            "stale": False,
            "fetched_at": cached_row.fetched_at,
        }

    # Fetch each table/news endpoint per token concurrently with 5s timeout
    tasks: list[Any] = []
    meta: list[tuple[str, str]] = []
    for tok in tokens[:3]:
        for path, extra, kind in _TABLE_ENDPOINTS:
            params = {**extra, "keyword": tok}
            tasks.append(_fetch_html(http, path, params))
            meta.append((kind, tok))
        tasks.append(
            _fetch_html(http, "/information/Data/NewsList/", {"type": "new", "keyword": tok, "newquery": "true"})
        )
        meta.append(("announcement", tok))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    matched: list[dict[str, Any]] = []
    any_succeeded = False

    for (kind, tok), res in zip(meta, results):
        if isinstance(res, Exception):
            log.warning("APHIA fetch failed (%s, %s): %s", kind, tok, res)
            continue
        any_succeeded = True
        parsed = parse_news_records(res) if kind == "announcement" else parse_table_records(res, kind)
        for rec in parsed:
            haystack = f"{rec.get('common_name') or ''} {rec.get('title') or ''}"
            if _matches_token(haystack, tok):
                matched.append(rec)

    if not any_succeeded:
        if cached_row is not None:
            data = cached_row.result_json
            return {
                "keywords": tokens,
                "forbid_matches": data.get("forbid_matches", []),
                "assessment_matches": data.get("assessment_matches", []),
                "announcements": data.get("announcements", []),
                "stale": True,
                "fetched_at": cached_row.fetched_at,
            }
        return {
            "keywords": tokens,
            "forbid_matches": [],
            "assessment_matches": [],
            "announcements": [],
            "stale": True,
            "fetched_at": now,
        }

    categorized = _categorize_records(matched)
    await _upsert_records(session, matched)
    cache_stmt = insert(AphiaQueryCache).values(
        query_key=qkey,
        keywords_json=tokens,
        result_json=categorized,
        fetched_at=now,
    )
    cache_stmt = cache_stmt.on_conflict_do_update(
        index_elements=[AphiaQueryCache.query_key],
        set_={
            "keywords_json": tokens,
            "result_json": categorized,
            "fetched_at": now,
        },
    )
    await session.execute(cache_stmt)
    await session.commit()

    return {
        "keywords": tokens,
        **categorized,
        "stale": False,
        "fetched_at": now,
    }


async def sync_aphia_catalog(session: AsyncSession, http: httpx.AsyncClient) -> int:
    """Sync full APHIA static/recent tables (Forbid1..3, Opendata 3/6/8, NewsList) into aphia_records."""
    tasks = [
        _fetch_html(http, path, {**extra, "keyword": ""})
        for path, extra, _ in _TABLE_ENDPOINTS
    ]
    tasks.append(_fetch_html(http, "/information/Data/NewsList/", {"type": "new", "keyword": "", "newquery": "true"}))
    kinds = [kind for _, _, kind in _TABLE_ENDPOINTS] + ["announcement"]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_records: list[dict[str, Any]] = []
    for kind, res in zip(kinds, results):
        if isinstance(res, Exception):
            log.warning("APHIA sync failed (%s): %s", kind, res)
            continue
        parsed = parse_news_records(res) if kind == "announcement" else parse_table_records(res, kind)
        all_records.extend(parsed)

    await _upsert_records(session, all_records)
    await session.commit()
    return len(all_records)
