# Security Policy

Zynost Pay is payment infrastructure. Please treat security findings responsibly and do not test against live merchant/customer funds without explicit written authorization.

## What this public repository contains

This repository documents architecture, trust boundaries, security invariants and the public behavior of the Zynost Pay gateway.

It intentionally does **not** contain production credentials, private keys, seed phrases, customer/merchant data, signing secrets, production database access, operational runbooks or the complete private backend source.

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

Security controls continue to evolve as the system is reviewed and expanded.
