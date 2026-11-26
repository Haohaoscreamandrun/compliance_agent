# cSpell:words MOAOPD httpx asyncpg JSONB
"""On-call Taiwan MOA EIR027 pesticide lookup with a PostgreSQL snapshot cache (ADR-001 Decision 2).

Verified against the live API (manual MOAOPD-API-EIR027 v1.2):
  * filter syntax:  ?$top=1000&$filter=許可證字+like+農藥製+and+許可證號+like+00001
  * unknown permit -> HTTP 200 with `[]`
  * the core row carries the sub-endpoint URLs; 農藥使用範圍 / 農藥條碼資料 / 混合劑百分率 return JSON lists,
    but 許可證標示 returns an HTML page, so only its URL is stored.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, NamedTuple
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PUBLIC_DEMO_REG_NO, MoaRegistry

log = logging.getLogger(__name__)

_REG_NO = re.compile(r"^([^0-9]+)([0-9]+)$")  # ASCII digits only: fullwidth digits must not alias a key
MAX_ROWS = 1000  # MOA caps one answer at 1000 rows; hitting it means the match may be truncated
# core-row URL field -> MoaRegistry JSONB column (JSON list responses)
_JSON_SUB_ENDPOINTS = {
    "農藥使用範圍": "approved_usages_json",
    "農藥條碼資料": "barcode_json",
    "混合劑百分率": "mixture_ratio_json",
}


class MoaNotFoundError(LookupError):
    """MOA answered, but no permit matches the requested reg_no."""


class MoaUnavailableError(RuntimeError):
    """MOA could not be reached (or answered garbage) and no cached snapshot exists."""


class RegistryResult(NamedTuple):
    snapshot: MoaRegistry
    stale: bool  # True: MOA was unreachable and `snapshot` is the cached copy (see snapshot.fetched_at)


def split_reg_no(reg_no: str) -> tuple[str, str]:
    """'農藥製00001' -> ('農藥製', '00001')."""
    match = _REG_NO.match(reg_no.strip())
    if not match:
        raise ValueError(f"reg_no must look like '農藥製00001', got {reg_no!r}")
    return match.group(1), match.group(2)


def build_filter_url(base_url: str, permit_type: str, permit_number: str) -> str:
    """Build the manual's `$filter` URL ('+' for spaces, UTF-8 percent-encoded Chinese)."""
    tokens = ["許可證字", "like", permit_type, "and", "許可證號", "like", permit_number]
    return f"{base_url}?$top={MAX_ROWS}&$filter=" + "+".join(quote(t, safe="") for t in tokens)


def _text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def core_to_columns(row: dict[str, Any]) -> dict[str, Any]:
    """Map one MOA core row to MoaRegistry columns (raw row kept whole in raw_core_json)."""
    permit_type = row["許可證字"].strip()
    permit_number = row["許可證號"].strip()
    return {
        "reg_no": f"{permit_type}{permit_number}",
        "permit_type": permit_type,
        "permit_number": permit_number,
        "chinese_name": _text(row.get("中文名稱")),
        "pesticide_code": _text(row.get("農藥代號")),
        "english_name": _text(row.get("英文名稱")),
        "brand_name": _text(row.get("廠牌名稱")),
        "company_name": _text(row.get("廠商名稱")),
        "foreign_manufacturer": _text(row.get("國外原製造廠商")),
        "formulation": _text(row.get("劑型")),
        "content_pct": _text(row.get("含量")),
        "valid_until_roc": _text(row.get("有效期限")),
        "revocation_status": _text(row.get("撤銷類別")),
        "raw_core_json": row,
    }


def _trusted(url: str, base_url: str) -> bool:
    """Only request sub-endpoint URLs on the API's own https origin (URLs come from remote data)."""
    try:
        u, b = urlsplit(url), urlsplit(base_url)
        return (
            b.scheme == "https"
            and u.scheme == "https"
            and u.hostname is not None
            and u.hostname == b.hostname
            and (u.port or 443) == (b.port or 443)
            and u.username is None
            and u.password is None
        )
    except ValueError:  # malformed URL / port
        return False


async def _get(http: httpx.AsyncClient, url: str) -> httpx.Response:
    # Never follow redirects, whatever the caller configured on the client (SSRF).
    response = await http.get(url, follow_redirects=False)
    response.raise_for_status()  # 3xx raises too
    return response


async def _fetch_core_row(http: httpx.AsyncClient, base_url: str, reg_no: str) -> dict[str, Any]:
    permit_type, permit_number = split_reg_no(reg_no)
    payload = (await _get(http, build_filter_url(base_url, permit_type, permit_number))).json()
    if not isinstance(payload, list) or len(payload) >= MAX_ROWS or not all(isinstance(r, dict) for r in payload):
        raise ValueError("unexpected MOA payload (not a list of objects, or possibly truncated)")
    # `like` is a substring match, so keep only the exact permit.
    for row in payload:
        if _text(row.get("許可證字")) == permit_type and _text(row.get("許可證號")) == permit_number:
            return row
    raise MoaNotFoundError(f"MOA has no permit {permit_type}{permit_number}")


async def fetch_registry_columns(
    http: httpx.AsyncClient,
    base_url: str,
    reg_no: str,
    cached: MoaRegistry | None = None,
) -> dict[str, Any]:
    """Fetch the live core row + sub-endpoints and return MoaRegistry column values (no DB access).

    A failed or malformed sub-endpoint keeps its previously cached value instead of wiping it.
    An empty list from MOA is treated as truth (the permit really lost those entries).
    """
    row = await _fetch_core_row(http, base_url, reg_no)
    columns = core_to_columns(row)

    async def fetch_sub(field: str, column: str) -> tuple[str, Any]:
        kept = getattr(cached, column, None)
        try:
            url = row.get(field)
            if not url:  # MOA says this permit has none
                return column, None
            if not isinstance(url, str) or not _trusted(url, base_url):
                log.warning("Ignoring untrusted MOA sub-endpoint URL for %s", field)
                return column, kept
            data = (await _get(http, url)).json()
            if not isinstance(data, list):
                raise ValueError("sub-endpoint did not return a list")
            return column, data
        except Exception:  # one bad sub-endpoint must never fail the whole lookup
            log.warning("MOA sub-endpoint %s failed for %s; keeping cached value", field, columns["reg_no"])
            return column, kept

    columns.update(await asyncio.gather(*(fetch_sub(f, c) for f, c in _JSON_SUB_ENDPOINTS.items())))

    label_url = row.get("許可證標示")  # HTML page, not JSON: store the link only
    columns["license_label_json"] = (
        {"url": label_url} if isinstance(label_url, str) and _trusted(label_url, base_url) else None
    )
    return columns


def build_upsert(columns: dict[str, Any]) -> Insert:
    """INSERT ... ON CONFLICT (reg_no) DO UPDATE: safe when two audits fetch the same new permit."""
    return (
        insert(MoaRegistry)
        .values(**columns)
        .on_conflict_do_update(
            index_elements=["reg_no"],
            set_={k: v for k, v in columns.items() if k != "reg_no"},
        )
        .returning(MoaRegistry)
    )


async def get_registry_snapshot(
    session: AsyncSession,
    http: httpx.AsyncClient,
    base_url: str,
    reg_no: str,
) -> RegistryResult:
    """Freshest snapshot: live MOA data upserted into PostgreSQL, else the cached row (stale=True).

    Caller contract:
      * this COMMITS `session` (our session factory sets expire_on_commit=False; keep that, or the
        returned snapshot expires and lazy-loads outside an async context);
      * `session.get` opens a transaction, so one pooled connection is held during the MOA calls.
        shortcut: fine for a single RA team; read-then-release the connection if pool pressure appears.
      * two concurrent fetches are last-writer-wins; MOA data changes weekly so that is acceptable.
    """
    permit_type, permit_number = split_reg_no(reg_no)
    key = f"{permit_type}{permit_number}"
    cached = await session.get(MoaRegistry, key)
    if key == PUBLIC_DEMO_REG_NO and isinstance(cached, MoaRegistry):
        return RegistryResult(cached, stale=False)
    try:
        columns = await fetch_registry_columns(http, base_url, key, cached)
    except (httpx.HTTPError, ValueError) as exc:
        if cached is not None:
            log.warning("MOA unreachable for %s; serving cached snapshot from %s", key, cached.fetched_at)
            return RegistryResult(cached, stale=True)
        raise MoaUnavailableError(f"MOA unreachable and no cached snapshot for {key}") from exc

    columns["fetched_at"] = datetime.now(timezone.utc)  # ORM onupdate does not fire on ON CONFLICT
    try:
        snapshot = (
            await session.scalars(build_upsert(columns), execution_options={"populate_existing": True})
        ).one()
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return RegistryResult(snapshot, stale=False)
