"""Multi-RPC consensus validator — ported from tradeos-backend's own
app/services/rpc_consensus.py (same "never trust a single RPC" principle
applied here, since this gateway's merchant payment checks carry the exact
same class of risk tradeos-backend's own subscription checkout does — see
payment_check.py's module docstring on why this whole service is a direct
mainnet-only port of that logic).

Why this exists: payment_check.py's check_evm_payment() used to query
exactly ONE RPC endpoint per chain (see the old _EVM_RPCS-only version).
That endpoint is a real single point of failure for money moving through
OTHER merchants' checkouts, not just Zynost's own — if it's ever
DNS-hijacked, compromised, or simply wrong, this service has no way to
notice and would confirm (or reject) a real merchant payment on that one
endpoint's word alone. This module fixes that structurally: query multiple
independent providers concurrently, and only trust the answer if a
configurable quorum (default 2-of-3) agrees on the EXACT value.

Design principles (all deliberate, not incidental — identical to
tradeos-backend's copy of this module, kept in lockstep on purpose):
  - Fail SAFE, never fail OPEN. If quorum can't be reached, the caller gets
    `reached=False` — never "well, use whichever one answered."
  - Chain ID is verified on every single response, not just once. A
    provider that ever answers for the wrong chain has its answer
    discarded outright — it counts as "didn't respond usefully," not as a
    vote.
  - Per-provider circuit breaker: a provider that fails repeatedly is
    skipped for a cooldown window instead of being retried into every
    single request.
  - Per-provider rate limiting: protects the free/shared public RPC
    endpoints (and this service's own reputation with them).
  - Disagreement is logged loudly. Two providers returning different
    numbers for the same eth_call is either a bug or an attack, never
    silently smoothed over by "well, 2 of them agreed."
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from app.core.config import settings

logger = logging.getLogger("gateway.rpc_consensus")

# Alchemy's per-chain subdomain naming and Ankr's per-chain path segment,
# keyed by this codebase's own chain-name convention (see payment_check.py's
# _EVM_RPCS) so callers never need to know these vendor-specific naming
# schemes themselves.
_ALCHEMY_SUBDOMAIN = {"ethereum": "eth-mainnet", "binance-smart-chain": "bnb-mainnet", "polygon-pos": "polygon-mainnet"}
_ANKR_PATH = {"ethereum": "eth", "binance-smart-chain": "bsc", "polygon-pos": "polygon"}
_QUICKNODE_SETTING = {
    "ethereum": "QUICKNODE_ETHEREUM_URL",
    "binance-smart-chain": "QUICKNODE_BSC_URL",
    "polygon-pos": "QUICKNODE_POLYGON_URL",
}


def build_evm_providers_for_chain(chain: str, baseline_url: str) -> list[RpcProvider]:
    """Assembles this chain's provider pool: the existing free publicnode.com
    endpoint already used today (`baseline_url`) plus whichever of Alchemy/
    QuickNode/Ankr have an API key configured. Degrades gracefully — with
    zero keys configured this still returns 2 providers (publicnode.com +
    Ankr's free public endpoint), enough for a real 2-of-2 consensus check,
    not a hard failure."""
    providers = [RpcProvider(name="publicnode", url=baseline_url)]

    if settings.ALCHEMY_API_KEY and chain in _ALCHEMY_SUBDOMAIN:
        providers.append(RpcProvider(
            name="alchemy",
            url=f"https://{_ALCHEMY_SUBDOMAIN[chain]}.g.alchemy.com/v2/{settings.ALCHEMY_API_KEY}",
        ))

    quicknode_url = getattr(settings, _QUICKNODE_SETTING.get(chain, ""), "")
    if quicknode_url:
        providers.append(RpcProvider(name="quicknode", url=quicknode_url))

    if chain in _ANKR_PATH:
        ankr_path = _ANKR_PATH[chain]
        ankr_url = (
            f"https://rpc.ankr.com/{ankr_path}/{settings.ANKR_API_KEY}"
            if settings.ANKR_API_KEY
            else f"https://rpc.ankr.com/{ankr_path}"
        )
        providers.append(RpcProvider(name="ankr", url=ankr_url))

    return providers

_ERC20_BALANCE_OF_SELECTOR = "0x70a08231"


@dataclass(frozen=True)
class RpcProvider:
    """One independent RPC endpoint. `name` is used only for logging/circuit
    breaker bookkeeping — it never needs to match anything on-chain."""
    name: str
    url: str


@dataclass
class ConsensusResult:
    value: int | None
    reached: bool
    agreeing_providers: list[str] = field(default_factory=list)
    disagreeing: dict[str, str] = field(default_factory=dict)  # provider name -> raw hex they returned
    unavailable: list[str] = field(default_factory=list)  # circuit-open, rate-limited, timed out, or wrong chain ID
    responded: int = 0
    total_providers: int = 0


# --- Per-provider state, kept at module scope so it persists across
# requests. ---

_FAILURE_THRESHOLD = 5          # consecutive failures before a provider's circuit opens
_COOLDOWN_SECONDS = 60.0        # how long an open circuit stays open before a half-open trial
_RATE_LIMIT_PER_SECOND = 8.0    # token-bucket refill rate per provider
_RATE_LIMIT_BURST = 8.0         # token-bucket capacity per provider


@dataclass
class _ProviderState:
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0
    tokens: float = _RATE_LIMIT_BURST
    last_refill: float = field(default_factory=time.monotonic)

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.circuit_open_until = 0.0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= _FAILURE_THRESHOLD:
            self.circuit_open_until = time.monotonic() + _COOLDOWN_SECONDS

    def circuit_is_open(self) -> bool:
        return time.monotonic() < self.circuit_open_until

    def try_consume_token(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.last_refill = now
        self.tokens = min(_RATE_LIMIT_BURST, self.tokens + elapsed * _RATE_LIMIT_PER_SECOND)
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


_provider_state: dict[str, _ProviderState] = {}


def _state_for(provider_name: str) -> _ProviderState:
    if provider_name not in _provider_state:
        _provider_state[provider_name] = _ProviderState()
    return _provider_state[provider_name]


def reset_provider_state() -> None:
    """Test-only escape hatch — production code never needs to call this;
    circuit/rate-limit state is meant to persist for the life of the
    process."""
    _provider_state.clear()


def _is_valid_hex_uint(value: str) -> bool:
    """Anti-spoofing sanity check on a raw eth_call result before it's even
    considered as a vote: must be a real 0x-prefixed hex string, decode to a
    non-negative integer, and fit in the 32-byte word the EVM actually
    returns for a balanceOf call."""
    if not isinstance(value, str) or not value.startswith("0x"):
        return False
    hex_digits = value[2:]
    if not hex_digits:
        return True  # "0x" alone is a valid (zero) uint256 encoding some nodes return
    if len(hex_digits) > 64:  # 32 bytes = 64 hex chars
        return False
    try:
        int(hex_digits, 16)
    except ValueError:
        return False
    return True


class RpcConsensusValidator:
    """Queries `providers` concurrently for the same read and requires
    `quorum` of them to agree on the exact value before trusting it.
    `expected_chain_id` is checked on every provider's own eth_chainId
    response in the same request batch — never cached/assumed once and
    reused."""

    def __init__(
        self,
        providers: list[RpcProvider],
        expected_chain_id: int,
        quorum: int | None = None,
        timeout_seconds: float = 6.0,
    ):
        if len(providers) < 2:
            raise ValueError("RpcConsensusValidator needs at least 2 independent providers to mean anything.")
        self.providers = providers
        self.expected_chain_id = expected_chain_id
        self.quorum = quorum if quorum is not None else (len(providers) // 2 + 1)
        if self.quorum < 2:
            raise ValueError("Quorum below 2 provides no protection against a single bad/compromised provider.")
        self.timeout_seconds = timeout_seconds

    async def _query_one(self, client: httpx.AsyncClient, provider: RpcProvider, to: str, data: str) -> tuple[str, str | None]:
        """Returns (provider_name, raw_hex_result_or_None). None means
        "does not count as a vote" — rate-limited, circuit-open, timed out,
        malformed, or wrong chain ID all collapse to this same outcome."""
        state = _state_for(provider.name)

        if state.circuit_is_open():
            logger.warning("rpc_consensus: %s circuit OPEN, skipping this round", provider.name)
            return provider.name, None

        if not state.try_consume_token():
            logger.warning("rpc_consensus: %s rate-limited, skipping this round", provider.name)
            return provider.name, None

        attempts = 2  # one retry on transient network errors, no more
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                chain_id_resp = await client.post(
                    provider.url,
                    json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
                    timeout=self.timeout_seconds,
                )
                chain_id_hex = (chain_id_resp.json() or {}).get("result")
                if not isinstance(chain_id_hex, str):
                    raise ValueError(f"eth_chainId returned no usable result: {chain_id_hex!r}")
                reported_chain_id = int(chain_id_hex, 16)
                if reported_chain_id != self.expected_chain_id:
                    logger.error(
                        "rpc_consensus: %s reported chain ID %s, expected %s - discarding its answer entirely",
                        provider.name, reported_chain_id, self.expected_chain_id,
                    )
                    state.record_failure()
                    return provider.name, None

                balance_resp = await client.post(
                    provider.url,
                    json={"jsonrpc": "2.0", "id": 2, "method": "eth_call", "params": [{"to": to, "data": data}, "latest"]},
                    timeout=self.timeout_seconds,
                )
                payload = balance_resp.json() or {}
                if "error" in payload:
                    raise RuntimeError(f"eth_call error from {provider.name}: {payload['error']}")
                result = payload.get("result")
                if not _is_valid_hex_uint(result):
                    raise ValueError(f"malformed/out-of-range result from {provider.name}: {result!r}")

                state.record_success()
                return provider.name, result
            except Exception as e:  # noqa: BLE001 - deliberately broad: any failure here is "this provider didn't vote"
                last_error = e
                if attempt < attempts - 1:
                    await asyncio.sleep(0.3 * (attempt + 1))
                    continue

        state.record_failure()
        logger.warning("rpc_consensus: %s failed after retries: %s", provider.name, last_error)
        return provider.name, None

    async def eth_call_balance_of(self, client: httpx.AsyncClient, contract_address: str, holder_address: str) -> ConsensusResult:
        """The one real read this system needs consensus on: an ERC20
        balanceOf(holder) call against a stablecoin contract, used to decide
        whether a merchant order/invoice is confirmed."""
        padded_holder = holder_address.lower().replace("0x", "").zfill(64)
        data = _ERC20_BALANCE_OF_SELECTOR + padded_holder

        results = await asyncio.gather(
            *(self._query_one(client, p, contract_address, data) for p in self.providers)
        )

        votes: dict[str, list[str]] = {}  # raw_hex -> [provider names that returned it]
        unavailable: list[str] = []
        for name, raw in results:
            if raw is None:
                unavailable.append(name)
                continue
            votes.setdefault(raw, []).append(name)

        responded = len(self.providers) - len(unavailable)

        if not votes:
            logger.warning("rpc_consensus: no provider returned a usable result this round (%s/%s unavailable)", len(unavailable), len(self.providers))
            return ConsensusResult(value=None, reached=False, unavailable=unavailable, responded=responded, total_providers=len(self.providers))

        winning_hex, winning_voters = max(votes.items(), key=lambda kv: len(kv[1]))
        disagreeing = {name: raw for raw, names in votes.items() if raw != winning_hex for name in names}

        if disagreeing:
            logger.error(
                "rpc_consensus: DISAGREEMENT on balanceOf(%s) at %s - majority=%s (%d votes), dissenters=%s",
                holder_address, contract_address, winning_hex, len(winning_voters), disagreeing,
            )

        reached = len(winning_voters) >= self.quorum
        value = int(winning_hex, 16) if (reached and winning_hex not in ("", "0x")) else (0 if reached else None)

        return ConsensusResult(
            value=value,
            reached=reached,
            agreeing_providers=winning_voters,
            disagreeing=disagreeing,
            unavailable=unavailable,
            responded=responded,
            total_providers=len(self.providers),
        )
