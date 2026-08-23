"""TDD suite for check_solana_payment's consumed-balance watermark (see
payment_check.py's docstring) — the fix for a real, live double-fulfillment
bug: multiple orders under one merchant share that merchant's single
payout_solana_address (Solana has no practical watch-only HD derivation the
way EVM's xpub does), so a plain "current balance >= this order's amount"
check let ONE real payment satisfy MULTIPLE pending orders at once.
"""
import asyncio
from decimal import Decimal

from app.services.payment_check import check_solana_payment, _SOLANA_STABLECOIN_MINTS

USDC_MINT = next(mint for mint, asset in _SOLANA_STABLECOIN_MINTS.items() if asset == "USDC")
ADDRESS = "SomeSharedMerchantSolanaAddress11111111111"


def _client_with_balance(ui_amount: float, mint: str = USDC_MINT):
    """Scripts a balance for ONLY the given mint's token account - the
    other accepted mint (see _SOLANA_STABLECOIN_MINTS) correctly reports
    empty, matching how a real address has independent per-mint token
    accounts rather than one shared balance across assets."""

    class _Resp:
        def __init__(self_inner, payload):
            self_inner._payload = payload

        def json(self_inner):
            return self_inner._payload

    class _Client:
        async def post(self_inner, url, json=None):
            requested_mint = json["params"][1]["mint"]
            if requested_mint != mint:
                return _Resp({"result": {"value": []}})
            return _Resp({
                "result": {"value": [
                    {"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": ui_amount}}}}}},
                ]}
            })

    return _Client()


def test_fresh_address_with_no_consumption_matches_normally():
    client = _client_with_balance(20.0)
    result = asyncio.run(check_solana_payment(client, ADDRESS, 20.0, {}))
    assert result is not None
    assert result["asset"] == "USDC"
    assert result["amount"] == Decimal("20.0")
    assert result["new_consumed"] == Decimal("20.0")


def test_second_order_does_not_match_when_balance_already_fully_consumed():
    # Exactly the double-fulfillment scenario: $20 arrived once, order A
    # already claimed all of it (already_consumed=20) - a second $20 order
    # checked against the SAME (unchanged) balance must NOT also match.
    client = _client_with_balance(20.0)
    result = asyncio.run(check_solana_payment(client, ADDRESS, 20.0, {"USDC": Decimal("20.0")}))
    assert result is None


def test_matches_only_the_genuinely_new_unclaimed_portion():
    # $45 total balance, $20 of it already claimed by an earlier order -
    # $25 is genuinely unclaimed, enough for a new $20 order.
    client = _client_with_balance(45.0)
    result = asyncio.run(check_solana_payment(client, ADDRESS, 20.0, {"USDC": Decimal("20.0")}))
    assert result is not None
    assert result["amount"] == Decimal("20.0")
    assert result["new_consumed"] == Decimal("40.0")  # 20 previously + 20 just claimed, $5 stays unclaimed


def test_overpayment_only_claims_the_order_amount_not_the_whole_balance():
    # Customer sent $25 for a $20 order - the order still only claims its
    # own $20, leaving the extra $5 available for a future order rather
    # than silently swallowing it.
    client = _client_with_balance(25.0)
    result = asyncio.run(check_solana_payment(client, ADDRESS, 20.0, {}))
    assert result["amount"] == Decimal("20.0")
    assert result["new_consumed"] == Decimal("20.0")


def test_insufficient_unclaimed_balance_does_not_match():
    client = _client_with_balance(20.0)
    result = asyncio.run(check_solana_payment(client, ADDRESS, 19.0, {"USDC": Decimal("10.0")}))
    # available = 20 - 10 = 10, less than the 19 owed
    assert result is None


def test_no_solana_address_returns_none_immediately():
    client = _client_with_balance(100.0)
    result = asyncio.run(check_solana_payment(client, None, 20.0, {}))
    assert result is None
