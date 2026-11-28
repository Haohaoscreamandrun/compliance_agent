# cSpell:words NewebPay
"""Unit and route tests for company billing pool, 5-free-call grant, and NewebPay MPG."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app.api.deps import CompanyUserContext, get_current_company_user, require_admin_user
from app.config import Settings
from app.db.models import CompanyBillingAccount, UsageLedger
from app.db.session import get_db_session
from app.main import create_app
from app.services.billing import (
    _aes_cbc_encrypt_hex,
    apply_credit_topup,
    build_newebpay_checkout_form,
    calculate_cost_micros,
    compute_trade_sha,
    ensure_quota_available,
    record_successful_usage,
    twd_to_usd_micros,
    verify_and_decrypt_newebpay_notify,
)


def _billing_settings(tmp_path: Path) -> Settings:
    return Settings(
        gemini_api_key="test-key",
        postgres_password="test-password",
        storage_dir=tmp_path,
        newebpay_merchant_id="MS12345678",
        newebpay_hash_key="12345678901234567890123456789012",
        newebpay_hash_iv="1234567890123456",
    )


def test_cost_and_twd_conversion() -> None:
    # 1M input ($0.30) + 1M output ($1.20) = $1.50 * 1.5x = $2.25 = 2_250_000 micros
    assert calculate_cost_micros("gemini-3.8-flash", 1_000_000, 1_000_000, markup=1.5) == 2_250_000
    assert twd_to_usd_micros(320, usd_to_twd=32.0) == 10_000_000


@pytest.mark.asyncio
async def test_quota_and_usage_lifecycle() -> None:
    account = CompanyBillingAccount(
        company_id="acme",
        free_calls_remaining=1,
        credit_balance_micros=50_000,
        billing_exempt=False,
        billing_mode="free_tier",
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.execute.side_effect = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=account)),  # ensure_quota_available #1
        MagicMock(scalar_one_or_none=MagicMock(return_value=account)),  # get_or_create #1
        MagicMock(scalar_one_or_none=MagicMock(return_value=0)),        # atomic free decrement -> 0
        MagicMock(scalar_one_or_none=MagicMock(return_value=account)),  # ensure_quota_available #2
        MagicMock(scalar_one_or_none=MagicMock(return_value=account)),  # get_or_create #2
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),     # atomic free decrement -> no rows
        MagicMock(),                                                    # atomic credit decrement
        MagicMock(scalar_one_or_none=MagicMock(return_value=account)),  # ensure_quota_available #3 (402)
        MagicMock(scalar_one_or_none=MagicMock(return_value=account)),  # ensure_quota_available #4 (admin)
        MagicMock(scalar_one_or_none=MagicMock(return_value=account)),  # get_or_create #4
    ]

    # 1st call uses free_grant
    await ensure_quota_available(session, company_id="acme", role="member")
    e1 = await record_successful_usage(
        session,
        company_id="acme",
        user_email="alice@acme.com",
        is_admin=False,
        service_name="gemini_parse",
        unit="tokens",
        quantity=1000,
        cost_micros=10_000,
    )
    assert e1.charge_source == "free_grant"

    # Simulate DB state after free_grant and credit deduction
    account.free_calls_remaining = 0

    # 2nd call deducts from credit_balance_micros
    await ensure_quota_available(session, company_id="acme", role="member")
    e2 = await record_successful_usage(
        session,
        company_id="acme",
        user_email="bob@acme.com",
        is_admin=False,
        service_name="gemini_audit",
        unit="tokens",
        quantity=2000,
        cost_micros=50_000,
    )
    assert e2.charge_source == "credit_balance"

    account.credit_balance_micros = 0

    # 3rd call blocked with 402 for member, but allowed & logged as admin_exempt for admin
    with pytest.raises(HTTPException) as exc_info:
        await ensure_quota_available(session, company_id="acme", role="member")
    assert exc_info.value.status_code == 402

    await ensure_quota_available(session, company_id="acme", role="admin")
    e3 = await record_successful_usage(
        session,
        company_id="acme",
        user_email="admin@acme.com",
        is_admin=True,
        service_name="gemini_audit",
        unit="tokens",
        quantity=500,
        cost_micros=5_000,
    )
    assert e3.charge_source == "admin_exempt"


def test_newebpay_checkout_and_notify_verification(tmp_path: Path) -> None:
    settings = _billing_settings(tmp_path)
    form = build_newebpay_checkout_form(
        settings,
        company_id="acme",
        user_email="alice@acme.com",
        pack_id="pack_500",
        notify_url="https://example.com/api/billing/newebpay/notify",
        return_url="https://example.com/api/billing/newebpay/return",
    )
    assert form["form_fields"]["MerchantID"] == "MS12345678"
    assert form["form_fields"]["Version"] == "2.0"

    # Simulate NewebPay NotifyURL JSON payload
    notify_json = '{"Status":"SUCCESS","Result":{"MerchantOrderNo":"acme_1_abc","Amt":500}}'
    trade_info = _aes_cbc_encrypt_hex(notify_json, settings.newebpay_hash_key, settings.newebpay_hash_iv)
    trade_sha = compute_trade_sha(trade_info, settings.newebpay_hash_key, settings.newebpay_hash_iv)

    parsed = verify_and_decrypt_newebpay_notify(settings, trade_info_hex=trade_info, trade_sha=trade_sha)
    assert parsed["Status"] == "SUCCESS"
    assert parsed["Result"]["Amt"] == 500

    with pytest.raises(HTTPException) as bad_sig:
        verify_and_decrypt_newebpay_notify(settings, trade_info_hex=trade_info, trade_sha="0" * 64)
    assert bad_sig.value.status_code == 400


@pytest.mark.asyncio
async def test_apply_credit_topup_idempotency() -> None:
    account = CompanyBillingAccount(company_id="acme", free_calls_remaining=0, credit_balance_micros=0)
    session = AsyncMock()
    session.add = MagicMock()
    session.execute.side_effect = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),  # not seen yet
        MagicMock(scalar_one_or_none=MagicMock(return_value=account)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=42)),  # duplicate order
    ]

    first = await apply_credit_topup(
        session,
        company_id="acme",
        user_email="alice@acme.com",
        amount_micros=1_000_000,
        external_order_id="ord_1",
    )
    assert first is True
    assert account.credit_balance_micros == 1_000_000

    second = await apply_credit_topup(
        session,
        company_id="acme",
        user_email="alice@acme.com",
        amount_micros=1_000_000,
        external_order_id="ord_1",
    )
    assert second is False
    assert account.credit_balance_micros == 1_000_000


@pytest.mark.asyncio
async def test_billing_routes_summary_return_and_admin_topup(tmp_path: Path) -> None:
    app = create_app(_billing_settings(tmp_path), init_db=False)
    account = CompanyBillingAccount(
        company_id="acme",
        free_calls_remaining=3,
        credit_balance_micros=2_500_000,
        billing_exempt=False,
        billing_mode="prepaid_credits",
    )
    ev = UsageLedger(
        id=1,
        company_id="acme",
        user_email="alice@acme.com",
        service_name="gemini_parse",
        unit="tokens",
        quantity=1200,
        reg_no="農藥進99001",
        doc_id="label_1",
        metadata_json={},
        cost_micros=4500,
        charge_source="free_grant",
        created_at=datetime.now(timezone.utc),
    )

    session = AsyncMock()
    session.add = MagicMock()
    session.execute.side_effect = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=account)),
        MagicMock(all=MagicMock(return_value=[("alice@acme.com", 1, 1200, 4500)])),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[ev])))),
        MagicMock(scalar_one_or_none=MagicMock(return_value=account)),
        MagicMock(scalar_one_or_none=MagicMock(return_value=account)),
    ]

    async def _db():
        yield session

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_current_company_user] = lambda: CompanyUserContext(
        email="alice@acme.com", company_id="acme", role="member"
    )
    app.dependency_overrides[require_admin_user] = lambda: CompanyUserContext(
        email="admin@acme.com", company_id="default", role="admin"
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        summary = await client.get("/api/billing/summary")
        assert summary.status_code == 200
        body = summary.json()
        assert body["free_calls_remaining"] == 3
        assert body["credit_balance_usd"] == 2.5
        assert len(body["packs"]) == 3

        ret = await client.post("/api/billing/newebpay/return", follow_redirects=False)
        assert ret.status_code == 303
        assert ret.headers["location"] == "/?payment=pending"

        topup = await client.post("/api/admin/companies/acme/credits", json={"amount_twd": 320})
        assert topup.status_code == 200
        assert topup.json()["credit_balance_usd"] == 12.5
