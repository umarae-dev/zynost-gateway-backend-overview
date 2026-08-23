"""Integration-level regression test for the real double-fulfillment bug
fixed in merchant_service.poll_pending_orders + payment_check.
check_solana_payment: two pending orders sharing one merchant's Solana
address, only ONE real payment having arrived, must resolve to exactly one
paid order (the older one, FIFO) - not both.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.merchant import Merchant, MerchantOrder
from app.services import merchant_service, rpc_consensus
from app.services.payment_check import _SOLANA_STABLECOIN_MINTS

_USDC_MINT = next(mint for mint, asset in _SOLANA_STABLECOIN_MINTS.items() if asset == "USDC")


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _FakeSession:
    """Returns queued results in call order - poll_pending_orders always
    calls db.execute exactly twice per pass (pending orders, then their
    merchants), in that fixed order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.commits = 0

    async def execute(self, _stmt):
        return _FakeResult(self._responses.pop(0))

    async def commit(self):
        self.commits += 1


class _FakeHttpClient:
    """Zero EVM balance everywhere (via a chain-id mismatch, same net
    effect as check_evm_payment's own consensus-not-reached path) so every
    order falls through to the Solana check - a real shared-address
    balance is scripted there instead."""

    def __init__(self, solana_balance: float):
        self.solana_balance = solana_balance

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, timeout=None):
        method = (json or {}).get("method")

        class _Resp:
            def __init__(self_inner, payload):
                self_inner._payload = payload

            def json(self_inner):
                return self_inner._payload

        if method == "getTokenAccountsByOwner":
            requested_mint = json["params"][1]["mint"]
            if requested_mint != _USDC_MINT:
                return _Resp({"result": {"value": []}})
            return _Resp({
                "result": {"value": [
                    {"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": self.solana_balance}}}}}}
                ]}
            })
        return _Resp({"result": "0x0"})  # eth_chainId=0 (mismatch) / eth_call=0 either way -> no EVM match


@pytest.fixture(autouse=True)
def _reset_rpc_state():
    rpc_consensus.reset_provider_state()
    yield
    rpc_consensus.reset_provider_state()


def _order(merchant_id, solana_address, amount_usd, created_at):
    return MerchantOrder(
        id=uuid.uuid4(), merchant_id=merchant_id, amount_usd=amount_usd,
        evm_derivation_index=0, evm_address="0x" + "1" * 40, solana_address=solana_address,
        status="pending", expires_at=datetime.now(timezone.utc) + timedelta(minutes=30), created_at=created_at,
    )


def test_only_the_older_order_is_marked_paid_when_one_payment_covers_both(monkeypatch):
    monkeypatch.setattr(merchant_service, "httpx", type("_M", (), {"AsyncClient": lambda: _FakeHttpClient(20.0)}))

    merchant_id = uuid.uuid4()
    solana_address = "SharedSolanaAddress1111111111111111111111"
    now = datetime.now(timezone.utc)
    older_order = _order(merchant_id, solana_address, 20.0, now - timedelta(minutes=10))
    newer_order = _order(merchant_id, solana_address, 20.0, now - timedelta(minutes=5))
    merchant = Merchant(
        id=merchant_id, user_id=uuid.uuid4(), business_name="Test Merchant",
        api_key_hash="x", api_key_prefix="zg_live_x", payout_evm_xpub="xpub_test",
        payout_solana_address=solana_address, webhook_secret="secret",
        solana_usdc_consumed=0, solana_usdt_consumed=0,
    )

    session = _FakeSession(responses=[[older_order, newer_order], [merchant]])
    paid_count = asyncio.run(merchant_service.poll_pending_orders(session))

    assert paid_count == 1
    assert older_order.status == "paid"
    assert newer_order.status == "pending"  # NOT double-fulfilled from the same payment
    assert merchant.solana_usdc_consumed == 20.0
    assert session.commits == 1


def test_a_second_genuinely_new_payment_pays_the_second_order_on_a_later_pass(monkeypatch):
    merchant_id = uuid.uuid4()
    solana_address = "SharedSolanaAddress2222222222222222222222"
    now = datetime.now(timezone.utc)
    older_order = _order(merchant_id, solana_address, 20.0, now - timedelta(minutes=10))
    newer_order = _order(merchant_id, solana_address, 20.0, now - timedelta(minutes=5))
    merchant = Merchant(
        id=merchant_id, user_id=uuid.uuid4(), business_name="Test Merchant",
        api_key_hash="x", api_key_prefix="zg_live_x", payout_evm_xpub="xpub_test",
        payout_solana_address=solana_address, webhook_secret="secret",
        solana_usdc_consumed=0, solana_usdt_consumed=0,
    )

    # First pass: only $20 has arrived - only the older order should pay.
    monkeypatch.setattr(merchant_service, "httpx", type("_M", (), {"AsyncClient": lambda: _FakeHttpClient(20.0)}))
    session1 = _FakeSession(responses=[[older_order, newer_order], [merchant]])
    asyncio.run(merchant_service.poll_pending_orders(session1))
    assert older_order.status == "paid"
    assert newer_order.status == "pending"

    # Second pass: a genuinely NEW payment arrives, bringing the shared
    # address's total balance to $40 - now the second order's $20 is truly
    # unclaimed and should be recognized as paid.
    monkeypatch.setattr(merchant_service, "httpx", type("_M", (), {"AsyncClient": lambda: _FakeHttpClient(40.0)}))
    session2 = _FakeSession(responses=[[newer_order], [merchant]])
    asyncio.run(merchant_service.poll_pending_orders(session2))
    assert newer_order.status == "paid"
    assert merchant.solana_usdc_consumed == 40.0
