# cSpell:words NewebPay microdollar micros
"""Billing summary, NewebPay MPG checkout/notify/return, and admin credit top-up routes."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CompanyUserContext, get_current_company_user, require_admin_user
from app.config import Settings
from app.db.models import UsageLedger
from app.db.session import get_db_session
from app.services.billing import (
    TOPUP_PACKS_TWD,
    apply_credit_topup,
    build_newebpay_checkout_form,
    get_or_create_billing_account,
    twd_to_usd_micros,
    verify_and_decrypt_newebpay_notify,
)
from app.services.storage import validate_company_id

router = APIRouter(prefix="/api", tags=["billing"])


class UsageByUserItem(BaseModel):
    user_email: str
    calls: int
    total_tokens: int
    total_cost_usd: float


class UsageEventOut(BaseModel):
    id: int
    user_email: str
    service_name: str
    unit: str
    quantity: int
    reg_no: str | None = None
    cost_usd: float
    charge_source: str
    created_at: datetime


class TopupPackOut(BaseModel):
    pack_id: str
    amount_twd: int
    label: str
    estimated_usd: float


class BillingSummaryResponse(BaseModel):
    company_id: str
    free_calls_remaining: int
    free_calls_limit: int
    credit_balance_usd: float
    billing_exempt: bool
    billing_mode: str
    packs: list[TopupPackOut]
    usage_by_user: list[UsageByUserItem]
    recent_events: list[UsageEventOut]


class CheckoutRequest(BaseModel):
    pack_id: str = Field(..., min_length=1, max_length=32)


class CheckoutResponse(BaseModel):
    mpg_url: str
    order_no: str
    form_fields: dict[str, str]


class AdminCreditTopupRequest(BaseModel):
    amount_twd: int = Field(..., ge=1, le=1_000_000)
    note: str | None = Field(default=None, max_length=256)


@router.get("/billing/summary", response_model=BillingSummaryResponse)
async def get_billing_summary(
    request: Request,
    user: CompanyUserContext = Depends(get_current_company_user),
    session: AsyncSession = Depends(get_db_session),
) -> BillingSummaryResponse:
    settings: Settings = request.app.state.settings
    account = await get_or_create_billing_account(
        session,
        user.company_id,
        default_free_calls=settings.billing_free_calls_default,
    )
    await session.commit()

    by_user_rows = (
        await session.execute(
            select(
                UsageLedger.user_email,
                func.count(UsageLedger.id),
                func.coalesce(func.sum(UsageLedger.quantity), 0),
                func.coalesce(func.sum(UsageLedger.cost_micros), 0),
            )
            .where(
                UsageLedger.company_id == user.company_id,
                UsageLedger.cost_micros >= 0,
            )
            .group_by(UsageLedger.user_email)
            .order_by(func.sum(UsageLedger.cost_micros).desc())
        )
    ).all()

    recent_rows = (
        await session.execute(
            select(UsageLedger)
            .where(UsageLedger.company_id == user.company_id)
            .order_by(UsageLedger.created_at.desc(), UsageLedger.id.desc())
            .limit(20)
        )
    ).scalars().all()

    packs = [
        TopupPackOut(
            pack_id=p["pack_id"],
            amount_twd=p["amount_twd"],
            label=p["label"],
            estimated_usd=round(p["amount_twd"] / settings.billing_usd_to_twd, 2),
        )
        for p in TOPUP_PACKS_TWD.values()
    ]

    return BillingSummaryResponse(
        company_id=user.company_id,
        free_calls_remaining=account.free_calls_remaining if account else settings.billing_free_calls_default,
        free_calls_limit=settings.billing_free_calls_default,
        credit_balance_usd=round((account.credit_balance_micros if account else 0) / 1_000_000.0, 4),
        billing_exempt=(user.role == "admin") or bool(account and account.billing_exempt),
        billing_mode=account.billing_mode if account else "free_tier",
        packs=packs,
        usage_by_user=[
            UsageByUserItem(
                user_email=str(r[0]),
                calls=int(r[1]),
                total_tokens=int(r[2] or 0),
                total_cost_usd=round(int(r[3] or 0) / 1_000_000.0, 4),
            )
            for r in by_user_rows
        ],
        recent_events=[
            UsageEventOut(
                id=ev.id,
                user_email=ev.user_email,
                service_name=ev.service_name,
                unit=ev.unit,
                quantity=ev.quantity,
                reg_no=ev.reg_no,
                cost_usd=round(ev.cost_micros / 1_000_000.0, 4),
                charge_source=ev.charge_source,
                created_at=ev.created_at,
            )
            for ev in recent_rows
        ],
    )


@router.post("/billing/checkout", response_model=CheckoutResponse)
async def create_newebpay_checkout(
    body: CheckoutRequest,
    request: Request,
    user: CompanyUserContext = Depends(get_current_company_user),
) -> CheckoutResponse:
    settings: Settings = request.app.state.settings
    base_url = settings.public_base_url.strip().rstrip("/") or str(request.base_url).rstrip("/")
    try:
        payload = build_newebpay_checkout_form(
            settings,
            company_id=user.company_id,
            user_email=user.email,
            pack_id=body.pack_id,
            notify_url=f"{base_url}/api/billing/newebpay/notify",
            return_url=f"{base_url}/api/billing/newebpay/return",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CheckoutResponse(**payload)


@router.post("/billing/newebpay/notify")
async def newebpay_notify_webhook(
    request: Request,
    TradeInfo: str = Form(...),
    TradeSha: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    payload = verify_and_decrypt_newebpay_notify(
        settings,
        trade_info_hex=TradeInfo,
        trade_sha=TradeSha,
    )
    if payload.get("Status") != "SUCCESS":
        return {"status": "ignored"}

    result = payload.get("Result") or {}
    merchant_id = str(result.get("MerchantID") or "").strip()
    expected_merchant_id = settings.newebpay_merchant_id.strip()
    if expected_merchant_id and merchant_id != expected_merchant_id:
        raise HTTPException(status_code=400, detail="MerchantID mismatch.")

    order_no = str(result.get("MerchantOrderNo") or "").strip()
    amt_twd = int(result.get("Amt") or 0)
    if not order_no or amt_twd <= 0:
        raise HTTPException(status_code=400, detail="Missing MerchantOrderNo or Amt.")

    comment_raw = str(result.get("OrderComment") or "")
    company_id = order_no.split("_", 1)[0]
    user_email = str(result.get("Email") or "newebpay@checkout").strip().lower()
    try:
        meta = json.loads(comment_raw) if comment_raw.startswith("{") else {}
        if isinstance(meta.get("cid"), str) and meta["cid"]:
            company_id = meta["cid"]
        if isinstance(meta.get("email"), str) and meta["email"]:
            user_email = meta["email"]
    except json.JSONDecodeError:
        pass

    validate_company_id(company_id)
    amount_micros = twd_to_usd_micros(amt_twd, settings.billing_usd_to_twd)
    await apply_credit_topup(
        session,
        company_id=company_id,
        user_email=user_email,
        amount_micros=amount_micros,
        external_order_id=order_no,
        charge_source="newebpay_topup",
        metadata_json={"amt_twd": amt_twd, "trade_no": result.get("TradeNo")},
    )
    return {"status": "ok"}


@router.post("/billing/newebpay/return")
async def newebpay_return_redirect() -> RedirectResponse:
    return RedirectResponse(url="/?payment=pending", status_code=303)


@router.post("/admin/companies/{company_id}/credits")
async def admin_grant_company_credits(
    company_id: str,
    body: AdminCreditTopupRequest,
    request: Request,
    admin: CompanyUserContext = Depends(require_admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    cid = validate_company_id(company_id)
    settings: Settings = request.app.state.settings
    amount_micros = twd_to_usd_micros(body.amount_twd, settings.billing_usd_to_twd)
    await apply_credit_topup(
        session,
        company_id=cid,
        user_email=admin.email,
        amount_micros=amount_micros,
        charge_source="admin_grant",
        metadata_json={"amt_twd": body.amount_twd, "note": body.note or ""},
    )
    account = await get_or_create_billing_account(session, cid)
    return {
        "company_id": cid,
        "credited_twd": body.amount_twd,
        "credit_balance_usd": round((account.credit_balance_micros if account else 0) / 1_000_000.0, 4),
    }
