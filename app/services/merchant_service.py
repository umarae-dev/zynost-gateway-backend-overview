"""The multi-tenant payment gateway's core logic — completely standalone
from tradeos-backend (see app/core/config.py's docstring on what's
actually shared: just the DB and JWT_SECRET at the environment-variable
level, never a Python import).

Non-custodial by design: every order's EVM receive address is derived
from the MERCHANT's own xpub (see wallet_derivation.derive_evm_address),
so this service never holds a private key capable of moving a merchant's
collected funds — there's no "withdraw" step for a merchant because the
money never passes through a wallet this service controls.
"""
import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.models.merchant import Merchant, MerchantOrder
from app.services import bip32_lite, push_notification_service
from app.services.wallet_derivation import derive_evm_address
from app.services.payment_check import check_evm_payment, check_solana_payment

logger = logging.getLogger("gateway")

_ORDER_LIFETIME = timedelta(minutes=45)
_API_KEY_PREFIX_LIVE = "zg_live_"
_WEBHOOK_TIMEOUT = 10.0

# Free plan: enough for a small/testing merchant to fully evaluate the
# gateway; a real, meaningfully-sized business is expected to either
# upgrade to flat Pro or switch to volume billing — either way, that's
# what actually covers this service's own RPC/hosting costs.
FREE_MONTHLY_ORDER_CAP = 50
_MONTHLY_RESET_DAYS = 30

# Volume billing: no order cap at all, instead this percentage of each
# month's paid volume is owed. Settled manually (see billing_mode's
# docstring on why this service never auto-charges anything).
VOLUME_FEE_RATE = 0.003
_BILLING_MODES = {"flat", "volume"}

# Flat Pro: a fixed monthly price for unlimited orders, paid through this
# same gateway (see gateway_billing_service.py) — Zynost is just the payee.
PRO_MONTHLY_PRICE_USD = 19.0


class InvalidXpubError(Exception):
    pass


class QuotaExceededError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class InvalidBillingModeError(Exception):
    pass


def validate_evm_xpub(xpub: str) -> None:
    try:
        bip32_lite.parse_xpub(xpub.strip())
    except Exception as e:
        raise InvalidXpubError(f"That doesn't look like a valid xpub: {e}")


def generate_api_key() -> tuple[str, str, str]:
    raw = _API_KEY_PREFIX_LIVE + secrets.token_urlsafe(32)
    return raw, hash_token(raw), raw[:16]


API_KEY_GRACE_PERIOD = timedelta(hours=24)


def matches_current_or_grace_period_key(merchant, key_hash: str, now: datetime | None = None) -> bool:
    """True if key_hash is this merchant's CURRENT API key, or their
    PREVIOUS one (set by regenerate_api_key's rotation) with its
    grace-period window still open. Real incident this prevents: a
    merchant regenerates their key, and every system already deployed with
    the old one (including this gateway's own tradeos-backend integration)
    starts failing immediately with zero migration window - the old key
    simply stops matching once previous_api_key_expires_at passes, no
    cleanup job needed."""
    now = now or datetime.now(timezone.utc)
    if merchant.api_key_hash == key_hash:
        return True
    if merchant.previous_api_key_hash == key_hash and merchant.previous_api_key_expires_at:
        return merchant.previous_api_key_expires_at > now
    return False


def generate_webhook_secret() -> str:
    return secrets.token_hex(24)


def regenerate_webhook_secret(merchant: Merchant) -> str:
    """Rotates a merchant's webhook signing secret in place - the same
    "rolling secret" capability generate_api_key/regenerate_api_key already
    give merchants for their API key. A merchant who suspects their old
    secret leaked (e.g. logged by an over-eager webhook receiver on their
    own side) can rotate it without losing their merchant account, API
    key, or order history - only the signature-verification key changes.
    Caller commits afterward."""
    new_secret = generate_webhook_secret()
    merchant.webhook_secret = new_secret
    return new_secret


def sign_webhook_payload(secret: str, payload_json: str) -> str:
    return hmac.new(secret.encode(), payload_json.encode(), hashlib.sha256).hexdigest()


def _ensure_billing_cycle_current(merchant: Merchant) -> None:
    """Lazy monthly rollover — same pattern credits_service.py uses for
    Zynost's own daily/monthly credit resets. ONE shared window governs
    both the flat order-count cap and the volume-fee counter, and — this
    is the important part — a queued billing_mode switch (see
    set_billing_mode) is only ever applied HERE, at a cycle boundary.
    That's deliberate: it's what stops a merchant from, say, burning
    through their free 50 orders under 'flat' mid-month and then
    instantly hopping to 'volume' to keep going uncapped — the switch
    they requested doesn't take effect until their NEXT cycle starts, so
    whichever mode was active when the cycle began is the one that
    governs it all the way through."""
    now = datetime.now(timezone.utc)
    if merchant.billing_cycle_reset_at is None or now >= merchant.billing_cycle_reset_at:
        if merchant.pending_billing_mode is not None:
            merchant.billing_mode = merchant.pending_billing_mode
            merchant.pending_billing_mode = None
        merchant.monthly_order_count = 0
        merchant.monthly_volume_usd = 0
        merchant.volume_fee_paid_usd = 0
        merchant.billing_cycle_reset_at = now + timedelta(days=_MONTHLY_RESET_DAYS)
    # A paid plan that has lapsed quietly falls back to free-tier limits
    # the same way Zynost's own subscription_tier lapses — checked here,
    # not in a separate scheduled job.
    if merchant.plan == "pro" and merchant.plan_expires_at is not None and now >= merchant.plan_expires_at:
        merchant.plan = "free"


def fee_owed_usd(merchant: Merchant) -> float:
    if merchant.billing_mode != "volume":
        return 0.0
    owed = float(merchant.monthly_volume_usd) * VOLUME_FEE_RATE - float(merchant.volume_fee_paid_usd)
    return round(max(0.0, owed), 2)


def set_billing_mode(merchant: Merchant, mode: str) -> None:
    """Queues a billing-mode switch for the START of the merchant's NEXT
    cycle — never applied immediately once a cycle is already underway
    (see _ensure_billing_cycle_current for why). The one exception: a
    brand-new merchant who hasn't had a cycle start yet (no order/invoice
    has ever been created) can pick freely, since there's nothing to game
    before their first cycle even begins."""
    if mode not in _BILLING_MODES:
        raise InvalidBillingModeError(f"billing_mode must be one of {sorted(_BILLING_MODES)}.")
    if mode == merchant.billing_mode:
        merchant.pending_billing_mode = None  # cancel any switch back to the current mode
        return
    if merchant.billing_cycle_reset_at is None:
        merchant.billing_mode = mode
        return
    merchant.pending_billing_mode = mode


async def _reserve_next_evm_index(db: AsyncSession, merchant: Merchant) -> int:
    """Atomically claims the next derivation index for this merchant and
    returns it - see create_order's docstring on the race this replaces.
    RETURNING gives back the POST-increment value in the same round trip,
    so this is exactly one statement, not a read then a separate write."""
    result = await db.execute(
        update(Merchant)
        .where(Merchant.id == merchant.id)
        .values(next_evm_index=Merchant.next_evm_index + 1)
        .returning(Merchant.next_evm_index)
    )
    new_value = result.scalar_one()
    merchant.next_evm_index = new_value  # keep the caller's in-memory object consistent
    return new_value - 1


async def create_order(
    db: AsyncSession,
    merchant: Merchant,
    amount_usd: float,
    order_reference: str | None,
    description: str | None,
) -> MerchantOrder:
    """Raises QuotaExceededError if a free-plan merchant has used up this
    month's order allowance. Caller commits afterward."""
    _ensure_billing_cycle_current(merchant)
    # Volume-billing merchants have no order cap at all — their cost is
    # already covered proportionally by the fee on what actually gets paid.
    if merchant.billing_mode == "flat" and merchant.plan == "free" and merchant.monthly_order_count >= FREE_MONTHLY_ORDER_CAP:
        raise QuotaExceededError(
            f"Free plan limit reached ({FREE_MONTHLY_ORDER_CAP} orders/month). "
            f"Upgrade to keep accepting payments without interruption."
        )

    # Real, previously-unprotected race condition this closes: reading
    # merchant.next_evm_index and incrementing it in plain Python memory
    # meant two concurrent checkout requests for the same merchant (a busy
    # merchant with many customers checking out at once, e.g. right after
    # a broadcast email or a pricing-page traffic spike) could both read
    # the SAME index before either committed, derive the SAME evm_address,
    # and silently corrupt payment attribution between two different
    # customers' orders - with no error, since nothing at the DB level
    # enforced uniqueness either. UPDATE...RETURNING is atomic at the
    # Postgres row level: concurrent UPDATEs to the same merchant row are
    # serialized by Postgres itself, so every caller is guaranteed a
    # distinct index with zero application-level locking needed.
    index = await _reserve_next_evm_index(db, merchant)
    merchant.monthly_order_count += 1
    evm_address = derive_evm_address(index, xpub=merchant.payout_evm_xpub)

    order = MerchantOrder(
        merchant_id=merchant.id,
        order_reference=order_reference,
        description=description,
        amount_usd=amount_usd,
        evm_derivation_index=index,
        evm_address=evm_address,
        solana_address=merchant.payout_solana_address,
        expires_at=datetime.now(timezone.utc) + _ORDER_LIFETIME,
    )
    db.add(order)
    return order


async def deliver_webhook(merchant: Merchant, order: MerchantOrder) -> bool:
    if not merchant.webhook_url:
        return False
    payload = {
        "event": "checkout.confirmed",
        "order_id": str(order.id),
        "order_reference": order.order_reference,
        "amount_usd": float(order.amount_usd),
        "paid_amount": float(order.paid_amount) if order.paid_amount is not None else None,
        "paid_asset": order.paid_asset,
        "paid_chain": order.paid_chain,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
    }
    payload_json = json.dumps(payload, separators=(",", ":"))
    signature = sign_webhook_payload(merchant.webhook_secret, payload_json)
    try:
        async with httpx.AsyncClient(timeout=_WEBHOOK_TIMEOUT) as client:
            resp = await client.post(
                merchant.webhook_url,
                content=payload_json,
                headers={"Content-Type": "application/json", "X-Zynost-Signature": signature},
            )
        return resp.status_code < 400
    except Exception:
        return False


async def poll_pending_orders(db: AsyncSession) -> int:
    """Background poll loop's core — mirrors tradeos-backend's own
    poll_pending_invoices, using this service's own self-contained
    payment_check module instead of importing anything.

    Pending orders are processed oldest-first (FIFO) specifically so that
    when multiple orders for the same merchant share one Solana address
    (see check_solana_payment's docstring), whichever order was created
    first gets first claim on any newly-arrived balance - each match
    updates that merchant's consumed watermark immediately, in-memory, so
    the NEXT order checked in this same pass sees the update rather than
    both orders racing to claim the same payment."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(MerchantOrder).where(MerchantOrder.status == "pending").order_by(MerchantOrder.created_at.asc())
    )
    pending = list(result.scalars().all())
    if not pending:
        return 0

    merchant_ids = {o.merchant_id for o in pending}
    merchants_result = await db.execute(select(Merchant).where(Merchant.id.in_(merchant_ids)))
    merchants_by_id = {m.id: m for m in merchants_result.scalars().all()}

    paid_count = 0
    async with httpx.AsyncClient() as client:
        for order in pending:
            if order.expires_at <= now:
                order.status = "expired"
                continue

            merchant = merchants_by_id.get(order.merchant_id)
            match = await check_evm_payment(client, order.evm_address, order.amount_usd)
            if match is None and merchant is not None:
                already_consumed = {
                    "USDC": Decimal(str(merchant.solana_usdc_consumed)),
                    "USDT": Decimal(str(merchant.solana_usdt_consumed)),
                }
                match = await check_solana_payment(client, order.solana_address, order.amount_usd, already_consumed)
                if match is not None:
                    if match["asset"] == "USDC":
                        merchant.solana_usdc_consumed = match["new_consumed"]
                    elif match["asset"] == "USDT":
                        merchant.solana_usdt_consumed = match["new_consumed"]
            if match is None:
                continue

            order.status = "paid"
            order.paid_chain = match["chain"]
            order.paid_asset = match["asset"]
            order.paid_amount = match["amount"]
            order.paid_at = now
            paid_count += 1

            if merchant is not None:
                if merchant.billing_mode == "volume":
                    _ensure_billing_cycle_current(merchant)
                    merchant.monthly_volume_usd = float(merchant.monthly_volume_usd) + float(match["amount"])
                delivered = await deliver_webhook(merchant, order)
                if delivered:
                    order.webhook_delivered_at = now
                logger.info(
                    "Gateway: CONFIRMED order=%s merchant=%s amount=%s %s chain=%s webhook=%s",
                    order.id, merchant.business_name, match["amount"], match["asset"], match["chain"], delivered,
                )
                await push_notification_service.send_push_to_user(
                    db, merchant.user_id, "Payment received",
                    f"${match['amount']} {match['asset']} confirmed"
                    + (f" for order {order.order_reference}" if order.order_reference else "."),
                )

    await db.commit()
    return paid_count
