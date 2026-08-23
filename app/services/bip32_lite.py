"""Self-contained BIP32 (secp256k1) implementation used by the payment
gateway — deliberately built on `ecdsa` (pure Python, zero C-extension
dependency) instead of the `bip32`/`coincurve` packages, after coincurve's
source build failed on Railway's build image (no prebuilt wheel for that
Python/platform combination, and its build script itself errors under a
newer scikit-build-core: "Use build.verbose instead of cmake.verbose").
A pure-Python implementation has no wheel to be missing, on any platform.

Implements just the two operations this app needs, per BIP32 (see
https://github.com/bitcoin/bips/blob/master/bip-0032.mediawiki):
  - CKDpriv (hardened) + xpub serialization — used OFFLINE by
    scripts/generate_payment_wallet_keys.py to turn a BIP39 seed into the
    account-level extended public key.
  - CKDpub (non-hardened only, which is all this app ever needs) — used by
    wallet_derivation.py to mint a fresh receive address per invoice from
    that xpub, with zero private key material anywhere in the process.

Verified against python-bip32 (which itself wraps libsecp256k1/coincurve)
during development: identical xpub, identical derived addresses for the
same seed/path — this file is the drop-in replacement, not a reinvention of
the algorithm.
"""
import hashlib
import hmac

import base58
from ecdsa import SECP256k1
from ecdsa.ellipticcurve import Point

_CURVE = SECP256k1
_ORDER = _CURVE.order
_GENERATOR = _CURVE.generator
_P = _CURVE.curve.p()

_XPRV_VERSION = bytes.fromhex("0488ADE4")
_XPUB_VERSION = bytes.fromhex("0488B21E")
_HARDENED_OFFSET = 0x80000000


def _hmac_sha512(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha512).digest()


def _hash160(data: bytes) -> bytes:
    return hashlib.new("ripemd160", hashlib.sha256(data).digest()).digest()


def _compress_point(point: Point) -> bytes:
    prefix = b"\x03" if point.y() % 2 else b"\x02"
    return prefix + point.x().to_bytes(32, "big")


def _decompress_point(data: bytes) -> Point:
    prefix, x_bytes = data[0], data[1:]
    x = int.from_bytes(x_bytes, "big")
    y_squared = (pow(x, 3, _P) + 7) % _P
    y = pow(y_squared, (_P + 1) // 4, _P)  # valid sqrt since p % 4 == 3 for secp256k1
    if (y % 2 == 0) != (prefix == 0x02):
        y = _P - y
    return Point(_CURVE.curve, x, y)


def privkey_to_pubkey_compressed(privkey_int: int) -> bytes:
    point = _GENERATOR * privkey_int
    return _compress_point(point)


def _b58check_encode(payload: bytes) -> str:
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return base58.b58encode(payload + checksum).decode()


def _b58check_decode(encoded: str) -> bytes:
    raw = base58.b58decode(encoded)
    payload, checksum = raw[:-4], raw[-4:]
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if checksum != expected:
        raise ValueError("Invalid base58check checksum")
    return payload


def master_key_from_seed(seed: bytes) -> tuple[int, bytes]:
    """Returns (master_privkey_int, master_chaincode) per BIP32 section
    'Master key generation'."""
    digest = _hmac_sha512(b"Bitcoin seed", seed)
    return int.from_bytes(digest[:32], "big"), digest[32:]


def ckd_priv(parent_privkey: int, parent_chaincode: bytes, index: int) -> tuple[int, bytes]:
    """CKDpriv — works for both hardened (index >= 0x80000000) and
    non-hardened indices, since the private key is available either way."""
    if index >= _HARDENED_OFFSET:
        data = b"\x00" + parent_privkey.to_bytes(32, "big") + index.to_bytes(4, "big")
    else:
        data = privkey_to_pubkey_compressed(parent_privkey) + index.to_bytes(4, "big")
    digest = _hmac_sha512(parent_chaincode, data)
    il, child_chaincode = digest[:32], digest[32:]
    child_privkey = (int.from_bytes(il, "big") + parent_privkey) % _ORDER
    return child_privkey, child_chaincode


def ckd_pub(parent_pubkey_compressed: bytes, parent_chaincode: bytes, index: int) -> tuple[bytes, bytes]:
    """CKDpub — public-key-only child derivation. Only defined for
    non-hardened indices (per BIP32; a hardened child's key material can't
    be derived without the parent private key), which is exactly what this
    app's per-invoice address derivation (m/0/i) uses."""
    if index >= _HARDENED_OFFSET:
        raise ValueError("CKDpub is undefined for hardened indices — need the private key for those.")
    data = parent_pubkey_compressed + index.to_bytes(4, "big")
    digest = _hmac_sha512(parent_chaincode, data)
    il, child_chaincode = digest[:32], digest[32:]
    il_point = _GENERATOR * int.from_bytes(il, "big")
    parent_point = _decompress_point(parent_pubkey_compressed)
    child_point = il_point + parent_point
    return _compress_point(child_point), child_chaincode


def derive_priv_path(seed: bytes, path: str) -> tuple[int, bytes, bytes, int, int]:
    """Derives down `path` (e.g. "m/44'/60'/0'") from a BIP39 seed.
    Returns (privkey, chaincode, parent_pubkey_compressed, depth, child_number)
    — the parent pubkey (for computing this key's own fingerprint-of-parent
    when serializing) and depth/child_number are needed for a spec-correct
    xpub/xprv serialization."""
    privkey, chaincode = master_key_from_seed(seed)
    parent_pubkey = privkey_to_pubkey_compressed(privkey)  # only used if path is "m"
    depth = 0
    child_number = 0
    for segment in path.strip().split("/"):
        if segment == "m" or segment == "":
            continue
        hardened = segment.endswith("'")
        index = int(segment[:-1] if hardened else segment)
        if hardened:
            index += _HARDENED_OFFSET
        parent_pubkey = privkey_to_pubkey_compressed(privkey)
        privkey, chaincode = ckd_priv(privkey, chaincode, index)
        depth += 1
        child_number = index
    return privkey, chaincode, parent_pubkey, depth, child_number


def serialize_xpub(pubkey_compressed: bytes, chaincode: bytes, parent_pubkey_compressed: bytes, depth: int, child_number: int) -> str:
    parent_fingerprint = _hash160(parent_pubkey_compressed)[:4] if depth > 0 else b"\x00\x00\x00\x00"
    payload = (
        _XPUB_VERSION
        + depth.to_bytes(1, "big")
        + parent_fingerprint
        + child_number.to_bytes(4, "big")
        + chaincode
        + pubkey_compressed
    )
    return _b58check_encode(payload)


def decompress_pubkey(pubkey_compressed: bytes) -> tuple[bytes, bytes]:
    """Returns (x, y) as 32-byte big-endian values — e.g. for computing an
    Ethereum-style address, which hashes the uncompressed (x || y) point."""
    point = _decompress_point(pubkey_compressed)
    return point.x().to_bytes(32, "big"), point.y().to_bytes(32, "big")


def parse_xpub(xpub: str) -> tuple[bytes, bytes]:
    """Returns (pubkey_compressed, chaincode) — the only two fields this
    app needs to derive further (non-hardened) child addresses."""
    payload = _b58check_decode(xpub)
    if payload[:4] != _XPUB_VERSION:
        raise ValueError("Not a mainnet xpub (wrong version bytes).")
    chaincode = payload[13:45]
    pubkey_compressed = payload[45:78]
    return pubkey_compressed, chaincode
