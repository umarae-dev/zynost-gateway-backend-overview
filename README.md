# Zynost Gateway Backend — Production-Safe Core

> Real production-derived, non-custodial crypto gateway core extracted from Zynost Pay without production credentials, customer data, or live signer infrastructure.

This repository now publishes the production-safe gateway logic used to derive merchant-controlled receive addresses, verify stablecoin settlement, reconcile merchant orders, and protect multi-tenant payment attribution.

**Primary BNB use case:** BNB Smart Chain USDT/USDC checkout  
**Runtime:** Python 3.12  
**Architecture:** non-custodial, watch-only address derivation + on-chain verification  
**Related on-chain gas sponsorship:** `umarae-dev/zynost-paymaster-overview`

## What is real production source here

The following files are copied from the private production gateway rather than rewritten as demos:

- `app/services/bip32_lite.py` — BIP32/secp256k1 child derivation primitives;
- `app/services/wallet_derivation.py` — watch-only EVM address derivation from merchant xpubs;
- `app/services/rpc_consensus.py` — independent-provider quorum, chain-ID validation, circuit breaking and fail-safe settlement reads;
- `app/services/payment_check.py` — BSC/Ethereum/Polygon stablecoin verification plus Solana consumed-balance attribution;
- `app/services/merchant_service.py` — API-key generation/rotation, webhook signing, atomic order-index reservation, order creation and FIFO reconciliation;
- production merchant/device models and supporting security/database/push modules required by that core;
- production regression tests covering the published behavior.

The public repository intentionally does **not** publish live credentials, user/customer records, production `.env` files, operational signer/bundler services, or temporary production migration scripts.

## Non-custodial design

For EVM checkouts, a merchant provides an **xpub**, never a seed phrase or spend-capable private key. The gateway derives a fresh address under that public key for each order. It can observe and reconcile funds, but it cannot spend from those addresses.

```text
merchant xpub
    │
    └── m/0/order-index
              │
              ▼
   merchant-controlled EVM address
              │
      USDT / USDC arrives
              │
              ▼
   multi-RPC settlement verifier
              │
              ▼
      paid order + HMAC webhook
```

## BNB Smart Chain settlement

BNB Smart Chain is a first-class production path. The published source contains the accepted BSC USDT/USDC contracts, chain ID `56`, and the production rule that BSC stablecoins use **18 decimals** in this integration.

An EVM payment is not confirmed from a single provider's answer. `RpcConsensusValidator` requires at least two independent providers, verifies each provider's reported chain ID, rejects malformed responses, rate-limits providers, opens a circuit after repeated failures, and fails safe when quorum is unavailable.

The default no-key provider pool still includes PublicNode plus Ankr's public endpoint. Optional Alchemy, QuickNode and keyed Ankr endpoints can expand the pool through environment configuration.

## Payment policy

The production payment checker accepts a qualifying observed balance at **99% or more** of the invoice amount. This is an explicit checkout policy intended to absorb common exchange withdrawal fees; the production tests verify that a typical 1% shortfall can confirm while a materially larger shortfall remains rejected.

For Solana, multiple orders can share a merchant payout address. The published production code therefore tracks how much balance has already been consumed by earlier orders and processes pending orders FIFO, preventing one real payment from satisfying multiple orders.

## Merchant security

The published merchant core includes:

- cryptographically random `zg_live_...` API keys;
- SHA-256 storage of high-entropy API-key secrets rather than plaintext recovery;
- a 24-hour previous-key grace window for safe rotation;
- HMAC-SHA256 signed webhook payloads;
- independent webhook-secret rotation;
- atomic PostgreSQL `UPDATE ... RETURNING` reservation of each merchant's next derivation index;
- free/flat/volume billing-cycle controls used by the production order core.

## Tests

The public test suite is copied from production and includes regression coverage for:

- full RPC agreement and 2-of-3 majority;
- quorum failure and fail-safe behavior;
- provider timeouts and wrong-chain responses;
- malformed RPC values;
- circuit breaking and per-provider rate limiting;
- BSC 18-decimal wallet metadata;
- real matching balances and underpayment policy;
- Solana consumed-balance watermarking;
- FIFO protection against double fulfillment;
- API-key grace-period boundaries;
- atomic EVM order-index reservation.

Run locally:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/check_public_repo.py
python -m compileall -q app
pytest -q
```

No production credential is required for these tests.

## Public / private boundary

### Published

- production-safe settlement and merchant-order core;
- watch-only derivation code;
- public chain/token constants;
- production data models required by the published core;
- production regression tests;
- secret-free configuration schema and `.env.example`;
- CI and repository secret guard.

### Not published

- production database or user data;
- actual JWT, SMTP, Firebase, RPC-provider or WalletConnect credentials;
- spend-capable wallet material;
- Paymaster verifying-signer material;
- bundler credentials and operational gas-sponsorship abuse controls;
- the production User mirror because its source contains an account-specific identifier not needed by this public core;
- temporary one-off migration/check scripts;
- unrelated private application routes and operational runbooks.

See `PUBLIC_PRIVATE_BOUNDARY.md` and `SECURITY.md`.

## ERC-4337 boundary

Gas sponsorship is split deliberately. The inspectable on-chain Paymaster and its production tests live in the separate `zynost-paymaster-overview` repository. This gateway repository does not publish the live off-chain signer/bundler operational path or its credentials.

## CI

GitHub Actions installs the public test environment on Python 3.12, runs the repository secret guard, compiles the published Python source, imports the gateway core, and executes all published production tests.

## License

The public mirror is released under Apache-2.0. Third-party dependencies retain their own licenses.

## Status

**Production-safe gateway core is publicly inspectable and independently testable. Live operational infrastructure remains separately operated and private.**
