"""On-chain payment detection — ported from tradeos-backend's
payment_service.py (same 'ask the chain itself via a free public RPC,
hold no keys' approach), simplified to mainnet-only since merchants
integrate with real money from day one, the same way Coinbase Commerce
does. No API key/indexer dependency anywhere in this module."""
import logging
from decimal import Decimal
import httpx

from app.core.config import settings
from app.services.rpc_consensus import RpcConsensusValidator, build_evm_providers_for_chain

logger = logging.getLogger("gateway")

# Real-world friction this accounts for: a customer paying FROM an
# exchange withdrawal (the common case for gasless funding too) has the
# exchange's own withdrawal fee deducted from the requested amount before
# it ever reaches the chain - typically ~1% on a stablecoin withdrawal,
# nothing to do with Zynost or gas at all. 0.999 only tolerated rounding,
# not a real fee like that, so a customer sending exactly what they were
# told to pay could still land "underpaid" and stuck. Loosened to 0.99 to
# absorb a typical exchange fee without opening the door to meaningfully
# short payments - still a genuine, deliberate business tradeoff, not a
# rounding allowance.
_MIN_PAYMENT_FRACTION = Decimal("0.99")

_EVM_RPCS = {
    "binance-smart-chain": "https://bsc-rpc.publicnode.com",
    "ethereum": "https://ethereum-rpc.publicnode.com",
    "polygon-pos": "https://polygon-bor-rpc.publicnode.com",
}
_ERC20_BALANCE_OF_SELECTOR = "0x70a08231"
_DEFAULT_STABLECOIN_DECIMALS = 6

_STABLECOIN_CONTRACTS = {
    "ethereum": {
        "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
    },
    "binance-smart-chain": {
        "0x55d398326f99059ff775485246999027b3197955": "USDT",
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": "USDC",
    },
    "polygon-pos": {
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": "USDT",
        "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": "USDC",
    },
}
_EVM_STABLECOIN_DECIMALS = {"binance-smart-chain": 18, "ethereum": 6, "polygon-pos": 6}
_CHAIN_DISPLAY_NAME = {"ethereum": "ethereum", "binance-smart-chain": "bsc", "polygon-pos": "polygon"}
_CHAIN_IDS = {"ethereum": 1, "binance-smart-chain": 56, "polygon-pos": 137}


_SOLANA_STABLECOIN_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
}
_SOLANA_RPC = "https://api.mainnet-beta.solana.com"
_SOLANA_STABLECOIN_DECIMALS = 6


def wallet_connect_meta() -> dict:
    """Everything the browser 'Connect Wallet' bridge on Zynost Pay's
    checkout needs to build a real transfer client-side, without
    hardcoding any chain-specific constant of its own — same idea as
    tradeos-backend's payment_service.wallet_connect_meta(), single source
    of truth stays server-side. EVM chains keyed by display name (ethereum/
    bsc/polygon) to match paid_chain's own values everywhere else in this
    module, not the internal ethereum/binance-smart-chain/polygon-pos keys.
    `solana` mirrors the same shape (mint address per asset, decimals, RPC)
    so the Connect Wallet bridge's Solana path never needs its own copy of
    these constants either."""
    evm_chains = {}
    for chain, chain_id in _CHAIN_IDS.items():
        display = _CHAIN_DISPLAY_NAME[chain]
        evm_chains[display] = {
            "chain_id_hex": hex(chain_id),
            "contracts": {asset: address for address, asset in _STABLECOIN_CONTRACTS[chain].items()},
            "decimals": _EVM_STABLECOIN_DECIMALS.get(chain, _DEFAULT_STABLECOIN_DECIMALS),
        }
    return {
        "evm_chains": evm_chains,
        "solana": {
            "mints": {asset: mint for mint, asset in _SOLANA_STABLECOIN_MINTS.items()},
            "decimals": _SOLANA_STABLECOIN_DECIMALS,
            "rpc": _SOLANA_RPC,
        },
        # Empty until a real cloud.reown.com Project ID is configured — the
        # client-side wallet_connect.js bridge hides the "WalletConnect"
        # picker entry entirely in that case, same graceful-degradation
        # convention as every other optional integration in this codebase.
        "walletconnect_project_id": settings.WALLETCONNECT_PROJECT_ID,
    }


async def check_evm_payment(client: httpx.AsyncClient, evm_address: str, amount_usd) -> dict | None:
    """Checks every configured EVM chain for a CURRENT USDT/USDC balance at
    evm_address meeting the invoiced amount — via the multi-RPC consensus
    validator (see rpc_consensus.py). This is mainnet-only real money for
    OTHER merchants' customers, so it gets the same "never trust a single
    RPC" treatment as tradeos-backend's own subscription checkout: quorum
    agreement across independent providers (publicnode + Ankr's free
    endpoint at minimum) is required before a balance is trusted, and a
    round that can't reach quorum is treated as "not yet confirmed, retry
    next poll" rather than silently trusting whichever provider answered."""
    for chain, rpc_url in _EVM_RPCS.items():
        contracts = _STABLECOIN_CONTRACTS.get(chain, {})
        if not contracts:
            continue
        decimals = _EVM_STABLECOIN_DECIMALS.get(chain, _DEFAULT_STABLECOIN_DECIMALS)
        validator = RpcConsensusValidator(
            build_evm_providers_for_chain(chain, rpc_url), expected_chain_id=_CHAIN_IDS[chain]
        )

        for contract_address, asset in contracts.items():
            consensus = await validator.eth_call_balance_of(client, contract_address, evm_address)
            if not consensus.reached:
                logger.warning(
                    "Gateway payment poll: RPC consensus NOT reached for address=%s chain=%s asset=%s "
                    "(responded=%s/%s providers, unavailable=%s) — treating as unverified this round, will retry",
                    evm_address, chain, asset, consensus.responded, consensus.total_providers, consensus.unavailable,
                )
                continue
            raw_balance = consensus.value
            if raw_balance == 0:
                continue
            amount = Decimal(raw_balance) / (Decimal(10) ** decimals)
            if amount < Decimal(str(amount_usd)) * _MIN_PAYMENT_FRACTION:
                continue
            return {"chain": _CHAIN_DISPLAY_NAME[chain], "asset": asset, "amount": amount}
    return None


async def check_solana_payment(
    client: httpx.AsyncClient, solana_address: str, amount_usd, already_consumed: dict[str, Decimal],
) -> dict | None:
    """Checks solana_address's CURRENT on-chain token balance for each
    accepted stablecoin mint via getTokenAccountsByOwner.

    Every order for a given merchant shares that merchant's ONE
    payout_solana_address (see Merchant.solana_usdc_consumed's docstring
    for why: Solana has no practical watch-only HD derivation the way EVM's
    xpub does, so there's no way to hand each order its own unique
    address). That means a plain "balance >= this order's amount" check is
    unsafe: if two $20 orders are pending against the same address and only
    one customer actually paid, checking each order independently would
    satisfy BOTH of them from that single real payment.

    `already_consumed` is this merchant's already-claimed balance per asset
    (persisted on the Merchant row) - only the UNCLAIMED remainder
    (current balance minus already_consumed) can satisfy this order. The
    caller is responsible for persisting the returned "new_consumed" value
    back onto the merchant BEFORE checking the next pending order for the
    same merchant in the same pass, so two orders competing for one
    payment resolve FIFO (oldest first), never both."""
    if not solana_address:
        return None
    for mint, asset in _SOLANA_STABLECOIN_MINTS.items():
        try:
            resp = await client.post(
                _SOLANA_RPC,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [solana_address, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "finalized"}],
                },
            )
            accounts = (resp.json().get("result") or {}).get("value") or []
        except Exception:
            logger.warning("Gateway payment poll: Solana token-account check failed for %s", solana_address, exc_info=True)
            continue

        for account in accounts:
            parsed_info = (((account.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
            ui_amount = (parsed_info.get("tokenAmount") or {}).get("uiAmount")
            if ui_amount is None:
                continue
            balance = Decimal(str(ui_amount))
            consumed = already_consumed.get(asset, Decimal("0"))
            available = balance - consumed
            expected = Decimal(str(amount_usd))
            if available < expected * _MIN_PAYMENT_FRACTION:
                continue
            # Claim exactly this order's own amount, not the whole
            # available delta - any genuine overpayment stays unclaimed
            # and available for a future order, rather than being silently
            # swallowed by whichever order happened to be checked first.
            return {"chain": "solana", "asset": asset, "amount": expected, "new_consumed": consumed + expected}
    return None
