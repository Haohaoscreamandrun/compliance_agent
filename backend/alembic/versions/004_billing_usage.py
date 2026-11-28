"""Add company_billing_accounts and usage_ledger tables.

Revision ID: 004_billing_usage
Revises: 003_company_tenancy
Create Date: 2026-11-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004_billing_usage"
down_revision = "003_company_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("company_billing_accounts"):
        op.create_table(
            "company_billing_accounts",
            sa.Column(
                "company_id",
                sa.String(64),
                sa.ForeignKey("companies.company_id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("free_calls_remaining", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("credit_balance_micros", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("billing_exempt", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("billing_mode", sa.String(32), nullable=False, server_default="free_tier"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.CheckConstraint("free_calls_remaining >= 0", name="ck_billing_free_calls_nonneg"),
            sa.CheckConstraint("credit_balance_micros >= 0", name="ck_billing_credit_nonneg"),
        )

    if not inspector.has_table("usage_ledger"):
        op.create_table(
            "usage_ledger",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "company_id",
                sa.String(64),
                sa.ForeignKey("companies.company_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("user_email", sa.String(256), nullable=False),
            sa.Column("service_name", sa.String(64), nullable=False),
            sa.Column("unit", sa.String(32), nullable=False),
            sa.Column("quantity", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("reg_no", sa.String(32), nullable=True),
            sa.Column("doc_id", sa.String(64), nullable=True),
            sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("cost_micros", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("charge_source", sa.String(32), nullable=False),
            sa.Column("external_order_id", sa.String(128), nullable=True, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_usage_ledger_company_id", "usage_ledger", ["company_id"])
        op.create_index("ix_usage_ledger_user_email", "usage_ledger", ["user_email"])
        op.create_index("ix_usage_ledger_service_name", "usage_ledger", ["service_name"])
        op.create_index("ix_usage_ledger_reg_no", "usage_ledger", ["reg_no"])
        op.create_index("ix_usage_ledger_created_at", "usage_ledger", ["created_at"])

    op.execute(
        sa.text(
            "INSERT INTO company_billing_accounts (company_id, free_calls_remaining, credit_balance_micros, billing_exempt, billing_mode, updated_at) "
            "SELECT company_id, 5, 0, false, 'free_tier', NOW() FROM companies "
            "ON CONFLICT (company_id) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.drop_table("usage_ledger")
    op.drop_table("company_billing_accounts")
