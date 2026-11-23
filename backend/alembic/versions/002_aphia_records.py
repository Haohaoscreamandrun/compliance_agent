"""Add aphia_records and aphia_query_cache tables.

Revision ID: 002_aphia_records
Revises: 001_initial_schema
Create Date: 2026-11-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002_aphia_records"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("moa_registry"):
        cols = {c["name"] for c in inspector.get_columns("moa_registry")}
        if "crop_uses_json" in cols and "approved_usages_json" not in cols:
            op.alter_column(
                "moa_registry",
                "crop_uses_json",
                new_column_name="approved_usages_json",
            )
        elif "approved_usages_json" not in cols:
            op.add_column(
                "moa_registry",
                sa.Column("approved_usages_json", postgresql.JSONB(), nullable=True),
            )

    if not inspector.has_table("aphia_records"):
        op.create_table(
            "aphia_records",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("source_kind", sa.String(32), nullable=False),
            sa.Column("source_key", sa.String(128), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("common_name", sa.Text(), nullable=True),
            sa.Column("published_date", sa.String(32), nullable=True),
            sa.Column("detail_url", sa.Text(), nullable=True),
            sa.Column("raw_fields_json", postgresql.JSONB(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("source_kind", "source_key", name="uq_aphia_source"),
        )
        op.create_index("ix_aphia_records_source_kind", "aphia_records", ["source_kind"])
        op.create_index("ix_aphia_records_common_name", "aphia_records", ["common_name"])

    if not inspector.has_table("aphia_query_cache"):
        op.create_table(
            "aphia_query_cache",
            sa.Column("query_key", sa.String(128), primary_key=True),
            sa.Column("keywords_json", postgresql.JSONB(), nullable=False),
            sa.Column("result_json", postgresql.JSONB(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        )

    if not inspector.has_table("chemical_evaluations"):
        op.create_table(
            "chemical_evaluations",
            sa.Column("common_name", sa.String(256), primary_key=True),
            sa.Column("submitted_date", sa.String(32), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("chemical_evaluations"):
        op.drop_table("chemical_evaluations")
    if inspector.has_table("aphia_query_cache"):
        op.drop_table("aphia_query_cache")
    if inspector.has_table("aphia_records"):
        op.drop_index("ix_aphia_records_common_name", table_name="aphia_records")
        op.drop_index("ix_aphia_records_source_kind", table_name="aphia_records")
        op.drop_table("aphia_records")
