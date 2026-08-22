# Zynost Pay — Non-Custodial Crypto Gateway API

> **Payment infrastructure designed so the gateway can verify money without ever gaining the power to move it.**

Zynost Pay is a non-custodial crypto payment gateway for developers and merchants. A merchant connects **public wallet information only** — never a seed phrase and never a spend-capable private key — and the gateway creates payment orders, watches supported chains, confirms settlement and delivers signed webhooks back to the merchant application.

**Live:** https://pay.zynost.com  
**Primary BNB use case:** BSC stablecoin checkout + ERC-4337 gas sponsorship  
**Backend:** FastAPI + PostgreSQL  
**Custody model:** Merchant-controlled funds

---

## The core idea

Traditional crypto gateways often receive customer funds into provider-controlled wallets and pay merchants later. Zynost Pay takes a different approach.

For EVM payments, a merchant supplies an **extended public key (xpub)**. Zynost can derive fresh receive addresses from that public key, but an xpub contains no private spending key. Each checkout therefore receives a unique merchant-controlled address while Zynost remains able to watch the chain and reconcile the order.

```text
Customer
   │
   │ sends USDT / USDC
   ▼
Unique merchant-controlled address
   │
   ├──── Zynost watches chain state
   │
   ▼
Payment reaches confirmation policy
   │
   ▼
Signed merchant webhook
   │
   ▼
Merchant application fulfills order
```

There is no provider payout queue because Zynost is not holding a merchant balance waiting to release it. A merchant may later consolidate its own derived addresses, but that is movement of funds the merchant already controls — not a withdrawal from Zynost custody.

---

## Why BNB Smart Chain matters

BNB Smart Chain is a first-class network in Zynost Pay because it combines broad stablecoin usage with low transaction fees and EVM compatibility.

The BSC integration is used for more than simply accepting a token transfer:

- USDT / USDC checkout;
- unique merchant-controlled EVM receive addresses;
- independent RPC verification;
- WalletConnect / connected-wallet payment flows;
- ERC-4337 smart accounts;
- gas-sponsored checkout through **Zynost Paymaster**.

This makes BNB Chain a practical execution layer for merchants rather than just another chain name in a selector.

---

## Payment lifecycle

### 1. Merchant creates an order

An authenticated merchant requests a checkout with an amount and optional order reference/description.

For EVM chains, the service atomically reserves the merchant's next derivation index before creating the address. The index allocation is handled at the database level so simultaneous checkout requests cannot silently receive the same child address.

### 2. A fresh EVM address is derived

The address is derived watch-only from the merchant's xpub using BIP32 public-child derivation.

```text
merchant xpub
    │
    ▼
change branch
    │
    ▼
unique order index
    │
    ▼
merchant-controlled EVM receive address
```

Ethereum, BNB Smart Chain and Polygon use the same secp256k1 address format, so the same derived EVM address can be observed across supported EVM networks.

### 3. Zynost verifies chain state

The gateway does **not** accept one RPC provider as an oracle for real-money settlement.

For supported EVM payment checks, multiple independent RPC providers are queried and the gateway requires quorum agreement on the relevant on-chain value. Every participating provider is also checked against the expected chain ID.

If quorum cannot be reached, the system fails safe: the payment remains unverified for that polling round instead of trusting whichever endpoint happened to answer.

### 4. Practical underpayment handling

The current production policy accepts a payment once the observed eligible balance reaches at least **99% of the invoiced amount**. This is a deliberate checkout policy for real-world cases such as exchange withdrawal fees rather than pretending every transfer arrives with mathematically exact decimal equality.

### 5. Signed fulfillment signal

Once an order is confirmed, Zynost sends the merchant a webhook signed with HMAC-SHA256. The receiving application verifies the signature before fulfilling the order.

---

## Multi-RPC verification

A payment processor should not have a single infrastructure provider whose incorrect answer can decide whether a merchant gets paid.

Zynost's EVM verifier is therefore designed around:

- multiple independent RPC providers;
- configurable quorum rather than first-response-wins;
- chain-ID validation for every provider;
- malformed-response rejection;
- per-provider rate limiting;
- circuit breaking for repeatedly failing providers;
- explicit logging when providers disagree;
- fail-safe behavior when consensus is unavailable.

This architecture treats RPC disagreement as something to investigate, not something to silently average away.

---

## API and merchant security

### API keys

Merchant API keys are generated with cryptographic randomness. The production system stores only a hash of the active secret rather than a recoverable plaintext copy.

Key rotation includes a controlled grace window so a merchant can roll credentials across production systems without instantly breaking every existing integration.

### Webhook signing

Webhook payloads are signed per merchant using HMAC-SHA256. Webhook secrets can be rotated independently from API credentials.

### Payout-address changes

Changing the public wallet information that determines where future orders settle is treated as a high-risk account action rather than an ordinary instant settings toggle. The production workflow uses an explicit change-request/review path.

### Administrative controls

Sensitive administrative functions are protected server-side rather than merely hidden in the interface.

---

## Gasless checkout on BNB Smart Chain

A customer may have the required stablecoin but no native BNB for gas. Zynost Pay can route that case through an ERC-4337 smart account and the separate **[Zynost Paymaster](https://github.com/umarae-dev/zynost-paymaster-overview)** sponsorship layer.

High-level flow:

```text
Customer wallet
    │
    │ free signature / intent
    ▼
ERC-4337 smart account
    │
    │ stablecoin balance verified
    ▼
Server constructs order-bound transfer
    │
    ▼
Paymaster policy + on-chain limits
    │
    ▼
Bundler / EntryPoint
    │
    ▼
BNB Smart Chain
    │
    ▼
Merchant-controlled order address
```

Important invariants:

- the server does not accept a client-supplied payment destination as authoritative;
- the order destination is taken from the merchant's actual order record;
- smart-account funding is re-verified from chain state;
- sponsorship pays network gas, not the customer's purchase amount;
- the paymaster cannot spend merchant payment funds;
- the gasless path ultimately settles into the same reconciliation pipeline as a normal payment.

The production implementation intentionally keeps signer credentials, bundler configuration and operational abuse controls out of this public repository.

---

## Supported payment networks

Current gateway architecture includes:

| Network | Model | Stablecoins |
|---|---|---|
| **BNB Smart Chain** | Unique EVM address + gasless option | USDT, USDC |
| Ethereum | Unique EVM address | USDT, USDC |
| Polygon | Unique EVM address | USDT, USDC |
| Solana | Merchant payout address + balance attribution | USDT, USDC |

Solana differs from the EVM path because it does not use the same watch-only xpub child-address model. The production gateway therefore tracks already-attributed balance so one incoming payment cannot accidentally satisfy multiple pending orders that share a merchant address.

---

## Billing

Merchants can use a free evaluation tier and choose between two commercial models as they scale:

- **Flat:** $19/month for unlimited orders after the free allowance;
- **Volume:** 0.3% of successfully paid volume.

Billing-mode changes are applied at billing-cycle boundaries rather than allowing mid-cycle switching to bypass the active plan's limits.

Zynost's own subscription checkout also uses the same non-custodial gateway architecture — there is no privileged custodial payment rail reserved for the parent product.

---

## Trust boundaries

| Component | What it can do | What it cannot do |
|---|---|---|
| Gateway backend | Create orders, derive public addresses, verify chain state | Spend from merchant xpub-derived addresses |
| RPC providers | Report blockchain state | Single-handedly confirm an EVM payment when quorum is required |
| Paymaster | Sponsor eligible transaction gas | Spend merchant/customer payment balances |
| Merchant webhook secret | Authenticate Zynost webhook payloads | Move on-chain funds |
| Customer wallet | Authorize its own transaction | Change the gateway's server-side merchant destination |

---

## Production vs. public repository boundary

This repository is a **public technical overview** of a production payment system. It is intentionally not a mirror of the private deployment repository.

### Public here

- architecture;
- custody model;
- BNB integration model;
- payment lifecycle;
- security invariants;
- RPC-consensus design;
- merchant/API concepts;
- gasless-checkout architecture.

### Kept private

- private keys, seed phrases and signing credentials;
- database credentials;
- merchant/customer data;
- production environment variables;
- internal operational runbooks;
- abuse-detection thresholds where disclosure would weaken production defenses;
- proprietary backend implementation not required for public verification.

No production credential should ever be committed to this repository.

---

## Open-source / BNB developer track

The commercial gateway remains a private production service, but Zynost is preparing a **separately scoped open-source BNB component** for developer/hackathon use.

That component is intended to be reproducible with local/testnet configuration and inspectable without requiring any Zynost production credential. The goal is to contribute useful BNB-native technology without turning a live payment backend into unnecessary public attack surface.

This repository will link to the open-source component once it is ready.

---

## Broader Zynost ecosystem

```text
Zynost Intelligence
        │
        ▼
Zynost Wallet
        │
        ├───────────────┐
        ▼               ▼
   Zynost Pay      UQX ecosystem
        │
        ▼
 Zynost Paymaster
        │
        ▼
  BNB Smart Chain
```

The broader direction is an integrated crypto stack covering **decision intelligence, self-custody, merchant payments, account abstraction and a BNB-native community/token layer**.

---

## Technology

FastAPI · PostgreSQL / async SQLAlchemy · Docker · BIP32 public-child derivation · secp256k1 · multi-provider JSON-RPC consensus · HMAC-SHA256 webhooks · WalletConnect · ERC-4337 Account Abstraction · BNB Smart Chain

---

## Status

**Active production infrastructure.**

Zynost Pay is used for live payment flows, including Zynost's own subscription checkout. Security hardening, operational monitoring and independent review remain ongoing priorities as the gateway expands.

For responsible security reporting, see [`SECURITY.md`](SECURITY.md).
