import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Boolean, Integer, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class Merchant(Base):
    """A Zynost user who has turned on merchant mode — accepts crypto
    payments on their OWN site via the public /v1/checkout API (see
    merchant_service.py). Non-custodial by design: payout_evm_xpub is the
    merchant's OWN extended public key (never a private key), so every
    order's receive address is under the merchant's own control from
    creation — this service never holds or can move a merchant's funds,
    so there is no withdrawal step."""
    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False)

    business_name: Mapped[str] = mapped_column(String(120), nullable=False)

    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)

    # --- Regeneration grace period (see merchant_service.regenerate_api_key
    # and routes/merchant.py's require_merchant_api_key) - a real production
    # incident showed why this matters: regenerating used to invalidate the
    # old key INSTANTLY, which breaks every already-deployed system still
    # using it (this gateway's own tradeos-backend integration included) the
    # moment a merchant rotates their key, with zero window to migrate.
    # Industry-standard key rotation (AWS access keys, GitHub PATs) instead
    # keeps the old credential alive for a bounded window so nothing goes
    # down mid-rotation - the old key here simply stops being accepted once
    # previous_api_key_expires_at passes, no cleanup job required. ---
    previous_api_key_hash: Mapped[str] = mapped_column(String(255), nullable=True)
    previous_api_key_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    payout_evm_xpub: Mapped[str] = mapped_column(String(140), nullable=False)
    next_evm_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payout_solana_address: Mapped[str] = mapped_column(String(64), nullable=True)

    webhook_url: Mapped[str] = mapped_column(String(500), nullable=True)
    webhook_secret: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- Monetization: two models a merchant can pick between (see
    # merchant_service.py). "flat": free orders are capped so server/RPC
    # costs stay covered; "pro" lifts the cap for a flat monthly price.
    # "volume": no cap at all — instead a small percentage of each month's
    # paid volume is owed, tracked in monthly_volume_usd. Both are settled
    # by the merchant paying THROUGH THIS SAME GATEWAY (see
    # gateway_billing_service.py) — Zynost is just another payee, never a
    # custodian, staying true to the non-custodial promise everywhere.
    #
    # billing_cycle_reset_at governs ONE shared monthly window for both
    # monthly_order_count and monthly_volume_usd. pending_billing_mode
    # exists so a mode switch can never be used to dodge a cap mid-cycle:
    # requesting a switch only queues it in pending_billing_mode, which is
    # applied at the NEXT cycle rollover, not immediately (see
    # merchant_service.set_billing_mode). ---
    billing_mode: Mapped[str] = mapped_column(String(20), default="flat", nullable=False)
    pending_billing_mode: Mapped[str] = mapped_column(String(20), nullable=True)
    billing_cycle_reset_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    monthly_volume_usd: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    volume_fee_paid_usd: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)

    # --- Solana payment-attribution watermark (see merchant_service.py's
    # poll_pending_orders and payment_check.check_solana_payment) ---
    # Every order for this merchant shares the SAME payout_solana_address
    # (Solana has no practical watch-only HD derivation the way EVM's xpub
    # does, unlike each order's own unique EVM address) - so a plain
    # "current balance >= this order's amount" check would let ONE real
    # payment satisfy MULTIPLE pending orders at once. These track how much
    # of that address's balance has already been claimed by past orders, so
    # only genuinely NEW, unclaimed balance can satisfy the next one.
    solana_usdc_consumed: Mapped[float] = mapped_column(Numeric(24, 10), default=0, nullable=False)
    solana_usdt_consumed: Mapped[float] = mapped_column(Numeric(24, 10), default=0, nullable=False)

    plan: Mapped[str] = mapped_column(String(20), default="free", nullable=False)
    plan_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    monthly_order_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Hosted checkout page customization (see checkout_page.py) ---
    # brand_color: a merchant's own hex accent color (e.g. "#16A34A"),
    # replacing the default Zynost violet on THEIR checkout pages only -
    # nullable, falls back to the default when unset. gasless_enabled lets
    # a merchant turn off the "Pay gasless instead" option for their own
    # customers if they'd rather not offer it (default true - it's already
    # merchant-agnostic and free for the merchant either way, see
    # gasless_checkout_service.py, so opt-out rather than opt-in).
    brand_color: Mapped[str] = mapped_column(String(9), nullable=True)
    gasless_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # A merchant's own uploaded checkout logo — stored as a full data URI
    # (e.g. "data:image/png;base64,...") rather than a path/URL to a
    # separately-served file, since there's no object storage anywhere in
    # this stack yet: a data URI embeds directly in checkout_page.py's
    # <img src="..."> with zero extra serving route needed. Uploads are
    # resized/re-encoded server-side (see routes/merchant.py's
    # PATCH /merchant/logo) to a small fixed max, keeping this column's
    # size bounded. Nullable — falls back to the initial-letter badge
    # (business_initial) when unset, exactly as it always has.
    logo_data: Mapped[str] = mapped_column(Text, nullable=True)

    # A fixed taxonomy (not free text) so this stays reportable/filterable
    # the way real KYB platforms categorize merchants — set only via an
    # approved BusinessProfileChangeRequest below, never written directly.
    # Null until a merchant's first business-profile change is approved;
    # its presence is also what unlocks the "Verified" badge in the
    # dashboard/checkout UI (see BusinessProfileChangeRequest).
    industry: Mapped[str] = mapped_column(String(40), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class MerchantOrder(Base):
    """One checkout created via a merchant's POST /v1/checkout call."""
    __tablename__ = "merchant_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False)

    order_reference: Mapped[str] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(String(500), nullable=True)
    amount_usd: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    evm_derivation_index: Mapped[int] = mapped_column(Integer, nullable=False)
    evm_address: Mapped[str] = mapped_column(String(64), nullable=False)
    solana_address: Mapped[str] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)  # pending | paid | expired

    paid_chain: Mapped[str] = mapped_column(String(20), nullable=True)
    paid_asset: Mapped[str] = mapped_column(String(10), nullable=True)
    paid_amount: Mapped[float] = mapped_column(Numeric(24, 10), nullable=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    webhook_delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class GatewayBillingCounter(Base):
    """Singleton row (always id=1) tracking the next unused derivation
    index under settings.ZYNOST_OWN_PAYOUT_XPUB for gateway billing
    invoices — kept separate from any merchant's own next_evm_index so
    Zynost's own billing addresses never collide with a merchant's."""
    __tablename__ = "gateway_billing_counter"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    next_evm_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class GatewayBillingInvoice(Base):
    """A merchant paying ZYNOST for gateway usage (Pro upgrade or a
    volume-fee settlement) — deliberately a separate table from
    MerchantOrder, so a merchant's own customer-facing orders and their
    own bill to us can never be confused with each other. Same
    non-custodial mechanism either way: the receive address is a
    watch-only child of settings.ZYNOST_OWN_PAYOUT_XPUB (see
    gateway_billing_service.py) — Zynost never holds a private key here
    either, it's just the payee this time instead of the payer's own
    merchant customers."""
    __tablename__ = "gateway_billing_invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False)

    purpose: Mapped[str] = mapped_column(String(20), nullable=False)  # pro_upgrade | volume_fee
    amount_usd: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    evm_derivation_index: Mapped[int] = mapped_column(Integer, nullable=False)
    evm_address: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)  # pending | paid | expired

    paid_chain: Mapped[str] = mapped_column(String(20), nullable=True)
    paid_asset: Mapped[str] = mapped_column(String(10), nullable=True)
    paid_amount: Mapped[float] = mapped_column(Numeric(24, 10), nullable=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # Set once scripts/sweep_gateway_billing.py actually moves this
    # invoice's paid stablecoin balance out to Zynost's own destination
    # wallet — same swept_at/sweep_tx_hash pattern tradeos-backend's own
    # payment_invoices table uses, so a re-run only ever touches invoices
    # that haven't been swept yet.
    swept_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    sweep_tx_hash: Mapped[str] = mapped_column(String(80), nullable=True)


class ApiKeyHistory(Base):
    """One row per key ever issued to a merchant (initial + every
    regeneration) — the raw key itself is never stored here (same
    one-way-hash-only rule as Merchant.api_key_hash), just the short
    prefix + timeline, so a merchant can see "when did I last rotate my
    key" without that being a security-sensitive record."""
    __tablename__ = "api_key_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False)

    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)


class XpubChangeRequest(Base):
    """A merchant's in-dashboard request to change their payout_evm_xpub —
    self-serve changing it directly isn't allowed (it reroutes every
    future payment), so this is the real, in-app replacement for the old
    "email support" flow: the merchant submits a request here, an admin
    reviews and approves/rejects it from /admin, and approval is the only
    thing that actually writes to Merchant.payout_evm_xpub."""
    __tablename__ = "xpub_change_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False)

    requested_xpub: Mapped[str] = mapped_column(String(140), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)  # pending | approved | rejected

    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer_note: Mapped[str] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BusinessProfileChangeRequest(Base):
    """Same non-instant, admin-gated pattern as XpubChangeRequest above,
    for business_name/industry instead of the payout wallet — plus one
    extra gate in front of admin review: the merchant must first prove
    they still control the account inbox by entering a code emailed to
    it (see routes/merchant.py's create_business_profile_change_request /
    verify_business_profile_change_request and services/email_service.py).
    Only a later admin approval actually writes to Merchant.business_name
    / Merchant.industry — verifying the email code alone is NOT enough to
    apply the change, same "approval is the only write path" rule as xpub.

    status: pending_verification -> pending_review -> approved | rejected
    (an unverified request that never gets its code entered just sits in
    pending_verification forever - harmless, never reaches an admin)."""
    __tablename__ = "business_profile_change_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False)

    requested_business_name: Mapped[str] = mapped_column(String(120), nullable=True)
    requested_industry: Mapped[str] = mapped_column(String(40), nullable=True)

    status: Mapped[str] = mapped_column(String(20), default="pending_verification", nullable=False)

    # Only the SHA-256 hex digest of the 6-digit code is stored — same
    # one-way-hash-only rule ApiKeyHistory documents for API keys, so a
    # database read alone can never leak a usable code.
    verification_code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verification_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer_note: Mapped[str] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
