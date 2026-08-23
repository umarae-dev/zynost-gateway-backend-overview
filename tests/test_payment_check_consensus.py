"""check_evm_payment() carries real money for OTHER merchants' customers,
so it must go through the multi-RPC consensus validator (see
rpc_consensus.py) rather than trusting a single free RPC's answer — this
regression-tests that wiring, not the validator's own internal logic
(covered separately in test_rpc_consensus.py)."""
import asyncio
from decimal import Decimal

from app.services import payment_check, rpc_consensus

_MAINNET_CHAIN_ID_BY_URL = {
    payment_check._EVM_RPCS["ethereum"]: 1,
    payment_check._EVM_RPCS["binance-smart-chain"]: 56,
    payment_check._EVM_RPCS["polygon-pos"]: 137,
    "https://rpc.ankr.com/eth": 1,
    "https://rpc.ankr.com/bsc": 56,
    "https://rpc.ankr.com/polygon": 137,
}


class _ScriptedClient:
    """Answers eth_chainId truthfully and eth_call with a scripted balance
    (default zero) - records every URL hit so tests can assert the
    consensus path actually queried multiple independent providers, not
    just the old single publicnode.com endpoint."""

    def __init__(self, balance_hex: str = "0x0"):
        self.balance_hex = balance_hex
        self.urls_called: list[str] = []

    async def post(self, url, json=None, timeout=None):
        self.urls_called.append(url)
        method = (json or {}).get("method")

        class _Resp:
            def __init__(self_inner, payload):
                self_inner._payload = payload

            def json(self_inner):
                return self_inner._payload

        if method == "eth_chainId":
            return _Resp({"result": hex(_MAINNET_CHAIN_ID_BY_URL.get(url, 0))})
        return _Resp({"result": self.balance_hex})


def _pin_free_provider_pool(monkeypatch):
    """Deterministic 2-provider pool (publicnode + Ankr free) regardless of
    whatever keys happen to be in the local .env."""
    monkeypatch.setattr(rpc_consensus.settings, "ALCHEMY_API_KEY", "")
    monkeypatch.setattr(rpc_consensus.settings, "QUICKNODE_ETHEREUM_URL", "")
    monkeypatch.setattr(rpc_consensus.settings, "QUICKNODE_BSC_URL", "")
    monkeypatch.setattr(rpc_consensus.settings, "QUICKNODE_POLYGON_URL", "")
    monkeypatch.setattr(rpc_consensus.settings, "ANKR_API_KEY", "")


def test_check_evm_payment_queries_more_than_the_single_baseline_rpc(monkeypatch):
    _pin_free_provider_pool(monkeypatch)
    rpc_consensus.reset_provider_state()

    client = _ScriptedClient(balance_hex="0x0")  # zero balance everywhere = no match, just proving the query shape
    asyncio.run(payment_check.check_evm_payment(client, "0x" + "3" * 40, Decimal("5.00")))

    baseline_urls = set(payment_check._EVM_RPCS.values())
    ankr_urls = {"https://rpc.ankr.com/eth", "https://rpc.ankr.com/bsc", "https://rpc.ankr.com/polygon"}
    called = set(client.urls_called)
    assert called  # something was actually queried
    # It must have gone beyond the single old baseline RPC per chain - the
    # whole point of this wiring is more than one independent provider.
    assert called & ankr_urls
    assert called.issubset(baseline_urls | ankr_urls)


def test_check_evm_payment_confirms_a_real_matching_balance(monkeypatch):
    _pin_free_provider_pool(monkeypatch)
    rpc_consensus.reset_provider_state()

    # 5 USDT on Ethereum (6 decimals): 5_000_000 = 0x4c4b40
    client = _ScriptedClient(balance_hex=hex(5_000_000))
    result = asyncio.run(payment_check.check_evm_payment(client, "0x" + "4" * 40, Decimal("5.00")))

    assert result is not None
    assert result["chain"] == "ethereum"
    assert result["asset"] == "USDT"
    assert result["amount"] == Decimal("5")


def test_check_evm_payment_rejects_underpayment(monkeypatch):
    _pin_free_provider_pool(monkeypatch)
    rpc_consensus.reset_provider_state()

    # Only 1 USDT when 5 is owed - must not confirm.
    client = _ScriptedClient(balance_hex=hex(1_000_000))
    result = asyncio.run(payment_check.check_evm_payment(client, "0x" + "5" * 40, Decimal("5.00")))

    assert result is None


def test_check_evm_payment_tolerates_a_typical_exchange_withdrawal_fee(monkeypatch):
    """Real reported friction: a customer paying FROM an exchange
    withdrawal sees the exchange's own withdrawal fee (commonly ~1% on a
    stablecoin) deducted before it ever reaches the chain - nothing to do
    with Zynost or gas. The old 99.9% tolerance only covered rounding, so
    a customer who sent exactly what an exchange told them to withdraw
    could still land "underpaid" and stuck. 5.00 owed, 4.95 received (1%
    short) must now confirm."""
    _pin_free_provider_pool(monkeypatch)
    rpc_consensus.reset_provider_state()

    client = _ScriptedClient(balance_hex=hex(4_950_000))
    result = asyncio.run(payment_check.check_evm_payment(client, "0x" + "6" * 40, Decimal("5.00")))

    assert result is not None
    assert result["amount"] == Decimal("4.95")


def test_check_evm_payment_still_rejects_a_meaningfully_short_payment(monkeypatch):
    """The loosened tolerance is a deliberate, bounded business tradeoff
    (absorb a typical exchange fee), not an open door - 2% short must
    still fail to confirm."""
    _pin_free_provider_pool(monkeypatch)
    rpc_consensus.reset_provider_state()

    client = _ScriptedClient(balance_hex=hex(4_900_000))
    result = asyncio.run(payment_check.check_evm_payment(client, "0x" + "7" * 40, Decimal("5.00")))

    assert result is None
