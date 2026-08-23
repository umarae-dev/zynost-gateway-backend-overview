# Public Release Checklist

Before treating a public revision as release-ready:

- [ ] `python scripts/check_public_repo.py` passes.
- [ ] `python -m compileall -q app` passes.
- [ ] `pytest -q` passes on Python 3.12.
- [ ] GitHub Actions is green for the exact release content.
- [ ] Published production files still match their private-source blob SHAs where exact copying applies.
- [ ] No `.env`, credential, private key, service-account JSON, user data, or production database dump is present.
- [ ] `.env.example` contains placeholders only.
- [ ] README and `PUBLIC_PRIVATE_BOUNDARY.md` describe the actual current scope.
- [ ] Operational Paymaster signer/bundler internals remain outside this repository.
- [ ] Any newly copied production source has been reviewed for personal identifiers before publication.
