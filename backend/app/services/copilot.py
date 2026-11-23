# cSpell:words genai APHIA MSDSs
"""Two-Phase Compliance Copilot (ADR-001 Decision 1): Phase 1 tool loop -> Phase 2 JSON report."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any

from google import genai
from google.genai import types

from app.schemas.report import (
    CitationRef,
    ComplianceAuditReport,
    ComplianceChecklistItem,
    OverallComplianceStatus,
)
from app.services.cascade_diff import CascadeDiffFinding, normalize_reg_no
from app.services.moa_client import split_reg_no
from app.services.parser import _generate_with_5xx_fallback, extract_response_json
from app.services.tools import CopilotToolset

log = logging.getLogger(__name__)

MAX_PHASE1_TURNS = 6

_COPILOT_SYSTEM_PROMPT = """You are a senior Taiwan Agrochemical Regulatory Affairs (RA) Compliance Copilot.
Phase 1: Use the 4 tools in order for the target permit `reg_no`:
1. `lookup_moa_registry(reg_no)`
2. `get_baseline_documents(reg_no)`
3. `search_official_letters(reg_no, common_name)`
4. `run_compliance_diff(reg_no)`
Do not guess any regulatory facts before running all relevant tools."""

_PHASE2_SYSTEM_PROMPT = """You are synthesizing a Taiwan Agrochemical Label Compliance Audit Report.
Produce `ComplianceAuditReport` JSON grounded strictly in the provided deterministic diff findings and tool outputs.
Write concise, accurate `executive_summary_zh` (Traditional Chinese) and `executive_summary_en` (English).
Preserve every deterministic `field_name`, `category`, `action_status`, and `[doc_id, page, box_2d]` citation verbatim.
Additionally, if `crop_usage_comparison.moa_approved_usages` is non-empty and `crop_usage_comparison.label_crop_usages` is provided, compare the label's crop-usage table against MOA `approved_usages_json` and emit `checklist_items` with `category="crop_usage_sync"` for:
1. Wrong numbers (`action_status="must_update"`): mismatched dilution ratio (`稀釋倍數`), dosage per ha (`每公頃使用用藥量`), interval (`施藥間隔`), application count (`施用次數`), or PHI (`安全採收期`). Ground `current_label_citation` to that printed label cell's `{doc_id, page, box_2d, verbatim_quote}`.
2. Different wording (`action_status="verify_warning"`): differing crop/pest/timing/notes wording (`作物名稱`, `病蟲害名稱`, `使用時期`, `注意事項`/`備註`).
3. Allowed but not displayed (`action_status="verify_warning"`, `current_label_citation: null`): list MOA-allowed `(作物名稱, 病蟲害名稱)` entries absent from the label so the user knows which allowed crops are omitted due to label layout space."""


def _tool_declarations() -> list[types.FunctionDeclaration]:
    reg_prop = {"reg_no": types.Schema(type=types.Type.STRING, description="Taiwan permit number e.g. 農藥進03225")}
    return [
        types.FunctionDeclaration(
            name="lookup_moa_registry",
            description="Fetch live Taiwan MOA EIR027 registry snapshot on-call.",
            parameters=types.Schema(type=types.Type.OBJECT, properties=reg_prop, required=["reg_no"]),
        ),
        types.FunctionDeclaration(
            name="get_baseline_documents",
            description="Load uploaded label, latest MSDS (by issued_version), and change certificate.",
            parameters=types.Schema(type=types.Type.OBJECT, properties=reg_prop, required=["reg_no"]),
        ),
        types.FunctionDeclaration(
            name="search_official_letters",
            description="Find uploaded official letters (公文) matching permit number or common name.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    **reg_prop,
                    "common_name": types.Schema(type=types.Type.STRING, description="Optional Chinese common name"),
                },
                required=["reg_no"],
            ),
        ),
        types.FunctionDeclaration(
            name="run_compliance_diff",
            description="Run deterministic cascade diff across all loaded sources.",
            parameters=types.Schema(type=types.Type.OBJECT, properties=reg_prop, required=["reg_no"]),
        ),
    ]


def _derive_overall_status(
    findings: list[CascadeDiffFinding],
    *,
    has_label: bool,
    has_moa: bool,
) -> OverallComplianceStatus:
    statuses = {f.action_status for f in findings}
    if "pending_moa_approval" in statuses:
        return "pending_moa_approval"
    if "must_update" in statuses:
        return "action_required"
    if "verify_warning" in statuses or not has_label or not has_moa:
        return "needs_verification"
    return "compliant"


def _to_checklist_items(findings: list[CascadeDiffFinding]) -> list[ComplianceChecklistItem]:
    items: list[ComplianceChecklistItem] = []
    for f in findings:
        items.append(
            ComplianceChecklistItem(
                field_name=f.field_name,
                category=f.category,
                action_status=f.action_status,
                current_label_value=f.current_label_value,
                recommended_new_value=f.recommended_new_value,
                rationale=f.rationale,
                current_label_citation=(
                    CitationRef(**asdict(f.current_label_citation)) if f.current_label_citation else None
                ),
                supporting_citations=[CitationRef(**asdict(c)) for c in f.supporting_citations],
            )
        )
    return items


async def _dispatch_tool(
    toolset: CopilotToolset, name: str, args: dict[str, Any], canon_reg: str
) -> dict[str, Any]:
    raw_reg = args.get("reg_no")
    target_reg = normalize_reg_no(str(raw_reg)) if raw_reg is not None else canon_reg
    if target_reg != canon_reg:
        return {"error": f"Tool reg_no {target_reg!r} does not match target permit {canon_reg!r}."}

    if name == "lookup_moa_registry":
        return await toolset.lookup_moa_registry(canon_reg)
    if name == "get_baseline_documents":
        return await toolset.get_baseline_documents(canon_reg)
    if name == "search_official_letters":
        cn = args.get("common_name")
        return await toolset.search_official_letters(
            canon_reg, str(cn) if cn is not None else None
        )
    if name == "run_compliance_diff":
        return await toolset.run_compliance_diff(canon_reg)
    return {"error": f"Unknown tool: {name}"}


async def run_compliance_audit(
    *,
    client: genai.Client,
    toolset: CopilotToolset,
    reg_no: str,
    model_name: str = "gemini-3.8-flash",
    fallback_models: list[str] | None = None,
) -> ComplianceAuditReport:
    """Execute Phase 1 tool-calling loop followed by Phase 2 Constrained Decoding."""
    canon_reg = normalize_reg_no(reg_no)
    split_reg_no(canon_reg)  # Validate permit format upfront before calling Gemini
    candidate_models = list(dict.fromkeys([model_name, *(fallback_models or [])]))

    # --- Phase 1: Tool-Calling Evidence Gathering ---
    phase1_config = types.GenerateContentConfig(
        system_instruction=_COPILOT_SYSTEM_PROMPT,
        temperature=0.0,
        tools=[types.Tool(function_declarations=_tool_declarations())],
    )
    contents: list[Any] = [
        f"Run a full compliance audit for Taiwan pesticide permit '{canon_reg}'."
    ]

    called_tools: set[str] = set()
    completed_cleanly = False
    for _ in range(MAX_PHASE1_TURNS):
        resp, _ = await _generate_with_5xx_fallback(client, candidate_models, contents, phase1_config)
        fcalls = getattr(resp, "function_calls", None) or []
        if not fcalls:
            completed_cleanly = True
            break

        if getattr(resp, "candidates", None):
            model_content = resp.candidates[0].content
            if model_content is not None:
                contents.append(model_content)

        fn_parts: list[types.Part] = []
        for call in fcalls:
            fn_name = getattr(call, "name", None) or ""
            result = await _dispatch_tool(toolset, fn_name, dict(call.args or {}), canon_reg)
            if "error" not in result:
                called_tools.add(fn_name)
            fn_parts.append(types.Part.from_function_response(name=fn_name, response=result))
        contents.append(types.Content(role="user", parts=fn_parts))

    if not completed_cleanly:
        log.warning("Phase 1 tool loop reached MAX_PHASE1_TURNS=%d for %s", MAX_PHASE1_TURNS, canon_reg)

    # Guarantee all 4 deterministic steps ran and run_compliance_diff reflects complete state
    if "lookup_moa_registry" not in called_tools:
        await toolset.lookup_moa_registry(canon_reg)
    if "get_baseline_documents" not in called_tools:
        await toolset.get_baseline_documents(canon_reg)
    if "search_official_letters" not in called_tools:
        await toolset.search_official_letters(
            canon_reg, toolset.moa_chinese_name or toolset.label_common_name
        )
    diff_payload = await toolset.run_compliance_diff(canon_reg)

    # --- Phase 2: Constrained Decoding into ComplianceAuditReport ---
    crop_evidence = toolset.build_crop_usage_evidence() if hasattr(toolset, "build_crop_usage_evidence") else {}
    evidence_bundle = {
        "reg_no": canon_reg,
        "product_name_zh": toolset.moa_chinese_name or toolset.label_common_name,
        "product_name_en": toolset.moa_english_name,
        "moa_snapshot_stale": toolset.moa_stale,
        "msds_doc_id_used": toolset.msds_doc_id_used,
        "audit_warnings": toolset.warnings,
        "deterministic_diff": diff_payload,
        "crop_usage_comparison": {
            "label_crop_usages": crop_evidence.get("label_crop_usages", []),
            "moa_approved_usages": crop_evidence.get("moa_approved_usages", []),
        },
    }
    phase2_config = types.GenerateContentConfig(
        system_instruction=_PHASE2_SYSTEM_PROMPT,
        temperature=0.0,
        response_mime_type="application/json",
        response_json_schema=ComplianceAuditReport.model_json_schema(),
    )
    phase2_resp, phase2_model = await _generate_with_5xx_fallback(
        client,
        candidate_models,
        [json.dumps(evidence_bundle, ensure_ascii=False)],
        phase2_config,
    )
    report = extract_response_json(phase2_resp, ComplianceAuditReport)
    assert isinstance(report, ComplianceAuditReport)

    # Grounding safeguard: enforce exact deterministic findings, citations, names, and warnings,
    # while preserving validated semantic `crop_usage_sync` items from Phase 2.
    valid_label_doc_ids = set(crop_evidence.get("valid_label_doc_ids", []))
    crop_items: list[ComplianceChecklistItem] = []
    for item in report.checklist_items:
        if item.category != "crop_usage_sync":
            continue
        cite = item.current_label_citation
        if cite is not None and cite.doc_id not in valid_label_doc_ids:
            cite = None
        crop_items.append(item.model_copy(update={"current_label_citation": cite, "supporting_citations": []}))

    merged_items = _to_checklist_items(toolset.last_findings) + crop_items
    merged_statuses = {f.action_status for f in toolset.last_findings} | {i.action_status for i in crop_items}
    if "pending_moa_approval" in merged_statuses:
        overall: OverallComplianceStatus = "pending_moa_approval"
    elif "must_update" in merged_statuses:
        overall = "action_required"
    elif "verify_warning" in merged_statuses or not toolset.has_label or not toolset.has_moa:
        overall = "needs_verification"
    else:
        overall = "compliant"

    log.info(
        "Copilot audit complete reg_no=%s model=%s status=%s basic_items=%d crop_items=%d",
        canon_reg,
        phase2_model,
        overall,
        len(toolset.last_findings),
        len(crop_items),
    )

    return report.model_copy(
        update={
            "reg_no": canon_reg,
            "product_name_zh": toolset.moa_chinese_name or toolset.label_common_name or report.product_name_zh,
            "product_name_en": toolset.moa_english_name or report.product_name_en,
            "overall_status": overall,
            "checklist_items": merged_items,
            "moa_snapshot_stale": toolset.moa_stale,
            "msds_doc_id_used": toolset.msds_doc_id_used,
            "audit_warnings": list(toolset.warnings),
        }
    )
