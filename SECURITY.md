# Security Policy

Zynost Pay is payment infrastructure. Please treat security findings responsibly and do not test against live merchant/customer funds without explicit written authorization.

## What this public repository contains

This repository contains a production-safe subset of the real gateway backend: watch-only address derivation, multi-RPC settlement verification, merchant order/security logic, supporting models, and production regression tests.

It intentionally does **not** contain production credentials, private keys, seed phrases, customer/merchant data, live signing material, production database access, operational signer/bundler controls, or private runbooks.

## Reporting a vulnerability

If you believe you have found a vulnerability affecting Zynost Pay or its BNB/ERC-4337 infrastructure:

1. Do not publish exploit details before coordinated review.
2. Do not attempt to move funds, impersonate a merchant, drain sponsored gas, access another user's data or degrade production availability.
3. Include enough technical detail to reproduce the issue safely in a non-production environment.
4. Contact the Zynost team through the official contact channel listed on the Zynost website or GitHub profile.

## Scope examples

Useful reports may include:

- payment attribution or confirmation flaws;
- signature or webhook verification issues;
- authentication/authorization bypasses;
- replay or account-abstraction sponsorship weaknesses;
- RPC-consensus bypasses;
- unintended custody or fund-redirection paths;
- leakage of sensitive merchant/customer information.

## Out of scope without prior authorization

- denial-of-service/load testing against production;
- social engineering;
- automated scanning that materially impacts service availability;
- accessing or modifying real customer/merchant funds;
- testing third-party providers outside Zynost's control.

## Security design principle

The gateway is built around a simple invariant: **Zynost should be able to verify and coordinate a payment without obtaining the private keys required to spend merchant funds.**

The public source makes those inspectable controls reviewable; live credentials and operational controls remain outside this repository.
