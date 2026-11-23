"""Unit tests for APHIA HTML parsing, URL/token safety, 6h cache, and registry route."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import AphiaQueryCache
from app.services.aphia_client import (
    _matches_token,
    extract_ingredient_tokens,
    lookup_aphia_for_tokens,
    parse_news_records,
    parse_table_records,
    validate_aphia_url,
)


def test_validate_aphia_url_guards_origin_and_schemes() -> None:
    assert (
        validate_aphia_url("/information/Data/NewsContent/3453")
        == "https://pesticide.aphia.gov.tw/information/Data/NewsContent/3453"
    )
    assert validate_aphia_url("javascript:alert(1)") is None
    assert validate_aphia_url("data:text/html,hi") is None
    assert validate_aphia_url("https://pesticide.aphia.gov.tw.evil.com/x") is None
    assert validate_aphia_url("https://user@pesticide.aphia.gov.tw/x") is None
    assert validate_aphia_url("http://pesticide.aphia.gov.tw/x") is None
    assert validate_aphia_url("//evil.com/x") is None


def test_extract_ingredient_tokens_and_boundary_match() -> None:
    tokens = extract_ingredient_tokens("95% 嘉磷塞 SC + 2,4-D", None, "A")
    assert "嘉磷塞" in tokens
    assert "2,4-D" in tokens
    assert "A" not in tokens

    assert _matches_token("2,4-D 禁用公告", "2,4-D") is True
    assert _matches_token("2,4-DB 評估", "2,4-D") is False
    assert _matches_token("嘉磷塞原體", "嘉磷塞") is True


def test_parse_table_and_news_html() -> None:
    table_html = """
    <table>
      <thead><tr><th>普通名稱</th><th>公告日期</th><th>說明</th></tr></thead>
      <tbody>
        <tr><td><a href="/information/Data/NewsContent/101">巴拉刈</a></td><td>2020-02-01</td><td>全面禁用</td></tr>
      </tbody>
    </table>
    """
    recs = parse_table_records(table_html, "forbid_banned")
    assert len(recs) == 1
    assert recs[0]["common_name"] == "巴拉刈"
    assert recs[0]["published_date"] == "2020-02-01"
    assert recs[0]["detail_url"] == "https://pesticide.aphia.gov.tw/information/Data/NewsContent/101"

    news_html = """
    <ul class="news-list">
      <li><a href="/information/Data/NewsContent/3453"><span class="news-date">2026-11-11</span><span class="news-name-long">預先通知公告</span></a></li>
    </ul>
    """
    news = parse_news_records(news_html)
    assert len(news) == 1
    assert news[0]["source_kind"] == "announcement"
    assert news[0]["source_key"] == "3453"
    assert news[0]["published_date"] == "2026-11-11"


@pytest.mark.asyncio
async def test_lookup_aphia_uses_6h_cache_even_for_zero_matches() -> None:
    fresh = AphiaQueryCache(
        query_key="k",
        keywords_json=["嘉磷塞"],
        result_json={"forbid_matches": [], "assessment_matches": [], "announcements": []},
        fetched_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: fresh)
    )
    http = MagicMock()
    http.get = AsyncMock()

    res = await lookup_aphia_for_tokens(session, http, ["嘉磷塞"])
    assert res["stale"] is False
    assert res["forbid_matches"] == []
    http.get.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_aphia_falls_back_to_stale_cache_on_upstream_error() -> None:
    old = AphiaQueryCache(
        query_key="k",
        keywords_json=["巴拉刈"],
        result_json={
            "forbid_matches": [{"source_kind": "forbid_banned", "source_key": "1", "title": "巴拉刈"}],
            "assessment_matches": [],
            "announcements": [],
        },
        fetched_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: old)
    )
    http = MagicMock()
    http.get = AsyncMock(side_effect=RuntimeError("timeout"))

    res = await lookup_aphia_for_tokens(session, http, ["巴拉刈"])
    assert res["stale"] is True
    assert len(res["forbid_matches"]) == 1
