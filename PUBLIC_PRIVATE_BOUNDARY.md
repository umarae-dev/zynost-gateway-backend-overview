# Public / Private Boundary

This repository publishes a production-safe subset of the Zynost Gateway backend while deliberately excluding credentials, personal data, and live operational controls that would increase production attack surface.

## Published

- exact production BIP32 and watch-only EVM derivation source;
- exact production multi-RPC consensus and EVM/Solana payment-verification source;
- exact production merchant order/security core used by the published tests;
- production merchant and device-token models required by that core;
- production regression tests for RPC consensus, payment policy, BSC metadata, Solana attribution, API-key grace periods, atomic derivation-index reservation, and FIFO reconciliation;
- secret-free configuration schema and environment example;
- CI and repository secret scanning.

## Intentionally private

- real `.env` files and database credentials;
- JWT, SMTP, Firebase, RPC-provider, WalletConnect, bundler, or other service credentials;
- private keys, seed phrases, signing material, or spend-capable wallet data;
- Paymaster off-chain signer/bundler operational implementation and abuse-control internals;
- production customer, merchant, or user records;
- the production User mirror because its source contains an account-specific identifier unnecessary for this public core;
- one-off migration/check scripts and operational recovery runbooks;
- unrelated application routes and deployment-only infrastructure.

## Security principle

The gateway's non-custodial and settlement-verification properties do not depend on hiding the published algorithms. Public inspection is useful. Operational credentials, personal data, and controls whose disclosure would weaken a live service remain outside this mirror.

The public repository has its own clean history and does not expose the private production repository's commit history.
