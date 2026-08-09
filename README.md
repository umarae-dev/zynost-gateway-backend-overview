# Zynost Pay — Gateway API

Non-custodial crypto payment gateway backend for developers. Lets any merchant accept USDT/USDC directly to their own wallet — Zynost never custodies funds.

**Live API:** https://api.zynost.com
**Docs:** https://pay.zynost.com/docs

## What it does

- Generates a unique, watch-only receive address per checkout, derived from the merchant's own extended public key (xpub) — no private keys ever touch our servers.
- Verifies on-chain payments across multiple independent RPC providers before marking an order paid, so no single infrastructure provider can spoof a payment.
- Fires signed webhooks (HMAC-SHA256) the instant a payment confirms.
- Supports Ethereum, BSC, and Polygon, with an optional ERC-4337 gasless-checkout path so customers without native gas can still pay.

## Stack

FastAPI · PostgreSQL (SQLAlchemy async) · Docker · multi-chain RPC consensus · BIP32/BIP44 HD wallet derivation

## Status

In active production use, processing live payments for real merchants.

---

This repository is a public overview of a closed-source production system. Source code is not published here — the same practice most payment infrastructure providers (Stripe, Coinbase Commerce, etc.) follow for their core services.
