# cSpell:words MSDSs
"""Pydantic v2 schema for Phase 2 Constrained Decoding into ComplianceAuditReport."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.grounded import Coord0To1000
from app.services.cascade_diff import ActionStatus, DiffCategory

OverallComplianceStatus = Literal[
    "compliant",
    "action_required",
    "pending_moa_approval",
    "needs_verification",
]


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("Field must not be blank.")
    return value


class CitationRef(BaseModel):
    """Exact document coordinate citation for `<svg viewBox="0 0 1000 1000">` highlighting."""

    doc_id: str = Field(..., min_length=1)
    page: int = Field(..., ge=1)
    box_2d: list[Coord0To1000] = Field(..., min_length=4, max_length=4)
    verbatim_quote: str = Field(..., min_length=1)
    extra_boxes_2d: list[list[Coord0To1000]] = Field(default_factory=list)

    @field_validator("doc_id", "verbatim_quote")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        return _require_non_blank(value)


class ComplianceChecklistItem(BaseModel):
    """One actionable row in the Label Update Checklist."""

    field_name: str = Field(..., min_length=1)
    category: DiffCategory
    action_status: ActionStatus
    current_label_value: str
    recommended_new_value: str
    rationale: str
    current_label_citation: CitationRef | None = None
    supporting_citations: list[CitationRef] = Field(default_factory=list)


class ComplianceAuditReport(BaseModel):
    """Structured compliance audit output from Phase 2 Constrained Decoding."""

    reg_no: str = Field(..., min_length=1)
    product_name_zh: str | None = None
    product_name_en: str | None = None
    overall_status: OverallComplianceStatus
    executive_summary_zh: str = Field(..., min_length=1)
    executive_summary_en: str = Field(..., min_length=1)
    checklist_items: list[ComplianceChecklistItem] = Field(default_factory=list)
    moa_snapshot_stale: bool = False
    msds_doc_id_used: str | None = None
    audit_warnings: list[str] = Field(default_factory=list)

    @field_validator("executive_summary_zh", "executive_summary_en")
    @classmethod
    def _summary_non_blank(cls, value: str) -> str:
        return _require_non_blank(value)
