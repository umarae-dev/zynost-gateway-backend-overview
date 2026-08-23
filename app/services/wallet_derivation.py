"""Watch-only EVM address derivation — ported from tradeos-backend's
module of the same name, with the Zynost-own-xpub default removed since
every caller here always supplies a merchant's own xpub explicitly (this
service has no wallet of its own to derive from). secp256k1 (Ethereum/
BSC/Polygon) supports BIP32's CKDpub: deriving unlimited child public
keys/addresses from a parent extended public key alone, so a fresh,
unique, never-reused receive address can be minted per order forever,
with zero ability to ever spend from any of them."""
from Crypto.Hash import keccak
from app.services import bip32_lite

_RECEIVE_CHANGE_LEVEL = 0


def derive_evm_address(index: int, xpub: str) -> str:
    """Returns the lowercase 0x... address at m/{change}/{index} under the
    given account xpub. Same address is valid to receive USDT/USDC on
    Ethereum, BSC, and Polygon — they're the same curve and address
    format, just different networks."""
    account_pubkey, account_chaincode = bip32_lite.parse_xpub(xpub)
    change_pubkey, change_chaincode = bip32_lite.ckd_pub(account_pubkey, account_chaincode, _RECEIVE_CHANGE_LEVEL)
    address_pubkey, _ = bip32_lite.ckd_pub(change_pubkey, change_chaincode, index)

    x_bytes, y_bytes = bip32_lite.decompress_pubkey(address_pubkey)
    digest = keccak.new(digest_bits=256)
    digest.update(x_bytes + y_bytes)
    return "0x" + digest.hexdigest()[-40:]
