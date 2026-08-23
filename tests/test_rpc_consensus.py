"""TDD suite for the multi-RPC consensus validator (app/services/rpc_consensus.py).

Every test here is about a specific failure mode a real attacker or a real
infrastructure hiccup could produce - not just "happy path" coverage. A
payment-verification primitive that only has happy-path tests is exactly
the kind of thing that looks solid in review and then fails silently in
production.
"""
import asyncio

import pytest

from app.services import rpc_consensus
from app.services.rpc_consensus import RpcConsensusValidator, RpcProvider, build_evm_providers_for_chain

EXPECTED_CHAIN_ID = 56  # BSC, arbitrary for these tests
HOLDER = "0x" + "ab" * 20
CONTRACT = "0x" + "cd" * 20


class _ScriptedResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload


class _ScriptedClient:
    """A fake httpx.AsyncClient. `script` maps provider URL -> either:
      - a dict with "chain_id" (int) and "balance" (hex str) to script a
        normal two-call response, or
      - an Exception instance/class, to simulate that provider always
        raising (timeout, connection error, etc.) on every call.
    Records every URL actually called, so a test can assert a
    circuit-broken or rate-limited provider was never even queried.
    """

    def __init__(self, script: dict):
        self.script = script
        self.calls: list[str] = []

    async def post(self, url, json=None, timeout=None):
        self.calls.append(url)
        entry = self.script.get(url)
        if entry is None:
            raise AssertionError(f"Unscripted URL called: {url}")
        if isinstance(entry, Exception):
            raise entry
        if isinstance(entry, type) and issubclass(entry, Exception):
            raise entry("scripted failure")

        method = json["method"]
        if method == "eth_chainId":
            return _ScriptedResponse({"result": hex(entry["chain_id"])})
        elif method == "eth_call":
            return _ScriptedResponse({"result": entry["balance"]})
        raise AssertionError(f"Unexpected method: {method}")


@pytest.fixture(autouse=True)
def _reset_state():
    # Circuit breaker / rate limiter state is module-global by design (it
    # must persist across requests in production) - tests must not leak
    # state into each other.
    rpc_consensus.reset_provider_state()
    yield
    rpc_consensus.reset_provider_state()


def _providers(*names: str) -> list[RpcProvider]:
    return [RpcProvider(name=n, url=f"https://{n}.example.com") for n in names]


def test_consensus_reached_with_full_agreement():
    providers = _providers("a", "b", "c")
    script = {p.url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x64"} for p in providers}
    client = _ScriptedClient(script)
    validator = RpcConsensusValidator(providers, expected_chain_id=EXPECTED_CHAIN_ID)

    result = asyncio.run(validator.eth_call_balance_of(client, CONTRACT, HOLDER))

    assert result.reached is True
    assert result.value == 0x64
    assert sorted(result.agreeing_providers) == ["a", "b", "c"]
    assert result.disagreeing == {}


def test_consensus_reached_with_majority_two_of_three():
    providers = _providers("a", "b", "c")
    script = {
        providers[0].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x64"},
        providers[1].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x64"},
        providers[2].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x99"},  # dissenter
    }
    client = _ScriptedClient(script)
    validator = RpcConsensusValidator(providers, expected_chain_id=EXPECTED_CHAIN_ID)

    result = asyncio.run(validator.eth_call_balance_of(client, CONTRACT, HOLDER))

    assert result.reached is True
    assert result.value == 0x64
    assert sorted(result.agreeing_providers) == ["a", "b"]
    assert result.disagreeing == {"c": "0x99"}


def test_consensus_fails_when_quorum_not_reached():
    # 2 providers, quorum forced to 2 (both must agree) - a plain split
    # must NOT resolve to "whichever one, who cares", it must fail safe.
    providers = _providers("a", "b")
    script = {
        providers[0].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x64"},
        providers[1].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x99"},
    }
    client = _ScriptedClient(script)
    validator = RpcConsensusValidator(providers, expected_chain_id=EXPECTED_CHAIN_ID, quorum=2)

    result = asyncio.run(validator.eth_call_balance_of(client, CONTRACT, HOLDER))

    assert result.reached is False
    assert result.value is None
    assert result.disagreeing  # the losing side is still reported for investigation


def test_provider_exception_excluded_not_fatal():
    providers = _providers("a", "b", "c")
    script = {
        providers[0].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x64"},
        providers[1].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x64"},
        providers[2].url: TimeoutError,
    }
    client = _ScriptedClient(script)
    validator = RpcConsensusValidator(providers, expected_chain_id=EXPECTED_CHAIN_ID)

    result = asyncio.run(validator.eth_call_balance_of(client, CONTRACT, HOLDER))

    assert result.reached is True
    assert result.value == 0x64
    assert "c" in result.unavailable
    assert result.responded == 2


def test_wrong_chain_id_is_discarded_even_if_balance_matches():
    # A provider answering for the wrong network is worse than one that's
    # just down - it must never be allowed to vote, even if by coincidence
    # its balance figure matches the honest majority.
    providers = _providers("a", "b", "c")
    script = {
        providers[0].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x64"},
        providers[1].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x64"},
        providers[2].url: {"chain_id": 1, "balance": "0x64"},  # right balance, WRONG chain
    }
    client = _ScriptedClient(script)
    validator = RpcConsensusValidator(providers, expected_chain_id=EXPECTED_CHAIN_ID)

    result = asyncio.run(validator.eth_call_balance_of(client, CONTRACT, HOLDER))

    assert result.reached is True
    assert sorted(result.agreeing_providers) == ["a", "b"]
    assert "c" in result.unavailable  # discarded, not counted as agreeing


def test_malformed_balance_response_is_discarded():
    providers = _providers("a", "b", "c")
    script = {
        providers[0].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x64"},
        providers[1].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x64"},
        providers[2].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "not-hex-garbage"},
    }
    client = _ScriptedClient(script)
    validator = RpcConsensusValidator(providers, expected_chain_id=EXPECTED_CHAIN_ID)

    result = asyncio.run(validator.eth_call_balance_of(client, CONTRACT, HOLDER))

    assert result.reached is True
    assert "c" in result.unavailable


def test_circuit_breaker_opens_after_repeated_failures_and_skips_provider():
    providers = _providers("a", "flaky")
    script = {
        providers[0].url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x64"},
        providers[1].url: RuntimeError,
    }
    client = _ScriptedClient(script)
    # Default quorum for 2 providers is 2 - consensus won't actually be
    # reached here (the flaky one never votes), which is irrelevant to
    # this test: we only care whether its URL gets hit after the circuit
    # opens, not whether the round "succeeds".
    validator = RpcConsensusValidator(providers, expected_chain_id=EXPECTED_CHAIN_ID)

    # Drive the flaky provider past the failure threshold.
    for _ in range(rpc_consensus._FAILURE_THRESHOLD):
        asyncio.run(validator.eth_call_balance_of(client, CONTRACT, HOLDER))

    calls_before = len(client.calls)
    asyncio.run(validator.eth_call_balance_of(client, CONTRACT, HOLDER))
    calls_after = len(client.calls)

    # Once the circuit is open, the flaky provider's URL must not be hit
    # again this round - only provider "a" gets called.
    new_calls = client.calls[calls_before:calls_after]
    assert providers[1].url not in new_calls
    assert providers[0].url in new_calls


def test_rate_limiter_blocks_a_burst_without_hitting_the_network():
    providers = _providers("a", "b")
    script = {p.url: {"chain_id": EXPECTED_CHAIN_ID, "balance": "0x64"} for p in providers}
    client = _ScriptedClient(script)
    validator = RpcConsensusValidator(providers, expected_chain_id=EXPECTED_CHAIN_ID)

    # Burst well past the token-bucket capacity for provider "a".
    attempts = int(rpc_consensus._RATE_LIMIT_BURST) + 3
    last_result = None
    for _ in range(attempts):
        last_result = asyncio.run(validator.eth_call_balance_of(client, CONTRACT, HOLDER))

    # Each un-rate-limited round makes exactly 2 network calls to a given
    # provider (eth_chainId + eth_call); a rate-limited round makes 0. If
    # the limiter never kicked in, "a" would show attempts*2 calls exactly.
    calls_to_a = sum(1 for c in client.calls if c == providers[0].url)
    assert calls_to_a < attempts * 2
    # And it must have actually shown up as a suppressed vote at least once,
    # not just "fewer calls due to some unrelated reason".
    assert last_result.unavailable  # rate-limited provider(s) from the final round


def test_requires_at_least_two_providers():
    with pytest.raises(ValueError):
        RpcConsensusValidator(_providers("solo"), expected_chain_id=EXPECTED_CHAIN_ID)


def test_quorum_of_one_is_rejected():
    with pytest.raises(ValueError):
        RpcConsensusValidator(_providers("a", "b"), expected_chain_id=EXPECTED_CHAIN_ID, quorum=1)


def test_default_quorum_is_majority():
    v3 = RpcConsensusValidator(_providers("a", "b", "c"), expected_chain_id=EXPECTED_CHAIN_ID)
    assert v3.quorum == 2
    v5 = RpcConsensusValidator(_providers("a", "b", "c", "d", "e"), expected_chain_id=EXPECTED_CHAIN_ID)
    assert v5.quorum == 3


def test_build_evm_providers_for_chain_always_includes_baseline_and_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(rpc_consensus.settings, "ALCHEMY_API_KEY", "")
    monkeypatch.setattr(rpc_consensus.settings, "QUICKNODE_BSC_URL", "")
    monkeypatch.setattr(rpc_consensus.settings, "ANKR_API_KEY", "")

    providers = build_evm_providers_for_chain("binance-smart-chain", baseline_url="https://bsc-rpc.publicnode.com")
    names = [p.name for p in providers]

    assert "publicnode" in names
    assert "ankr" in names  # Ankr's free public endpoint always participates even with no key
    assert len(providers) >= 2


def test_build_evm_providers_for_chain_adds_alchemy_when_key_present(monkeypatch):
    monkeypatch.setattr(rpc_consensus.settings, "ALCHEMY_API_KEY", "test-key-123")
    monkeypatch.setattr(rpc_consensus.settings, "QUICKNODE_ETHEREUM_URL", "")
    monkeypatch.setattr(rpc_consensus.settings, "ANKR_API_KEY", "")

    providers = build_evm_providers_for_chain("ethereum", baseline_url="https://ethereum-rpc.publicnode.com")
    alchemy = next(p for p in providers if p.name == "alchemy")

    assert "test-key-123" in alchemy.url
    assert "eth-mainnet" in alchemy.url
