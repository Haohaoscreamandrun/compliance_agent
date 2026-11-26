"""Add company multi-tenancy tables and company_id scoping.

Revision ID: 003_company_tenancy
Revises: 002_aphia_records
Create Date: 2026-11-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "003_company_tenancy"
down_revision = "002_aphia_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("companies"):
        op.create_table(
            "companies",
            sa.Column("company_id", sa.String(64), primary_key=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.CheckConstraint(
                "company_id NOT LIKE '%/%' AND company_id NOT LIKE '%\\\\%' AND company_id NOT LIKE '%..%'",
                name="ck_company_id_safe_path",
            ),
        )

    op.execute(
        sa.text(
            "INSERT INTO companies (company_id, name) VALUES "
            "('default', 'Default Workspace'), "
            "('public_demo', 'Public Demo') "
            "ON CONFLICT (company_id) DO NOTHING"
        )
    )

    if not inspector.has_table("company_users"):
        op.create_table(
            "company_users",
            sa.Column("email", sa.String(256), primary_key=True),
            sa.Column(
                "company_id",
                sa.String(64),
                sa.ForeignKey("companies.company_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(32), nullable=False, server_default="member"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.CheckConstraint("role IN ('member', 'admin')", name="ck_company_user_role"),
            sa.CheckConstraint("email = lower(email)", name="ck_company_user_email_lower"),
        )
        op.create_index("ix_company_users_company_id", "company_users", ["company_id"])

    if not inspector.has_table("company_access_requests"):
        op.create_table(
            "company_access_requests",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("email", sa.String(256), nullable=False),
            sa.Column("requested_company_name", sa.String(128), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column(
                "assigned_company_id",
                sa.String(64),
                sa.ForeignKey("companies.company_id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("reviewed_by", sa.String(256), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending', 'approved', 'rejected')",
                name="ck_access_request_status",
            ),
            sa.CheckConstraint("email = lower(email)", name="ck_access_request_email_lower"),
        )
        op.create_index("ix_company_access_requests_email", "company_access_requests", ["email"])
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_access_requests_pending_email "
                "ON company_access_requests (email) WHERE status = 'pending'"
            )
        )

    if inspector.has_table("documents"):
        doc_cols = {c["name"] for c in inspector.get_columns("documents")}
        if "company_id" not in doc_cols:
            op.add_column(
                "documents",
                sa.Column(
                    "company_id",
                    sa.String(64),
                    sa.ForeignKey("companies.company_id", ondelete="CASCADE"),
                    nullable=False,
                    server_default="default",
                ),
            )
            op.create_index("ix_documents_company_id", "documents", ["company_id"])
            op.execute(sa.text("DROP INDEX IF EXISTS ix_documents_baseline_lookup"))
            op.execute(
                sa.text(
                    "CREATE INDEX IF NOT EXISTS ix_documents_baseline_lookup "
                    "ON documents (company_id, reg_no, doc_type, is_active_baseline)"
                )
            )
            op.execute(sa.text("DROP INDEX IF EXISTS uq_documents_active_baseline"))
            op.execute(
                sa.text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_active_baseline "
                    "ON documents (company_id, reg_no, doc_type, volume_tag) "
                    "WHERE is_active_baseline IS TRUE AND reg_no IS NOT NULL"
                )
            )

    if inspector.has_table("entity_aliases"):
        alias_cols = {c["name"] for c in inspector.get_columns("entity_aliases")}
        if "company_id" not in alias_cols:
            op.add_column(
                "entity_aliases",
                sa.Column(
                    "company_id",
                    sa.String(64),
                    sa.ForeignKey("companies.company_id", ondelete="CASCADE"),
                    nullable=False,
                    server_default="default",
                ),
            )
            op.create_index("ix_entity_aliases_company_id", "entity_aliases", ["company_id"])
            op.execute(sa.text("ALTER TABLE entity_aliases DROP CONSTRAINT IF EXISTS uq_entity_alias_pair"))
            op.create_unique_constraint(
                "uq_entity_alias_pair",
                "entity_aliases",
                ["company_id", "zh_name", "en_name"],
            )

    if inspector.has_table("audit_reports"):
        audit_cols = {c["name"] for c in inspector.get_columns("audit_reports")}
        if "company_id" not in audit_cols:
            op.add_column(
                "audit_reports",
                sa.Column(
                    "company_id",
                    sa.String(64),
                    sa.ForeignKey("companies.company_id", ondelete="CASCADE"),
                    nullable=False,
                    server_default="default",
                ),
            )
            op.create_index("ix_audit_reports_company_id", "audit_reports", ["company_id"])

    if inspector.has_table("chemical_evaluations"):
        eval_cols = {c["name"] for c in inspector.get_columns("chemical_evaluations")}
        if "company_id" not in eval_cols:
            op.add_column(
                "chemical_evaluations",
                sa.Column(
                    "company_id",
                    sa.String(64),
                    sa.ForeignKey("companies.company_id", ondelete="CASCADE"),
                    nullable=False,
                    server_default="default",
                ),
            )
            op.execute(
                sa.text(
                    "ALTER TABLE chemical_evaluations DROP CONSTRAINT IF EXISTS chemical_evaluations_pkey"
                )
            )
            op.create_primary_key(
                "chemical_evaluations_pkey",
                "chemical_evaluations",
                ["company_id", "common_name"],
            )


def downgrade() -> None:
    op.drop_table("company_access_requests")
    op.drop_table("company_users")
    op.drop_table("companies")
