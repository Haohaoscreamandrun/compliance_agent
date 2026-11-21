 # cSpell:words httpx APHIA MSDSs
"""Unit tests for CL 4.2: CopilotToolset (4 tools), ComplianceAuditReport, and Two-Phase Copilot."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.db.models import Document, MoaRegistry
from app.schemas.extraction import (
    AuthorizedProductItem,
    MsdsExtraction,
    NotarizedCertExtraction,
    OfficialLetterExtraction,
    ProductLabelExtraction,
)
from app.schemas.grounded import GroundedField
from app.schemas.report import ComplianceAuditReport
from app.services.copilot import run_compliance_audit
from app.services.tools import CopilotToolset


def _gf(verbatim: str, value: str | None = None, page: int = 1) -> GroundedField[str]:
    return GroundedField[str](
        verbatim_text=verbatim,
        value=value if value is not None else verbatim,
        page=page,
        box_2d=[100, 100, 180, 500],
    )


def _mock_session_with_docs(docs: list[Document]) -> AsyncMock:
    session = AsyncMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = docs
    exec_result = MagicMock()
    exec_result.scalars.return_value = scalars_mock
    session.execute.return_value = exec_result
    return session


@pytest.mark.asyncio
async def test_toolset_selects_latest_msds_and_runs_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    label_doc = Document(
        doc_id="lbl_1",
        filename="label.webp",
        doc_type="label",
        reg_no="農藥進03225",
        is_active_baseline=True,
        page_count=1,
        extracted_data=ProductLabelExtraction(
            reg_no=_gf("農藥進03225"),
            common_name=_gf("美氟綜"),
            foreign_manufacturer_name=_gf("Old Factory"),
            ghs_pictograms=[_gf("GHS07", "GHS07")],
        ).model_dump(),
        uploaded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    msds_old = Document(
        doc_id="msds_v3",
        filename="msds_v3.pdf",
        doc_type="msds",
        reg_no="農藥進03225",
        is_active_baseline=False,
        page_count=10,
        extracted_data=MsdsExtraction(
            common_name=_gf("Metaflumizone"),
            issued_version=_gf("Version 3.0", "3.0"),
            sec2_ghs_pictograms=[_gf("GHS07", "GHS07")],
        ).model_dump(),
        uploaded_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    msds_new = Document(
        doc_id="msds_v10",
        filename="msds_v10.pdf",
        doc_type="msds",
        reg_no="農藥進03225",
        is_active_baseline=False,
        page_count=10,
        extracted_data=MsdsExtraction(
            common_name=_gf("Metaflumizone"),
            issued_version=_gf("Version 10.0", "10.0"),
            sec2_ghs_pictograms=[_gf("GHS07", "GHS07"), _gf("GHS09", "GHS09")],
        ).model_dump(),
        uploaded_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
    )
    cert_doc = Document(
        doc_id="cert_1",
        filename="loa.pdf",
        doc_type="change_cert",
        reg_no="農藥進03225",
        is_active_baseline=False,
        page_count=2,
        extracted_data=NotarizedCertExtraction(
            certified_factory_name=_gf("New Factory Ltd."),
            products=[AuthorizedProductItem(common_name=_gf("Metaflumizone"))],
        ).model_dump(),
        uploaded_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    letter_doc = Document(
        doc_id="let_1",
        filename="letter.pdf",
        doc_type="official_letter",
        reg_no="農藥進03225",
        is_active_baseline=False,
        page_count=1,
        extracted_data=OfficialLetterExtraction(
            issue_number=_gf("防檢二字第1150001號"),
            affected_reg_nos=[_gf("農藥進字03225", "農藥進03225")],
            mandate_clauses=[_gf("加註警語")],
        ).model_dump(),
        uploaded_at=datetime(2026, 3, 2, tzinfo=timezone.utc),
    )

    moa_row = MoaRegistry(
        reg_no="農藥進03225",
        permit_type="農藥進",
        permit_number="03225",
        chinese_name="美氟綜",
        english_name="METAFLUMIZONE",
        foreign_manufacturer="Old Factory",
        raw_core_json={},
        fetched_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    async def _fake_get_snapshot(session, http, base_url, reg_no):
        return SimpleNamespace(snapshot=moa_row, stale=False)

    monkeypatch.setattr("app.services.tools.get_registry_snapshot", _fake_get_snapshot)

    session = _mock_session_with_docs([label_doc, msds_old, msds_new, cert_doc])
    async with httpx.AsyncClient() as http:
        toolset = CopilotToolset(
            session=session,
            http=http,
            moa_api_base_url="https://data.moa.gov.tw/Service/OpenData/FromM/PesticideData.aspx",
        )
        moa_res = await toolset.lookup_moa_registry("農藥進字03225")
        assert moa_res["found"] is True

        base_res = await toolset.get_baseline_documents("農藥進03225")
        assert base_res["msds_doc_id"] == "msds_v10"
        assert base_res["skipped_msds_doc_ids"] == ["msds_v3"]

        # Switch session return for official_letter query
        session.execute.return_value.scalars.return_value.all.return_value = [letter_doc]
        let_res = await toolset.search_official_letters("農藥進03225", "美氟綜")
        assert let_res["candidate_letter_doc_ids"] == ["let_1"]

        diff_res = await toolset.run_compliance_diff("農藥進03225")
        assert diff_res["finding_count"] >= 3
        fields = {f["field_name"] for f in diff_res["findings"]}
        assert "GHS 危害圖式 (GHS01–GHS09)" in fields
        assert "國外原製造廠名稱" in fields


@pytest.mark.asyncio
async def test_two_phase_copilot_executes_tools_and_enforces_grounded_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label_doc = Document(
        doc_id="lbl_1",
        filename="label.webp",
        doc_type="label",
        reg_no="農藥進03225",
        is_active_baseline=True,
        page_count=1,
        extracted_data=ProductLabelExtraction(
            reg_no=_gf("農藥進03225"),
            common_name=_gf("美氟綜"),
            ghs_pictograms=[_gf("GHS07", "GHS07")],
        ).model_dump(),
        uploaded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    msds_doc = Document(
        doc_id="msds_1",
        filename="msds.pdf",
        doc_type="msds",
        reg_no="農藥進03225",
        is_active_baseline=False,
        page_count=5,
        extracted_data=MsdsExtraction(
            common_name=_gf("Metaflumizone"),
            issued_version=_gf("Version 4.0", "4.0"),
            sec2_ghs_pictograms=[_gf("GHS07", "GHS07"), _gf("GHS09", "GHS09")],
        ).model_dump(),
        uploaded_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    moa_row = MoaRegistry(
        reg_no="農藥進03225",
        permit_type="農藥進",
        permit_number="03225",
        chinese_name="美氟綜",
        english_name="METAFLUMIZONE",
        raw_core_json={},
        fetched_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    async def _fake_get_snapshot(session, http, base_url, reg_no):
        return SimpleNamespace(snapshot=moa_row, stale=True)

    monkeypatch.setattr("app.services.tools.get_registry_snapshot", _fake_get_snapshot)

    session = _mock_session_with_docs([label_doc, msds_doc])

    # Simulate Phase 1 (1 tool-call turn -> end) + Phase 2 (LLM returns tampered/empty citations, safeguard restores exact ones)
    phase1_call = SimpleNamespace(
        function_calls=[
            SimpleNamespace(name="lookup_moa_registry", args={"reg_no": "農藥進03225"}),
            SimpleNamespace(name="get_baseline_documents", args={"reg_no": "農藥進03225"}),
        ],
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
    )
    phase1_done = SimpleNamespace(function_calls=[], candidates=[])
    phase2_json = ComplianceAuditReport(
        reg_no="農藥進03225",
        overall_status="compliant",  # Deliberately wrong; safeguard must override to action_required
        executive_summary_zh="標籤缺少 GHS09 圖式，需更新。",
        executive_summary_en="Label is missing GHS09 pictogram and must be updated.",
        checklist_items=[],  # Deliberately empty; safeguard must restore deterministic findings
    ).model_dump_json()
    phase2_done = SimpleNamespace(text=phase2_json)

    responses = iter([(phase1_call, "gemini-3.8-flash"), (phase1_done, "gemini-3.8-flash"), (phase2_done, "gemini-3.8-flash")])

    async def _fake_generate(client, models, contents, config):
        return next(responses)

    monkeypatch.setattr("app.services.copilot._generate_with_5xx_fallback", _fake_generate)

    async with httpx.AsyncClient() as http:
        toolset = CopilotToolset(
            session=session,
            http=http,
            moa_api_base_url="https://data.moa.gov.tw/Service/OpenData/FromM/PesticideData.aspx",
        )
        # Second execute call (search_official_letters) returns empty list
        session.execute.side_effect = [
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[label_doc, msds_doc])))),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        ]

        report = await run_compliance_audit(
            client=MagicMock(),
            toolset=toolset,
            reg_no="農藥進字第03225號",
        )

    assert report.reg_no == "農藥進03225"
    assert report.overall_status == "action_required"
    assert report.moa_snapshot_stale is True
    assert report.msds_doc_id_used == "msds_1"
    assert len(report.checklist_items) == 1
    assert report.checklist_items[0].current_label_citation is not None
    assert report.checklist_items[0].current_label_citation.doc_id == "lbl_1"
    assert report.checklist_items[0].supporting_citations[0].doc_id == "msds_1"


@pytest.mark.asyncio
async def test_toolset_moa_errors_version_case_and_sql_ordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.dialects import postgresql

    from app.services.moa_client import MoaNotFoundError, MoaUnavailableError

    session = _mock_session_with_docs([])

    async def _not_found(s, h, b, r):
        raise MoaNotFoundError("Permit not found")

    monkeypatch.setattr("app.services.tools.get_registry_snapshot", _not_found)
    async with httpx.AsyncClient() as http:
        toolset = CopilotToolset(session=session, http=http, moa_api_base_url="https://data.moa.gov.tw/x")
        res1 = await toolset.lookup_moa_registry("農藥進03225")
        assert res1["found"] is False and "unavailable" not in res1

        async def _unavail(s, h, b, r):
            raise MoaUnavailableError("MOA down")

        monkeypatch.setattr("app.services.tools.get_registry_snapshot", _unavail)
        res2 = await toolset.lookup_moa_registry("農藥進03225")
        assert res2["found"] is False and res2["unavailable"] is True

        # Case-insensitive V10.0 > v3.0
        msds_v3 = Document(
            doc_id="m_v3",
            filename="v3.pdf",
            doc_type="msds",
            reg_no="農藥進03225",
            is_active_baseline=False,
            page_count=1,
            extracted_data=MsdsExtraction(issued_version=_gf("v3.0")).model_dump(),
            uploaded_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        msds_v10 = Document(
            doc_id="m_v10",
            filename="v10.pdf",
            doc_type="msds",
            reg_no="農藥進03225",
            is_active_baseline=False,
            page_count=1,
            extracted_data=MsdsExtraction(issued_version=_gf("V10.0")).model_dump(),
            uploaded_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        )
        session.execute.return_value.scalars.return_value.all.return_value = [msds_v3, msds_v10]
        base = await toolset.get_baseline_documents("農藥進03225")
        assert base["msds_doc_id"] == "m_v10"
        assert base["skipped_msds_doc_ids"] == ["m_v3"]
        assert any("No baseline product label" in w for w in toolset.warnings)

        # Verify compiled SQL for search_official_letters prioritizes non-null reg_no before global letters
        session.execute.return_value.scalars.return_value.all.return_value = []
        await toolset.search_official_letters("農藥進03225", "美氟綜")
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "documents.reg_no IS NULL ASC" in compiled
        assert "LIMIT" in compiled


@pytest.mark.asyncio
async def test_copilot_out_of_order_tools_missing_label_and_tamper_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.copilot import MAX_PHASE1_TURNS
    from app.services.parser import GeminiParseError

    letter_doc = Document(
        doc_id="let_1",
        filename="let.pdf",
        doc_type="official_letter",
        reg_no="農藥進03225",
        is_active_baseline=False,
        page_count=1,
        extracted_data=OfficialLetterExtraction(
            issue_number=_gf("防檢二字第1150001號"),
            affected_reg_nos=[_gf("農藥進03225")],
            mandate_clauses=[_gf("加註警語")],
        ).model_dump(),
        uploaded_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    moa_row = MoaRegistry(
        reg_no="農藥進03225",
        permit_type="農藥進",
        permit_number="03225",
        chinese_name="美氟綜",
        english_name="METAFLUMIZONE",
        raw_core_json={},
        fetched_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    async def _fake_get_snapshot(s, h, b, r):
        return SimpleNamespace(snapshot=moa_row, stale=False)

    monkeypatch.setattr("app.services.tools.get_registry_snapshot", _fake_get_snapshot)

    session = _mock_session_with_docs([])
    session.execute.side_effect = [
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[letter_doc])))),
    ]

    # LLM calls run_compliance_diff FIRST with a mismatched reg_no (rejected), then runs out-of-order,
    # hits MAX_PHASE1_TURNS, and Phase 2 tries to tamper with product_name_zh and audit_warnings.
    bad_arg_call = SimpleNamespace(
        function_calls=[SimpleNamespace(name="run_compliance_diff", args={"reg_no": "農藥進99999"})],
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
    )
    early_diff_call = SimpleNamespace(
        function_calls=[SimpleNamespace(name="run_compliance_diff", args={"reg_no": "農藥進03225"})],
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
    )
    tampered_phase2 = SimpleNamespace(
        text=json.dumps(
            {
                "reg_no": "農藥進03225",
                "product_name_zh": "FakeName",
                "product_name_en": "FAKE",
                "overall_status": "compliant",
                "executive_summary_zh": "摘要",
                "executive_summary_en": "Summary",
                "checklist_items": [],
                "audit_warnings": ["Injected warning"],
            }
        ),
        candidates=[SimpleNamespace(finish_reason="STOP")],
    )
    turns = [bad_arg_call] + [early_diff_call] * (MAX_PHASE1_TURNS - 1) + [tampered_phase2]
    resp_iter = iter(turns)

    async def _fake_gen(client, models, contents, config):
        return (next(resp_iter), "gemini-3.8-flash")

    monkeypatch.setattr("app.services.copilot._generate_with_5xx_fallback", _fake_gen)

    async with httpx.AsyncClient() as http:
        toolset = CopilotToolset(session=session, http=http, moa_api_base_url="https://data.moa.gov.tw/x")
        report = await run_compliance_audit(client=MagicMock(), toolset=toolset, reg_no="農藥進03225")

    assert report.product_name_zh == "美氟綜"
    assert report.product_name_en == "METAFLUMIZONE"
    assert report.overall_status == "action_required"  # From backfilled official letter!
    assert "Injected warning" not in report.audit_warnings
    assert len(report.checklist_items) == 1

    # Also verify missing label + no findings -> "needs_verification" (never "compliant")
    from app.services.copilot import _derive_overall_status

    assert _derive_overall_status([], has_label=False, has_moa=True) == "needs_verification"
    assert _derive_overall_status([], has_label=True, has_moa=False) == "needs_verification"
    assert _derive_overall_status([], has_label=True, has_moa=True) == "compliant"

    # Verify blocked Phase 2 finish_reason raises GeminiParseError
    session2 = _mock_session_with_docs([])
    session2.execute.side_effect = [
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
    ]
    blocked_iter = iter([
        SimpleNamespace(function_calls=[], candidates=[]),
        SimpleNamespace(text="", candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")]),
    ])
    monkeypatch.setattr(
        "app.services.copilot._generate_with_5xx_fallback",
        AsyncMock(side_effect=lambda *a, **kw: (next(blocked_iter), "gemini-3.8-flash")),
    )
    async with httpx.AsyncClient() as http:
        toolset2 = CopilotToolset(session=session2, http=http, moa_api_base_url="https://data.moa.gov.tw/x")
        with pytest.raises(GeminiParseError, match="MAX_TOKENS"):
            await run_compliance_audit(client=MagicMock(), toolset=toolset2, reg_no="農藥進03225")


@pytest.mark.asyncio
async def test_shared_multi_product_loa_matching_and_nested_clause_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.parser import _filter_to_window_bounds, extract_clauses_for_db

    unrelated_loa = Document(
        doc_id="loa_other",
        filename="other.pdf",
        doc_type="change_cert",
        reg_no=None,
        is_active_baseline=False,
        page_count=2,
        extracted_data=NotarizedCertExtraction(
            products=[AuthorizedProductItem(common_name=_gf("Azoxystrobin", page=2))]
        ).model_dump(),
        uploaded_at=datetime(2026, 3, 2, tzinfo=timezone.utc),
    )
    shared_loa_model = NotarizedCertExtraction(
        cert_subtype=_gf("Letter of Authorization", "loa", page=2),
        products=[
            AuthorizedProductItem(
                brand_or_product_name=_gf("KUMULUS 80 DF", page=2),
                common_name=_gf("Sulfur", page=2),
                dosage_and_content=_gf("80%", page=2),
                formulation_type=_gf("WG", page=2),
            ),
            AuthorizedProductItem(
                brand_or_product_name=_gf("CABRIO TOP", page=2),
                common_name=_gf("Metiram + Pyraclostrobin", page=2),
            ),
        ],
    )
    shared_loa = Document(
        doc_id="loa_basf_7",
        filename="basf_loa.pdf",
        doc_type="change_cert",
        reg_no=None,
        is_active_baseline=False,
        page_count=3,
        extracted_data=shared_loa_model.model_dump(),
        uploaded_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    moa_sulfur = MoaRegistry(
        reg_no="農藥進00569",
        permit_type="農藥進",
        permit_number="00569",
        chinese_name="硫黃",
        english_name="SULFUR",
        formulation="WG",
        content_pct="80%",
        raw_core_json={},
        fetched_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    async def _fake_snap(s, h, b, r):
        return SimpleNamespace(snapshot=moa_sulfur, stale=False)

    monkeypatch.setattr("app.services.tools.get_registry_snapshot", _fake_snap)

    session = _mock_session_with_docs([unrelated_loa, shared_loa])
    async with httpx.AsyncClient() as http:
        toolset = CopilotToolset(session=session, http=http, moa_api_base_url="https://data.moa.gov.tw/x")
        await toolset.lookup_moa_registry("農藥進00569")
        base = await toolset.get_baseline_documents("農藥進00569")
        assert base["cert_doc_id"] == "loa_basf_7"

    # Verify nested AuthorizedProductItem window bounds filtering + DocumentClause extraction
    bounded = _filter_to_window_bounds(shared_loa_model, NotarizedCertExtraction, valid_pages={2})
    assert len(bounded.products) == 2
    assert _filter_to_window_bounds(shared_loa_model, NotarizedCertExtraction, valid_pages={1}).products == []

    clauses = extract_clauses_for_db(
        doc_id="loa_basf_7",
        doc_type="change_cert",
        reg_no=None,
        common_name=None,
        extraction=shared_loa_model,
    )
    section_ids = {c["section_id"] for c in clauses}
    assert "products_1_common_name" in section_ids
    assert "products_2_common_name" in section_ids

    # Verify merge_window_extractions preserves products[] across windows
    from app.services.parser import merge_window_extractions

    w1 = NotarizedCertExtraction(products=[shared_loa_model.products[0]])
    w2 = NotarizedCertExtraction(products=[shared_loa_model.products[1]])
    merged_cert = merge_window_extractions(NotarizedCertExtraction, [w1, w2])
    assert isinstance(merged_cert, NotarizedCertExtraction) and len(merged_cert.products) == 2

    # Verify newer shared (reg_no=None) cert beats older explicit reg_no cert, and MoC + LoA combine while LoO is skipped
    older_explicit_loa = Document(
        doc_id="cert_explicit_old",
        filename="explicit_old.pdf",
        doc_type="change_cert",
        reg_no="農藥進00569",
        is_active_baseline=False,
        page_count=1,
        extracted_data=shared_loa_model.model_dump(),
        uploaded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    moc_doc = Document(
        doc_id="moc_new",
        filename="moc.pdf",
        doc_type="change_cert",
        reg_no=None,
        is_active_baseline=False,
        page_count=1,
        extracted_data=NotarizedCertExtraction(
            cert_subtype=_gf("MoC", "moc"),
            products=[AuthorizedProductItem(common_name=_gf("Sulfur"))],
        ).model_dump(),
        uploaded_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    loo_doc = Document(
        doc_id="loo_new",
        filename="loo.pdf",
        doc_type="change_cert",
        reg_no=None,
        is_active_baseline=False,
        page_count=1,
        extracted_data=NotarizedCertExtraction(
            cert_subtype=_gf("LoO", "loo"),
            products=[AuthorizedProductItem(common_name=_gf("Sulfur"))],
        ).model_dump(),
        uploaded_at=datetime(2026, 4, 2, tzinfo=timezone.utc),
    )
    session2 = _mock_session_with_docs([loo_doc, moc_doc, shared_loa, older_explicit_loa])
    async with httpx.AsyncClient() as http:
        toolset2 = CopilotToolset(session=session2, http=http, moa_api_base_url="https://data.moa.gov.tw/x")
        await toolset2.lookup_moa_registry("農藥進00569")
        base2 = await toolset2.get_baseline_documents("農藥進00569")
        # Latest upload date (shared_loa 2026-03-01) beats older explicit (2026-01-01); moc_new is also kept, loo skipped
        assert base2["cert_doc_id"] == "moc_new"
        assert toolset2._extra_certs[0][0] == "loa_basf_7"
        assert all(cid != "loo_new" for cid, _ in [toolset2._cert, *toolset2._extra_certs])
