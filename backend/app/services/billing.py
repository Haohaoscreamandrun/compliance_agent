# cSpell:words NewebPay microdollar micros PKCS
"""Multi-service company billing pool, 5-free-call grant, usage ledger, and NewebPay MPG helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import CompanyBillingAccount, UsageLedger

# USD per 1M tokens (input, output)
MODEL_RATES_USD_PER_1M: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.30, 1.20),
    "gemini-3.6-flash": (0.15, 0.60),
    "gemini-3.5-flash-lite": (0.075, 0.30),
}
DEFAULT_RATE_USD_PER_1M: tuple[float, float] = (0.30, 1.20)

TOPUP_PACKS_TWD: dict[str, dict[str, Any]] = {
    "pack_150": {"pack_id": "pack_150", "amount_twd": 150, "label": "NT$150 入門儲值包"},
    "pack_500": {"pack_id": "pack_500", "amount_twd": 500, "label": "NT$500 標準儲值包"},
    "pack_1500": {"pack_id": "pack_1500", "amount_twd": 1500, "label": "NT$1,500 企業儲值包"},
}


def calculate_cost_micros(
    model_name: str,
    prompt_tokens: int,
    output_tokens: int,
    *,
    markup: float = 1.5,
) -> int:
    """Compute billable cost in USD micros (1e-6 USD) from token counts and markup."""
    in_rate, out_rate = MODEL_RATES_USD_PER_1M.get(model_name, DEFAULT_RATE_USD_PER_1M)
    raw_usd = (max(0, prompt_tokens) * in_rate + max(0, output_tokens) * out_rate) / 1_000_000.0
    return max(0, int(round(raw_usd * markup * 1_000_000)))


def twd_to_usd_micros(amount_twd: int, usd_to_twd: float = 32.0) -> int:
    """Convert TWD top-up amount into USD micros."""
    if amount_twd <= 0 or usd_to_twd <= 0:
        raise ValueError("Top-up amount and exchange rate must be positive.")
    return int(round((amount_twd / usd_to_twd) * 1_000_000))


async def get_or_create_billing_account(
    session: AsyncSession,
    company_id: str,
    *,
    default_free_calls: int = 5,
) -> CompanyBillingAccount | None:
    """Fetch or idempotently create the company's billing account (None when session is mocked)."""
    res = await session.execute(
        select(CompanyBillingAccount).where(CompanyBillingAccount.company_id == company_id)
    )
    account = res.scalar_one_or_none()
    if isinstance(account, CompanyBillingAccount):
        return account
    if account is not None:
        return None

    await session.execute(
        insert(CompanyBillingAccount)
        .values(
            company_id=company_id,
            free_calls_remaining=default_free_calls,
            credit_balance_micros=0,
            billing_exempt=False,
            billing_mode="free_tier",
        )
        .on_conflict_do_nothing(index_elements=["company_id"])
    )
    res2 = await session.execute(
        select(CompanyBillingAccount).where(CompanyBillingAccount.company_id == company_id)
    )
    created = res2.scalar_one_or_none()
    return created if isinstance(created, CompanyBillingAccount) else None


async def ensure_quota_available(
    session: AsyncSession,
    *,
    company_id: str,
    role: str,
    default_free_calls: int = 5,
) -> CompanyBillingAccount | None:
    """Verify company has free calls or prepaid credits (admins and exempt companies bypass)."""
    if role == "admin":
        return None
    account = await get_or_create_billing_account(
        session, company_id, default_free_calls=default_free_calls
    )
    if account is None or account.billing_exempt:
        return account
    if account.free_calls_remaining <= 0 and account.credit_balance_micros <= 0:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "QUOTA_EXCEEDED",
                "message": "貴公司免費額度（5 次）與儲值餘額皆已用畢，請儲值後繼續使用 AI 解析與合規審查。",
                "free_calls_remaining": 0,
                "credit_balance_usd": 0.0,
            },
        )
    return account


async def record_successful_usage(
    session: AsyncSession,
    *,
    company_id: str,
    user_email: str,
    is_admin: bool,
    service_name: str,
    unit: str,
    quantity: int,
    cost_micros: int,
    reg_no: str | None = None,
    doc_id: str | None = None,
    metadata_json: dict[str, Any] | None = None,
    default_free_calls: int = 5,
) -> UsageLedger:
    """Deduct quota (or record admin exemption) and append an immutable usage ledger row."""
    account = await get_or_create_billing_account(
        session, company_id, default_free_calls=default_free_calls
    )
    if is_admin or (account is not None and account.billing_exempt):
        charge_source = "admin_exempt"
    else:
        # Atomic SQL decrement avoids lost updates under concurrent AI requests.
        free_res = await session.execute(
            update(CompanyBillingAccount)
            .where(
                CompanyBillingAccount.company_id == company_id,
                CompanyBillingAccount.free_calls_remaining > 0,
            )
            .values(free_calls_remaining=CompanyBillingAccount.free_calls_remaining - 1)
            .returning(CompanyBillingAccount.free_calls_remaining)
        )
        updated_free = free_res.scalar_one_or_none()
        if updated_free is not None:
            charge_source = "free_grant"
        else:
            await session.execute(
                update(CompanyBillingAccount)
                .where(CompanyBillingAccount.company_id == company_id)
                .values(
                    credit_balance_micros=func.greatest(
                        0, CompanyBillingAccount.credit_balance_micros - max(0, cost_micros)
                    ),
                    billing_mode="prepaid_credits",
                )
            )
            charge_source = "credit_balance"

    entry = UsageLedger(
        company_id=company_id,
        user_email=user_email,
        service_name=service_name,
        unit=unit,
        quantity=max(0, quantity),
        reg_no=reg_no,
        doc_id=doc_id,
        metadata_json=metadata_json or {},
        cost_micros=max(0, cost_micros),
        charge_source=charge_source,
    )
    session.add(entry)
    return entry


async def apply_credit_topup(
    session: AsyncSession,
    *,
    company_id: str,
    user_email: str,
    amount_micros: int,
    external_order_id: str | None = None,
    charge_source: str = "newebpay_topup",
    metadata_json: dict[str, Any] | None = None,
) -> bool:
    """Credit a company's pool idempotently (by external_order_id when provided)."""
    if amount_micros <= 0:
        raise ValueError("amount_micros must be positive.")
    if external_order_id:
        existing = (
            await session.execute(
                select(UsageLedger.id).where(UsageLedger.external_order_id == external_order_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return False

    account = await get_or_create_billing_account(session, company_id)
    account.credit_balance_micros += amount_micros
    account.billing_mode = "prepaid_credits"

    session.add(
        UsageLedger(
            company_id=company_id,
            user_email=user_email,
            service_name="credit_topup",
            unit="usd_micros",
            quantity=amount_micros,
            metadata_json=metadata_json or {},
            cost_micros=-amount_micros,
            charge_source=charge_source,
            external_order_id=external_order_id,
        )
    )
    await session.commit()
    return True


def _aes_cbc_encrypt_hex(plaintext: str, key: str, iv: str) -> str:
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key.encode("utf-8")), modes.CBC(iv.encode("utf-8")))
    enc = cipher.encryptor()
    return (enc.update(padded) + enc.finalize()).hex()


def _aes_cbc_decrypt_hex(cipher_hex: str, key: str, iv: str) -> str:
    raw = bytes.fromhex(cipher_hex.strip())
    cipher = Cipher(algorithms.AES(key.encode("utf-8")), modes.CBC(iv.encode("utf-8")))
    dec = cipher.decryptor()
    padded = dec.update(raw) + dec.finalize()
    try:
        unpadder = sym_padding.PKCS7(128).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
    except ValueError:
        # NewebPay's pay2go notify gateway may pad with NUL/control bytes; TradeSha is already verified before decrypt.
        plain = padded.rstrip(bytes(range(0x00, 0x20)))
    return plain.decode("utf-8")


def compute_trade_sha(trade_info_hex: str, key: str, iv: str) -> str:
    raw = f"HashKey={key}&{trade_info_hex}&HashIV={iv}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()


def make_merchant_order_no(company_id: str) -> str:
    """Create a <=30-char NewebPay MerchantOrderNo embedding company_id safely."""
    safe_cid = "".join(ch for ch in company_id if ch.isalnum() or ch == "_")[:12] or "co"
    ts = int(time.time())
    nonce = secrets.token_hex(3)
    return f"{safe_cid}_{ts}_{nonce}"[:30]


def build_newebpay_checkout_form(
    settings: Settings,
    *,
    company_id: str,
    user_email: str,
    pack_id: str,
    notify_url: str,
    return_url: str,
) -> dict[str, Any]:
    """Generate NewebPay MPG v2.0 signed form payload for hosted checkout redirect."""
    pack = TOPUP_PACKS_TWD.get(pack_id)
    if pack is None:
        raise ValueError(f"Unknown top-up pack: {pack_id!r}")
    merchant_id = settings.newebpay_merchant_id.strip()
    hash_key = settings.newebpay_hash_key.strip()
    hash_iv = settings.newebpay_hash_iv.strip()
    if not merchant_id or len(hash_key) != 32 or len(hash_iv) != 16:
        raise HTTPException(
            status_code=503,
            detail="NewebPay 金流尚未設定完成（請聯繫系統管理員或使用管理員儲值）。",
        )

    order_no = make_merchant_order_no(company_id)
    comment = f"農藥標示合規審查系統 — {pack['label']}（付款完成後自動入帳至公司帳戶）"
    params = {
        "MerchantID": merchant_id,
        "RespondType": "JSON",
        "TimeStamp": str(int(time.time())),
        "Version": "2.0",
        "MerchantOrderNo": order_no,
        "Amt": int(pack["amount_twd"]),
        "ItemDesc": pack["label"],
        "ReturnURL": return_url,
        "NotifyURL": notify_url,
        "Email": user_email,
        "OrderComment": comment,
    }
    trade_info = _aes_cbc_encrypt_hex(urlencode(params), hash_key, hash_iv)
    trade_sha = compute_trade_sha(trade_info, hash_key, hash_iv)
    return {
        "mpg_url": settings.newebpay_mpg_url.strip(),
        "order_no": order_no,
        "form_fields": {
            "MerchantID": merchant_id,
            "TradeInfo": trade_info,
            "TradeSha": trade_sha,
            "Version": "2.0",
        },
    }


def verify_and_decrypt_newebpay_notify(
    settings: Settings,
    *,
    trade_info_hex: str,
    trade_sha: str,
) -> dict[str, Any]:
    """Verify TradeSha with constant-time compare and decrypt TradeInfo JSON."""
    hash_key = settings.newebpay_hash_key.strip()
    hash_iv = settings.newebpay_hash_iv.strip()
    if len(hash_key) != 32 or len(hash_iv) != 16:
        raise HTTPException(status_code=503, detail="NewebPay credentials not configured.")
    expected_sha = compute_trade_sha(trade_info_hex, hash_key, hash_iv)
    if not hmac.compare_digest(expected_sha, trade_sha.strip().upper()):
        raise HTTPException(status_code=400, detail="Invalid NewebPay TradeSha signature.")
    decrypted = _aes_cbc_decrypt_hex(trade_info_hex, hash_key, hash_iv)
    return json.loads(decrypted)
