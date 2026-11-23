"""Unit tests for APHIA assessment/carcinogen/pre-notice parsing, URL/token safety, and 6h cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import AphiaQueryCache
from app.services.aphia_client import (
    _matches_token,
    extract_ingredient_tokens,
    lookup_aphia_for_tokens,
    parse_assessment_records,
    parse_pre_notice_records,
    validate_aphia_url,
)


def test_validate_aphia_url_guards_origin_and_encodes_paths() -> None:
    assert (
        validate_aphia_url("/information/Data/NewsContent/3453")
        == "https://pesticide.aphia.gov.tw/information/Data/NewsContent/3453"
    )
    encoded = validate_aphia_url("/information/file/opendata/可能致癌.pdf")
    assert encoded is not None and "%E5%8F%AF%E8%83%BD" in encoded
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

    assert _matches_token("2,4-D 評估", "2,4-D") is True
    assert _matches_token("2,4-DB 評估", "2,4-D") is False
    assert _matches_token("嘉磷塞原體", "嘉磷塞") is True


def test_parse_assessment_and_pre_notice_html() -> None:
    assess_html = """
    <table>
      <thead><tr><th>檔案名稱</th><th>檔案大小</th><th>發佈日期</th></tr></thead>
      <tbody>
        <tr>
          <td><a href="/information/file/opendata/派滅芬.pdf">派滅芬(pydiflumetofen)112年登記新有效成分（PDF）</a></td>
          <td>488 KB</td>
          <td>2023-01-05</td>
        </tr>
      </tbody>
    </table>
    """
    recs = parse_assessment_records(assess_html)
    assert len(recs) == 1
    assert recs[0]["source_kind"] == "assessment"
    assert recs[0]["published_date"] == "2023-01-05"
    assert recs[0]["detail_url"].startswith("https://pesticide.aphia.gov.tw/information/file/opendata/")

    news_html = """
    <ul class="news-list">
      <li><a href="/information/Data/NewsContent/3437"><span class="news-date">2026-11-20</span><span class="news-name-long">代噴人員登錄名冊</span></a></li>
      <li><a href="/information/Data/NewsContent/3453"><span class="news-date">2026-11-11</span><span class="news-name-long">預先通知公告「可濕性硫黃」之農藥使用方法</span></a></li>
    </ul>
    """
    news = parse_pre_notice_records(news_html)
    assert len(news) == 1
    assert news[0]["source_kind"] == "announcement"
    assert news[0]["source_key"] == "3453"
    assert news[0]["published_date"] == "2026-11-11"


@pytest.mark.asyncio
async def test_lookup_aphia_uses_6h_cache_even_for_zero_matches() -> None:
    fresh = AphiaQueryCache(
        query_key="k",
        keywords_json=["嘉磷塞"],
        result_json={"carcinogen_matches": [], "assessment_matches": [], "latest_pre_notice": None},
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
    assert res["carcinogen_matches"] == []
    assert res["assessment_matches"] == []
    assert res["latest_pre_notice"] is None
    http.get.assert_not_called()


@pytest.mark.asyncio
async def test_lookup_aphia_falls_back_to_stale_cache_on_upstream_error() -> None:
    old = AphiaQueryCache(
        query_key="k",
        keywords_json=["依普座"],
        result_json={
            "carcinogen_matches": [{"source_kind": "carcinogen", "source_key": "1", "title": "依普座 (Epoxiconazole) [B級]"}],
            "assessment_matches": [],
            "latest_pre_notice": None,
        },
        fetched_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: old),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
        ]
    )
    http = MagicMock()
    http.get = AsyncMock(side_effect=RuntimeError("timeout"))

    res = await lookup_aphia_for_tokens(session, http, ["依普座"])
    assert res["stale"] is True
    assert len(res["carcinogen_matches"]) == 1


def test_label_crop_usage_schema_and_evidence_bundle() -> None:
    from app.db.models import MoaRegistry
    from app.schemas.extraction import CropUsageRow, ProductLabelExtraction
    from app.schemas.grounded import GroundedField
    from app.services.tools import CopilotToolset

    gf = lambda v: GroundedField[str](verbatim_text=v, value=v, page=1, box_2d=[10, 10, 50, 50])
    label = ProductLabelExtraction(
        reg_no=gf("農藥進00569"),
        crop_usage_table=[
            CropUsageRow(crop_name=gf("果樹"), pest_name=gf("白粉病"), dilution_ratio=gf("1000"))
        ],
    )
    moa = MoaRegistry(
        reg_no="農藥進00569",
        permit_type="農藥進",
        permit_number="00569",
        raw_core_json={},
        approved_usages_json=[{"作物名稱": "果樹", "病蟲害名稱": "白粉病", "稀釋倍數": "1200"}],
    )
    toolset = CopilotToolset(session=MagicMock(), http=MagicMock(), moa_api_base_url="https://example.com")
    toolset._moa = moa
    toolset._label = ("lbl_1", label)

    evidence = toolset.build_crop_usage_evidence()
    assert evidence["valid_label_doc_ids"] == ["lbl_1"]
    assert len(evidence["label_crop_usages"]) == 1
    assert len(evidence["moa_approved_usages"]) == 1
