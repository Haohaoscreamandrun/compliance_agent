# cSpell:words JSONB jsonb typeof uq_doc_page ck_doc_type ck_page_count ck_page_no ck_clause_page ck_clause_box_2d ck_width_px ck_height_px onupdate
"""Single-store PostgreSQL 16 JSONB ORM models per ADR-001."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


VALID_DOC_TYPES = ("label", "msds", "change_cert", "official_letter")


class Base(DeclarativeBase):
    """Declarative base for all Compliance Agent PostgreSQL tables."""


class MoaRegistry(Base):
    """Cached snapshot of a product's Taiwan MOA EIR027 record and 4 sub-endpoints."""

    __tablename__ = "moa_registry"

    # Canonical permit key: 許可證字 + 許可證號 (e.g. "農藥製00001" or "農藥原製00195")
    reg_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    permit_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    permit_number: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    chinese_name: Mapped[str | None] = mapped_column(String(128), index=True)
    pesticide_code: Mapped[str | None] = mapped_column(String(32), index=True)
    english_name: Mapped[str | None] = mapped_column(String(256), index=True)
    brand_name: Mapped[str | None] = mapped_column(String(128))
    company_name: Mapped[str | None] = mapped_column(String(128), index=True)
    foreign_manufacturer: Mapped[str | None] = mapped_column(String(256))
    formulation: Mapped[str | None] = mapped_column(String(32))
    content_pct: Mapped[str | None] = mapped_column(String(64))
    valid_until_roc: Mapped[str | None] = mapped_column(String(32))
    revocation_status: Mapped[str | None] = mapped_column(String(64))

    # Full JSONB payloads from PesticideData.aspx and its 4 sub-endpoints
    raw_core_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    approved_usages_json: Mapped[list[dict[str, Any]] | dict[str, Any] | None] = (
        mapped_column(JSONB)
    )
    license_label_json: Mapped[list[dict[str, Any]] | dict[str, Any] | None] = (
        mapped_column(JSONB)
    )
    barcode_json: Mapped[list[dict[str, Any]] | dict[str, Any] | None] = mapped_column(
        JSONB
    )
    mixture_ratio_json: Mapped[list[dict[str, Any]] | dict[str, Any] | None] = (
        mapped_column(JSONB)
    )

    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )


class Document(Base):
    """Uploaded regulatory PDF (Label, MSDS, Change Certificate, or Official Letter 公文)."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "doc_type IN ('label', 'msds', 'change_cert', 'official_letter')",
            name="ck_doc_type",
        ),
        CheckConstraint("page_count >= 1", name="ck_page_count"),
        Index("ix_documents_baseline_lookup", "reg_no", "doc_type", "is_active_baseline"),
        Index(
            "uq_documents_active_baseline",
            "reg_no",
            "doc_type",
            unique=True,
            postgresql_where=text("is_active_baseline IS TRUE AND reg_no IS NOT NULL"),
        ),
    )

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reg_no: Mapped[str | None] = mapped_column(String(32), index=True)
    common_name: Mapped[str | None] = mapped_column(String(128), index=True)
    pesticide_code: Mapped[str | None] = mapped_column(String(32), index=True)
    company_name: Mapped[str | None] = mapped_column(String(128), index=True)

    # Distinguishes active product baselines from candidate audit uploads to prevent self-diffing
    is_active_baseline: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    extracted_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )

    pages: Mapped[list[DocumentPage]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )
    clauses: Mapped[list[DocumentClause]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentPage(Base):
    """Normalized WebP page image metadata (width_px/height_px for zero-CLS <svg viewBox="0 0 1000 1000"> overlay)."""

    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("doc_id", "page_number", name="uq_doc_page"),
        CheckConstraint("page_number >= 1", name="ck_page_no"),
        CheckConstraint("width_px > 0", name="ck_width_px"),
        CheckConstraint("height_px > 0", name="ck_height_px"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("documents.doc_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    image_rel_path: Mapped[str] = mapped_column(String(512), nullable=False)
    width_px: Mapped[int] = mapped_column(Integer, nullable=False)
    height_px: Mapped[int] = mapped_column(Integer, nullable=False)

    document: Mapped[Document] = relationship(back_populates="pages")


class DocumentClause(Base):
    """Flattened clause/field index with [ymin, xmin, ymax, xmax] (0-1000) box_2d provenance."""

    __tablename__ = "document_clauses"
    __table_args__ = (
        CheckConstraint("page_number >= 1", name="ck_clause_page"),
        CheckConstraint(
            "doc_type IN ('label', 'msds', 'change_cert', 'official_letter')",
            name="ck_clause_doc_type",
        ),
        CheckConstraint(
            "jsonb_typeof(box_2d) = 'array' AND jsonb_array_length(box_2d) = 4",
            name="ck_clause_box_2d",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("documents.doc_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reg_no: Mapped[str | None] = mapped_column(String(32), index=True)
    common_name: Mapped[str | None] = mapped_column(String(128), index=True)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    section_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    verbatim_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(Text)
    box_2d: Mapped[list[int]] = mapped_column(JSONB, nullable=False)

    document: Mapped[Document] = relationship(back_populates="clauses")
