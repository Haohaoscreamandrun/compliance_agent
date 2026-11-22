"""Initial PostgreSQL schema for Compliance Agent (all 6 tables).

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "moa_registry",
        sa.Column("reg_no", sa.String(32), primary_key=True),
        sa.Column("permit_type", sa.String(16), nullable=False),
        sa.Column("permit_number", sa.String(16), nullable=False),
        sa.Column("chinese_name", sa.String(128), nullable=True),
        sa.Column("pesticide_code", sa.String(32), nullable=True),
        sa.Column("english_name", sa.String(256), nullable=True),
        sa.Column("brand_name", sa.String(128), nullable=True),
        sa.Column("company_name", sa.String(128), nullable=True),
        sa.Column("foreign_manufacturer", sa.String(256), nullable=True),
        sa.Column("formulation", sa.String(64), nullable=True),
        sa.Column("content_pct", sa.String(64), nullable=True),
        sa.Column("valid_until_roc", sa.String(32), nullable=True),
        sa.Column("revocation_status", sa.String(64), nullable=True),
        sa.Column("raw_core_json", postgresql.JSONB(), nullable=False),
        sa.Column("crop_uses_json", postgresql.JSONB(), nullable=True),
        sa.Column("license_label_json", postgresql.JSONB(), nullable=True),
        sa.Column("barcode_json", postgresql.JSONB(), nullable=True),
        sa.Column("mixture_ratio_json", postgresql.JSONB(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "documents",
        sa.Column("doc_id", sa.String(64), primary_key=True),
        sa.Column("filename", sa.String(256), nullable=False),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("reg_no", sa.String(32), nullable=True),
        sa.Column("volume_tag", sa.String(64), nullable=False, server_default=""),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("common_name", sa.String(128), nullable=True),
        sa.Column("pesticide_code", sa.String(32), nullable=True),
        sa.Column("company_name", sa.String(128), nullable=True),
        sa.Column("is_active_baseline", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("extracted_data", postgresql.JSONB(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "doc_type IN ('label', 'msds', 'change_cert', 'official_letter')", name="ck_doc_type"
        ),
        sa.CheckConstraint("page_count >= 1", name="ck_page_count"),
    )
    op.create_index("ix_documents_baseline_lookup", "documents", ["reg_no", "doc_type", "is_active_baseline"])
    op.create_index("ix_documents_content_sha256", "documents", ["content_sha256"])
    op.create_index(
        "uq_documents_active_baseline",
        "documents",
        ["reg_no", "doc_type", "volume_tag"],
        unique=True,
        postgresql_where=sa.text("is_active_baseline IS TRUE AND reg_no IS NOT NULL"),
    )

    op.create_table(
        "document_pages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("doc_id", sa.String(64), sa.ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("image_rel_path", sa.String(512), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        sa.UniqueConstraint("doc_id", "page_number", name="uq_doc_page"),
        sa.CheckConstraint("page_number >= 1", name="ck_page_no"),
        sa.CheckConstraint("width_px > 0", name="ck_width_px"),
        sa.CheckConstraint("height_px > 0", name="ck_height_px"),
    )

    op.create_table(
        "document_clauses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("doc_id", sa.String(64), sa.ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("reg_no", sa.String(32), nullable=True),
        sa.Column("common_name", sa.String(128), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.String(64), nullable=False),
        sa.Column("verbatim_text", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=True),
        sa.Column("box_2d", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("page_number >= 1", name="ck_clause_page"),
        sa.CheckConstraint(
            "doc_type IN ('label', 'msds', 'change_cert', 'official_letter')", name="ck_clause_doc_type"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(box_2d) = 'array' AND jsonb_array_length(box_2d) = 4", name="ck_clause_box_2d"
        ),
    )

    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("zh_name", sa.String(256), nullable=False),
        sa.Column("en_name", sa.String(256), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("zh_name", "en_name", name="uq_entity_alias_pair"),
    )

    op.create_table(
        "audit_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reg_no", sa.String(32), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False, unique=True),
        sa.Column("report_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_reports")
    op.drop_table("entity_aliases")
    op.drop_table("document_clauses")
    op.drop_table("document_pages")
    op.drop_table("documents")
    op.drop_table("moa_registry")
