# cSpell:words httpx asyncpg sqlalchemy Opendata pymupdf
"""On-call + PostgreSQL-cached client for Taiwan APHIA (pesticide.aphia.gov.tw).

Queries:
  1. OpendataList?code=2 (農藥評估資料 — 15-year post-registration evaluation)
  2. OpendataList?code=3 -> PDF (可能致癌農藥限制登記及評估狀態), parsed with PyMuPDF on refresh/sync
  3. NewsList?type=new (農藥公告) filtered by Chinese common name & '預先通知公告'
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx
import pymupdf
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AphiaQueryCache, AphiaRecord

log = logging.getLogger(__name__)

APHIA_ORIGIN = "https://pesticide.aphia.gov.tw"
APHIA_TIMEOUT = httpx.Timeout(8.0)
CACHE_TTL = timedelta(hours=6)

_FORMULATION_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*%|\b(?:SC|WP|EC|EW|SL|WG|GR|DP|SP|FS|ME|SE|OD|CS|WS|SG|DF)\b",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(r"[+＋・·/、與;()（）]+|,\s+")
_LATIN_TOKEN_RE = re.compile(r"^[A-Za-z0-9.,_-]+$")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def validate_aphia_url(raw_href: str | None) -> str | None:
    """Resolve relative links against APHIA_ORIGIN, encode non-ASCII paths, and reject non-APHIA URLs."""
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
    safe_path = quote(parsed.path, safe="/%")
    return urlunsplit((parsed.scheme, parsed.netloc, safe_path, parsed.query, ""))


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
    """Boundary-aware match preventing '2,4-D' from matching '2,4-DB'."""
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


def parse_assessment_records(html_text: str) -> list[dict[str, Any]]:
    """Parse APHIA OpendataList?code=2 (農藥評估資料) table HTML into normalized dicts."""
    parser = _TableParser()
    parser.feed(html_text)
    records: list[dict[str, Any]] = []
    for row in parser.rows:
        fields: dict[str, str] = row["fields"]
        values = [v for v in fields.values() if v]
        if not values:
            continue
        title = fields.get("檔案名稱") or values[0]
        pub_date = fields.get("發佈日期") or next(
            (v for v in values if re.match(r"^\d{4}-\d{2}-\d{2}$", v)), None
        )
        detail_url = validate_aphia_url(row.get("href"))
        key_src = f"assessment|{title}"
        records.append(
            {
                "source_kind": "assessment",
                "source_key": hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32],
                "title": title,
                "common_name": title,
                "published_date": pub_date[:32] if pub_date else None,
                "detail_url": detail_url,
                "raw_fields_json": fields,
            }
        )
    return records


def parse_carcinogen_pdf(pdf_bytes: bytes, pdf_url: str | None) -> list[dict[str, Any]]:
    """Parse APHIA 可能致癌農藥狀態 PDF tables using PyMuPDF."""
    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            tables = page.find_tables()
            for table in tables.tables:
                for raw_row in table.extract():
                    cells = [
                        re.sub(r"\s+", " ", c).strip() if isinstance(c, str) else ""
                        for c in raw_row
                    ]
                    if len(cells) < 3:
                        continue
                    grade, zh_name, en_name = cells[0], cells[1], cells[2]
                    if grade in ("等級", "") and zh_name in ("藥劑中文名", ""):
                        continue
                    if grade not in ("B", "C") and not zh_name and not en_name:
                        continue
                    status_text = cells[5] if len(cells) > 5 else (cells[4] if len(cells) > 4 else "")
                    eval_text = cells[4] if len(cells) > 4 else ""
                    title = f"{zh_name} ({en_name}) [{grade}級] — {status_text or eval_text}".strip(" —")
                    key_src = f"carcinogen|{grade}|{zh_name}|{en_name}"
                    skey = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32]
                    if skey in seen_keys:
                        continue
                    seen_keys.add(skey)
                    records.append(
                        {
                            "source_kind": "carcinogen",
                            "source_key": skey,
                            "title": title,
                            "common_name": f"{zh_name} {en_name}".strip(),
                            "published_date": None,
                            "detail_url": pdf_url,
                            "raw_fields_json": {
                                "grade": grade,
                                "zh_name": zh_name,
                                "en_name": en_name,
                                "us_epa": cells[3] if len(cells) > 3 else "",
                                "evaluation": eval_text,
                                "status": status_text,
                            },
                        }
                    )
    return records


def parse_pre_notice_records(html_text: str) -> list[dict[str, Any]]:
    """Parse APHIA NewsList HTML and keep only '預先通知公告' announcements."""
    parser = _NewsListParser()
    parser.feed(html_text)
    records: list[dict[str, Any]] = []
    for item in parser.items:
        title = str(item["title"])
        if "預先通知公告" not in title:
            continue
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
    records.sort(key=lambda r: r.get("published_date") or "", reverse=True)
    return records


def _extract_first_pdf_url(html_text: str) -> str | None:
    for match in re.findall(r'href=["\']([^"\']+\.pdf)["\']', html_text, re.IGNORECASE):
        valid = validate_aphia_url(match)
        if valid:
            return valid
    return None


def _build_query_key(tokens: list[str]) -> str:
    joined = "|".join(sorted(t.casefold() for t in tokens))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:64]


async def _fetch_text(http: httpx.AsyncClient, url: str) -> str:
    resp = await http.get(
        url,
        headers={"User-Agent": "ComplianceAgent/1.0"},
        follow_redirects=False,
        timeout=APHIA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


async def _fetch_bytes(http: httpx.AsyncClient, url: str) -> bytes:
    resp = await http.get(
        url,
        headers={"User-Agent": "ComplianceAgent/1.0"},
        follow_redirects=False,
        timeout=APHIA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.content


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


async def _refresh_carcinogen_records(
    session: AsyncSession, http: httpx.AsyncClient
) -> list[dict[str, Any]]:
    html3 = await _fetch_text(
        http, f"{APHIA_ORIGIN}/information/Data/OpendataList/?code=3&keyword=&newquery=true"
    )
    pdf_url = _extract_first_pdf_url(html3)
    if not pdf_url:
        return []
    pdf_bytes = await _fetch_bytes(http, pdf_url)
    records = parse_carcinogen_pdf(pdf_bytes, pdf_url)
    await _upsert_records(session, records)
    return records


async def lookup_aphia_for_tokens(
    session: AsyncSession,
    http: httpx.AsyncClient,
    tokens: list[str],
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Query 農藥評估資料 (code=2), 可能致癌 PDF (code=3), and latest 預先通知公告."""
    now = datetime.now(timezone.utc)
    if not tokens:
        return {
            "keywords": [],
            "carcinogen_matches": [],
            "assessment_matches": [],
            "latest_pre_notice": None,
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
            "carcinogen_matches": data.get("carcinogen_matches", []),
            "assessment_matches": data.get("assessment_matches", []),
            "latest_pre_notice": data.get("latest_pre_notice"),
            "stale": False,
            "fetched_at": cached_row.fetched_at,
        }

    chinese_tokens = [t for t in tokens if _CJK_RE.search(t)]

    # 1) Carcinogen PDF: parse only when force_refresh=True or aphia_records has no carcinogen rows
    existing_carcinogens = (
        await session.execute(select(AphiaRecord).where(AphiaRecord.source_kind == "carcinogen"))
    ).scalars().all()

    carcinogen_pool: list[dict[str, Any]] = [
        {
            "source_kind": r.source_kind,
            "source_key": r.source_key,
            "title": r.title,
            "common_name": r.common_name,
            "published_date": r.published_date,
            "detail_url": r.detail_url,
            "raw_fields_json": r.raw_fields_json,
        }
        for r in existing_carcinogens
    ]

    async def _maybe_refresh_carcinogen() -> list[dict[str, Any]] | None:
        if force_refresh or not carcinogen_pool:
            return await _refresh_carcinogen_records(session, http)
        return None

    # 2) Assessment (code=2) + 3) Pre-notice announcements (NewsList?type=new for Chinese tokens)
    fetch_tasks: list[Any] = [_maybe_refresh_carcinogen()]
    meta: list[tuple[str, str]] = [("carcinogen_refresh", "")]
    for tok in tokens[:3]:
        fetch_tasks.append(
            _fetch_text(
                http,
                f"{APHIA_ORIGIN}/information/Data/OpendataList/?code=2&keyword={quote(tok)}&newquery=true",
            )
        )
        meta.append(("assessment", tok))
    for zh_tok in chinese_tokens[:2]:
        fetch_tasks.append(
            _fetch_text(
                http,
                f"{APHIA_ORIGIN}/information/Data/NewsList/?type=new&keyword={quote(zh_tok)}&newquery=true",
            )
        )
        meta.append(("announcement", zh_tok))

    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    assessment_matches: list[dict[str, Any]] = []
    pre_notices: list[dict[str, Any]] = []
    any_succeeded = False

    for (kind, tok), res in zip(meta, results):
        if isinstance(res, Exception):
            log.warning("APHIA fetch failed (%s, %s): %s", kind, tok, res)
            continue
        if kind == "carcinogen_refresh":
            if isinstance(res, list):
                carcinogen_pool = res
                any_succeeded = True
            continue
        any_succeeded = True
        if kind == "assessment" and isinstance(res, str):
            for rec in parse_assessment_records(res):
                if _matches_token(rec["title"], tok):
                    assessment_matches.append(rec)
        elif kind == "announcement" and isinstance(res, str):
            for rec in parse_pre_notice_records(res):
                if _matches_token(rec["title"], tok):
                    pre_notices.append(rec)

    if not any_succeeded and not carcinogen_pool:
        if cached_row is not None:
            data = cached_row.result_json
            return {
                "keywords": tokens,
                "carcinogen_matches": data.get("carcinogen_matches", []),
                "assessment_matches": data.get("assessment_matches", []),
                "latest_pre_notice": data.get("latest_pre_notice"),
                "stale": True,
                "fetched_at": cached_row.fetched_at,
            }
        return {
            "keywords": tokens,
            "carcinogen_matches": [],
            "assessment_matches": [],
            "latest_pre_notice": None,
            "stale": True,
            "fetched_at": now,
        }

    carcinogen_matches: list[dict[str, Any]] = []
    seen_carc: set[str] = set()
    for rec in carcinogen_pool:
        haystack = f"{rec.get('common_name') or ''} {rec.get('title') or ''}"
        if any(_matches_token(haystack, tok) for tok in tokens):
            if rec["source_key"] not in seen_carc:
                seen_carc.add(rec["source_key"])
                carcinogen_matches.append(rec)

    # Deduplicate assessments & pick latest 預先通知公告
    seen_assess: set[str] = set()
    dedup_assess: list[dict[str, Any]] = []
    for rec in assessment_matches:
        if rec["source_key"] not in seen_assess:
            seen_assess.add(rec["source_key"])
            dedup_assess.append(rec)

    pre_notices.sort(key=lambda r: r.get("published_date") or "", reverse=True)
    latest_pre_notice = pre_notices[0] if pre_notices else None

    await _upsert_records(session, dedup_assess + ([latest_pre_notice] if latest_pre_notice else []))

    result_payload: dict[str, Any] = {
        "carcinogen_matches": carcinogen_matches,
        "assessment_matches": dedup_assess,
        "latest_pre_notice": latest_pre_notice,
    }
    cache_stmt = insert(AphiaQueryCache).values(
        query_key=qkey,
        keywords_json=tokens,
        result_json=result_payload,
        fetched_at=now,
    )
    cache_stmt = cache_stmt.on_conflict_do_update(
        index_elements=[AphiaQueryCache.query_key],
        set_={
            "keywords_json": tokens,
            "result_json": result_payload,
            "fetched_at": now,
        },
    )
    await session.execute(cache_stmt)
    await session.commit()

    return {
        "keywords": tokens,
        **result_payload,
        "stale": False,
        "fetched_at": now,
    }


async def sync_aphia_catalog(session: AsyncSession, http: httpx.AsyncClient) -> int:
    """Sync 農藥評估資料 (code=2), 可能致癌 PDF (code=3), and recent 預先通知公告."""
    carc = await _refresh_carcinogen_records(session, http)
    html2 = await _fetch_text(
        http, f"{APHIA_ORIGIN}/information/Data/OpendataList/?code=2&keyword=&newquery=true"
    )
    assess = parse_assessment_records(html2)
    html_news = await _fetch_text(
        http, f"{APHIA_ORIGIN}/information/Data/NewsList/?type=new&keyword=&newquery=true"
    )
    news = parse_pre_notice_records(html_news)
    all_records = carc + assess + news
    await _upsert_records(session, all_records)
    await session.commit()
    return len(all_records)
