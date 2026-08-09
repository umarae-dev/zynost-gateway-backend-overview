# Zynost Pay — Gateway API

**Built so we can't touch your money.**

Zynost Pay is a non-custodial crypto payment gateway. A merchant gives us an extended public key (xpub) — never a private key, never a seed phrase — and we derive a fresh, unique receive address for every single order from it. The funds land directly in the merchant's own wallet the moment a customer pays. There's no payout step, no minimum withdrawal, no balance sitting in our custody waiting to be released, because we never hold it in the first place.

**Live:** https://pay.zynost.com

## Why it exists

Zynost started as a decision-intelligence platform for crypto traders, not a payments company. When it came time to charge for subscriptions, every processor we looked at wanted custody of the funds first — hold the money, then release it on their schedule. That wasn't acceptable for a product built on the premise that your assets should stay in your own wallet. So we built our own non-custodial payment layer, ran it internally against real subscription revenue until it was genuinely battle-tested, and then opened it up as its own product. This repository describes that gateway.

## How a payment actually gets confirmed

1. **Address derivation.** Every order gets its own address, derived on the fly from the merchant's xpub via BIP32 (`m/0/{index}`) — no shared addresses, no address reuse, and mathematically no way to spend from it even if the derivation logic were compromised, because there's no private key material on the server to begin with.
2. **On-chain verification, by committee.** We don't trust a single RPC provider to tell us a payment happened. Every chain check goes out to at least two independent providers (a free baseline plus Ankr, with Alchemy and QuickNode joining the pool when configured — up to four), and a quorum of more than half has to agree before a payment counts. A provider reporting the wrong chain ID gets its vote thrown out entirely, not silently corrected. Disagreements between providers are logged as errors, never smoothed over.
3. **Realistic tolerance, not exact-match paranoia.** A payment is accepted at 99.9% or more of the invoiced amount, because real transfers lose fractions of a cent to rounding and gas-adjacent slippage — rejecting those would just create support tickets for money that genuinely arrived.
4. **A background watcher, not a webhook race.** Every pending order is re-checked on a 60-second cycle (oldest orders first, which matters for Solana — see below), independent of whether the customer's browser is even still open.
5. **Signed delivery.** The instant an order confirms, we POST a signed payload to the merchant's webhook URL, HMAC-SHA256 over the raw body, header `X-Zynost-Signature`. The merchant verifies it with the same secret we gave them at signup — no shared platform-level trust required.

## Chains and assets

Ethereum, BSC, and Polygon share one address format, so a single derived address is valid across all three. USDT and USDC are supported on each. Solana is supported too, though it works a little differently — there's no watch-only HD derivation standard for Solana the way there is for EVM chains, so a merchant's Solana orders share one static address, and the polling logic resolves multiple pending orders against it fairly, oldest first.

## Security choices that actually matter

- **API keys are never stored in a recoverable form.** Only a SHA-256 hash lives in the database. The full key is shown exactly once, at creation or regeneration — there is no "forgot my key" recovery, by design.
- **Regenerating a key doesn't break production instantly.** The old key keeps working for a 24-hour grace period so a merchant can roll credentials across their systems without a hard cutover.
- **Webhook secrets rotate immediately**, no grace period — a leaked signing secret should stop being useful the second you rotate it.
- **Changing a payout xpub is never self-serve.** It's the one action that redirects where every future payment lands, so it goes through an explicit request-and-admin-approval flow instead of an instant settings toggle.
- **Admin access requires both an allowlisted account and two-factor authentication enabled** — enforced server-side, not just hidden in the UI.

## Billing, running on its own rails

Merchants choose flat billing (50 orders/month free, then $19/month flat for unlimited) or pay-as-you-go (no cap, 0.3% of paid volume). A mode switch is queued for the next billing cycle rather than applied instantly, so nobody can dodge a cap they just hit by flipping modes mid-month. And Zynost's own subscription revenue — the thing that started this whole project — runs through this exact same non-custodial gateway, using Zynost's own xpub as plain public-key material. We don't have a separate, more-trusted payment path for ourselves.

## Gasless checkout (BSC mainnet)

For customers who want to pay but don't hold any native gas token, Zynost Pay can sponsor the transaction through an ERC-4337 smart account, built on eth-infinitism's audited `SimpleAccount` implementation. The owner key is deterministically derived from a single free wallet signature — nothing is stored — and the resulting smart-wallet address is reused across every future gasless checkout by that customer, on any merchant using Zynost Pay. Sponsorship is rate-limited and circuit-broken off-chain, and independently capped on-chain by the [Zynost Paymaster contract](https://github.com/umarae-dev/zynost-paymaster-overview).

## Stack

FastAPI · PostgreSQL (async SQLAlchemy) · Docker, behind Caddy for automatic HTTPS · multi-provider RPC consensus · BIP32/BIP44 HD wallet derivation · ERC-4337 Account Abstraction

## Status

In active production use, processing live payments for real merchants — including Zynost's own subscription business.

---

This repository is a public overview of a closed-source production system handling real customer funds. Source code isn't published here — the same practice most payment infrastructure providers follow for their core services.
