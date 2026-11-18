# cSpell:words MOAOPD httpx asyncpg JSONB
"""Tests for CL 2.2: on-call MOA EIR027 client (HTTP mocked; real-Postgres test needs TEST_DATABASE_URL)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from app.db.models import Base, MoaRegistry
from app.services.moa_client import (
    MAX_ROWS,
    MoaNotFoundError,
    MoaUnavailableError,
    _trusted,
    build_filter_url,
    build_upsert,
    core_to_columns,
    fetch_registry_columns,
    get_registry_snapshot,
    split_reg_no,
)

BASE = "https://data.moa.gov.tw/Service/OpenData/FromM/PesticideData.aspx"
DETAIL = "https://data.moa.gov.tw/Service/OpenData/FromM/PesticideDetail.aspx?ltyp=10&lno=00001"
LABEL = "https://data.moa.gov.tw/Service/OpenData/FromM/PesticideLicImage.aspx?licType=10&licNo=00001"
BARCODE = "https://data.moa.gov.tw/Service/OpenData/FromM/PesticideBrcode.aspx?licType=10&licNo=00001"
MIXTURE = "https://data.moa.gov.tw/Service/OpenData/FromM/PesticideGropty.aspx?pestcd=F060&cidecd=WP&pescnt=75.000"

CORE_ROW = {
    "許可證字": "農藥製", "許可證號": "00001", "中文名稱": "快得保淨", "農藥代號": "F060",
    "英文名稱": "THIOPHANATE-METHYL + OXINE-COPPER", "廠牌名稱": "好菌淨", "化學成分": "...",
    "國外原製造廠商": None, "劑型": "WP", "含量": "75.000 (%)", "有效期限": "117/10/11",
    "廠商名稱": "好速化學股份有限公司", "撤銷類別": "", "撤銷日期": None,
    "農藥使用範圍": DETAIL, "許可證標示": LABEL, "農藥條碼資料": BARCODE, "混合劑百分率": MIXTURE,
}
SUBS = {DETAIL: [{"作物名稱": "木瓜"}], BARCODE: [{"Barcode": "4711242092192"}], MIXTURE: [{"pestcd": "F060"}]}


def make_client(core_rows, subs=None, failing=(), **client_kwargs):
    """httpx client whose transport answers the core query and sub-endpoints from dicts."""
    subs = SUBS if subs is None else subs

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(BASE):
            return httpx.Response(200, json=core_rows)
        if url in failing:
            return httpx.Response(500)
        return httpx.Response(200, json=subs[url])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), **client_kwargs)


def test_split_reg_no() -> None:
    assert split_reg_no("農藥製00001") == ("農藥製", "00001")
    assert split_reg_no(" 農藥原進01234 ") == ("農藥原進", "01234")
    for bad in ("", "00001", "農藥製", "abc", "農藥製００００１"):  # last one: fullwidth digits
        with pytest.raises(ValueError):
            split_reg_no(bad)


def test_build_filter_url_matches_manual_syntax() -> None:
    assert build_filter_url(BASE, "農藥製", "00001") == (
        f"{BASE}?$top=1000&$filter=%E8%A8%B1%E5%8F%AF%E8%AD%89%E5%AD%97+like+%E8%BE%B2%E8%97%A5%E8%A3%BD"
        "+and+%E8%A8%B1%E5%8F%AF%E8%AD%89%E8%99%9F+like+00001"
    )


def test_core_to_columns_blanks_become_none_and_raw_is_kept() -> None:
    cols = core_to_columns(CORE_ROW)
    assert cols["reg_no"] == "農藥製00001"
    assert cols["foreign_manufacturer"] is None
    assert cols["revocation_status"] is None  # "" -> None
    assert cols["valid_until_roc"] == "117/10/11"
    assert cols["raw_core_json"] is CORE_ROW


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        (DETAIL, True),
        ("http://data.moa.gov.tw/x", False),  # not https
        ("https://data.moa.gov.tw:8443/x", False),  # other port
        ("https://user:pw@data.moa.gov.tw/x", False),  # userinfo
        ("https://evil.example.com/x", False),
        ("https://data.moa.gov.tw.evil.com/x", False),
        ("https://[", False),  # malformed
    ],
)
def test_trusted_origin(url: str, ok: bool) -> None:
    assert _trusted(url, BASE) is ok
    assert _trusted(DETAIL, "http://data.moa.gov.tw/x") is False  # a non-https API base trusts nothing


async def test_fetch_collects_core_and_sub_endpoints() -> None:
    async with make_client([CORE_ROW]) as http:
        cols = await fetch_registry_columns(http, BASE, "農藥製00001")
    assert cols["approved_usages_json"] == [{"作物名稱": "木瓜"}]
    assert cols["barcode_json"] == [{"Barcode": "4711242092192"}]
    assert cols["mixture_ratio_json"] == [{"pestcd": "F060"}]
    assert cols["license_label_json"] == {"url": LABEL}  # HTML page: link only, never fetched


async def test_fetch_ignores_substring_matches_and_missing_permit() -> None:
    other = {**CORE_ROW, "許可證號": "100001"}  # `like` also returns this one
    async with make_client([other, CORE_ROW]) as http:
        cols = await fetch_registry_columns(http, BASE, "農藥製00001")
    assert cols["reg_no"] == "農藥製00001"

    async with make_client([]) as http:
        with pytest.raises(MoaNotFoundError):
            await fetch_registry_columns(http, BASE, "農藥製99999")


async def test_null_sub_url_is_none_and_failed_sub_keeps_cached_value() -> None:
    row = {**CORE_ROW, "混合劑百分率": None, "許可證標示": None}
    cached = SimpleNamespace(approved_usages_json=[{"舊": "資料"}], barcode_json=None, mixture_ratio_json=[{"x": 1}])
    async with make_client([row], failing=(DETAIL,)) as http:
        cols = await fetch_registry_columns(http, BASE, "農藥製00001", cached)  # type: ignore[arg-type]
    assert cols["mixture_ratio_json"] is None  # MOA now says none -> not the stale cache
    assert cols["license_label_json"] is None
    assert cols["approved_usages_json"] == [{"舊": "資料"}]  # request failed -> keep cache
    assert cols["barcode_json"] == SUBS[BARCODE]


async def test_malformed_sub_payloads_and_urls_never_fail_the_lookup() -> None:
    cached = SimpleNamespace(approved_usages_json=[{"舊": 1}], barcode_json=[{"舊": 2}], mixture_ratio_json=[{"舊": 3}])
    row = {**CORE_ROW, "農藥條碼資料": "https://[", "混合劑百分率": ["not", "a", "url"]}
    subs = {DETAIL: {"error": "boom"}}  # a JSON object instead of a list
    async with make_client([row], subs=subs) as http:
        cols = await fetch_registry_columns(http, BASE, "農藥製00001", cached)  # type: ignore[arg-type]
    assert cols["approved_usages_json"] == [{"舊": 1}]
    assert cols["barcode_json"] == [{"舊": 2}]
    assert cols["mixture_ratio_json"] == [{"舊": 3}]


async def test_untrusted_sub_url_is_never_requested() -> None:
    evil = "https://evil.example.com/steal"
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url).startswith(BASE):
            return httpx.Response(200, json=[{**CORE_ROW, "農藥使用範圍": evil, "許可證標示": evil}])
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        cols = await fetch_registry_columns(http, BASE, "農藥製00001")
    assert evil not in requested
    assert cols["approved_usages_json"] is None or cols["approved_usages_json"] == []
    assert cols["license_label_json"] is None


async def test_redirects_are_not_followed_even_if_the_client_would() -> None:
    evil = "https://evil.example.com/steal"
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url.startswith(BASE):
            return httpx.Response(200, json=[CORE_ROW])
        if url == DETAIL:
            return httpx.Response(302, headers={"Location": evil})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as http:
        cols = await fetch_registry_columns(http, BASE, "農藥製00001")
    assert evil not in requested
    assert cols["approved_usages_json"] is None  # redirect = failure, nothing cached


class FakeSession:
    """Stands in for AsyncSession; records what get_registry_snapshot does to it."""

    def __init__(self, cached=None, fail_upsert: bool = False):
        self.cached, self.fail_upsert = cached, fail_upsert
        self.statements: list = []
        self.committed = self.rolled_back = False

    async def get(self, model, key):
        return self.cached

    async def scalars(self, stmt, execution_options=None):
        if self.fail_upsert:
            raise RuntimeError("db boom")
        self.statements.append(stmt)
        return SimpleNamespace(one=lambda: "SNAPSHOT")

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def bad_clients():
    def respond(make):
        return httpx.AsyncClient(transport=httpx.MockTransport(make))

    def connect_error(request):
        raise httpx.ConnectError("down")

    def timeout(request):
        raise httpx.ReadTimeout("slow")

    return {
        "503": respond(lambda r: httpx.Response(503)),
        "connect error": respond(connect_error),
        "timeout": respond(timeout),
        "not json": respond(lambda r: httpx.Response(200, text="<html>maintenance</html>")),
        "json object": respond(lambda r: httpx.Response(200, json={"error": "x"})),
        "list of strings": respond(lambda r: httpx.Response(200, json=["a", "b"])),
        "truncated at cap": respond(lambda r: httpx.Response(200, json=[{"x": i} for i in range(MAX_ROWS)])),
    }


@pytest.mark.parametrize("case", list(bad_clients()))
async def test_unreachable_or_garbage_moa_serves_cache_else_raises(case: str) -> None:
    cached = MoaRegistry(reg_no="農藥製00001", fetched_at=datetime.now(timezone.utc))
    async with bad_clients()[case] as http:
        result = await get_registry_snapshot(FakeSession(cached), http, BASE, "農藥製00001")  # type: ignore[arg-type]
        assert result.snapshot is cached and result.stale is True
        with pytest.raises(MoaUnavailableError):
            await get_registry_snapshot(FakeSession(None), http, BASE, "農藥製00001")  # type: ignore[arg-type]


async def test_not_found_is_not_masked_by_cache() -> None:
    cached = MoaRegistry(reg_no="農藥製99999", fetched_at=datetime.now(timezone.utc))
    async with make_client([]) as http:
        with pytest.raises(MoaNotFoundError):
            await get_registry_snapshot(FakeSession(cached), http, BASE, "農藥製99999")  # type: ignore[arg-type]


async def test_fresh_fetch_upserts_commits_and_is_not_stale() -> None:
    session = FakeSession()
    async with make_client([CORE_ROW]) as http:
        result = await get_registry_snapshot(session, http, BASE, "農藥製00001")  # type: ignore[arg-type]
    assert result.snapshot == "SNAPSHOT" and result.stale is False and session.committed
    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))  # the production statement
    assert "ON CONFLICT (reg_no) DO UPDATE" in sql
    updated = sql.split("DO UPDATE SET")[1]
    assert "fetched_at" in updated and "reg_no =" not in updated


async def test_db_failure_rolls_back_and_reraises() -> None:
    session = FakeSession(fail_upsert=True)
    async with make_client([CORE_ROW]) as http:
        with pytest.raises(RuntimeError, match="db boom"):
            await get_registry_snapshot(session, http, BASE, "農藥製00001")  # type: ignore[arg-type]
    assert session.rolled_back and not session.committed


def test_build_upsert_never_updates_the_key() -> None:
    sql = str(build_upsert(core_to_columns(CORE_ROW)).compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (reg_no) DO UPDATE" in sql and "RETURNING" in sql


@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="needs a real PostgreSQL (TEST_DATABASE_URL)")
async def test_upsert_against_real_postgres() -> None:
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = os.environ["TEST_DATABASE_URL"]
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("TEST_DATABASE_URL database name must end with '_test': this test DROPS all tables")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with make_client([CORE_ROW]) as http:
            async with factory() as session:
                first = await get_registry_snapshot(session, http, BASE, "農藥製00001")
                stamp = first.snapshot.fetched_at
            async with factory() as session:
                second = await get_registry_snapshot(session, http, BASE, "農藥製00001")  # conflict path
        assert second.snapshot.reg_no == "農藥製00001" and second.snapshot.company_name == "好速化學股份有限公司"
        assert second.snapshot.fetched_at > stamp and not second.stale
        assert second.snapshot.approved_usages_json == [{"作物名稱": "木瓜"}]
    finally:
        await engine.dispose()
