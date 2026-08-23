# Source Provenance

The application modules and tests identified in the README as production source are copied from the private production repository `umarae-dev/zynost-gateway-backend` into this public-safe mirror.

The public repository intentionally has a separate history. This avoids exposing private production commit history, deleted material, credentials, operational notes, or unrelated services.

## Exact-copy policy

Production business logic is not rewritten for this mirror. Where a production file is safe to publish, it is copied directly and its Git blob SHA can be compared with the corresponding private source. Public-only files such as CI, `.env.example`, documentation, repository guards, and public packaging may differ because they exist to make this isolated mirror safe and reproducible.

## Exclusions

A production file is not copied when it contains personal identifiers, live credentials, spend-capable material, operational signer/bundler controls, one-off migration logic, or unrelated private application functionality. Those exclusions are documented in `PUBLIC_PRIVATE_BOUNDARY.md` rather than replaced with fake implementations.
