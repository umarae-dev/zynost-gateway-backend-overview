"""wallet_connect_meta() feeds Zynost Pay's browser 'Connect Wallet' bridge
everything it needs to build a real ERC20 transfer client-side without
hardcoding any chain constant of its own - a wrong/missing entry here would
silently make the frontend send to the wrong contract or on the wrong
chain, so this is checked directly against payment_check's own private
tables (the actual source of truth for what's accepted on-chain)."""
from app.services import payment_check


def test_wallet_connect_meta_covers_every_accepted_display_chain():
    meta = payment_check.wallet_connect_meta()
    assert set(meta["evm_chains"].keys()) == {"ethereum", "bsc", "polygon"}


def test_wallet_connect_meta_bsc_uses_18_decimals_not_the_usual_6():
    # BSC's USDT/USDC are the one real exception - this was a live, reported
    # bug in the main app's own Connect Wallet flow before it hardcoded 6
    # everywhere; must never regress here either.
    meta = payment_check.wallet_connect_meta()
    assert meta["evm_chains"]["bsc"]["decimals"] == 18
    assert meta["evm_chains"]["ethereum"]["decimals"] == 6
    assert meta["evm_chains"]["polygon"]["decimals"] == 6


def test_wallet_connect_meta_contracts_match_the_real_accepted_addresses():
    meta = payment_check.wallet_connect_meta()
    for chain, addr, asset in [
        ("ethereum", "0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT"),
        ("bsc", "0x55d398326f99059ff775485246999027b3197955", "USDT"),
        ("polygon", "0xc2132d05d31c914a87c6611c10748aeb04b58e8f", "USDT"),
    ]:
        assert meta["evm_chains"][chain]["contracts"][asset].lower() == addr.lower()


def test_wallet_connect_meta_chain_id_hex_matches_real_mainnet_ids():
    meta = payment_check.wallet_connect_meta()
    assert meta["evm_chains"]["ethereum"]["chain_id_hex"] == hex(1)
    assert meta["evm_chains"]["bsc"]["chain_id_hex"] == hex(56)
    assert meta["evm_chains"]["polygon"]["chain_id_hex"] == hex(137)
