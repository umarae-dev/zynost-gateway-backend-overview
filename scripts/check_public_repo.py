from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".pytest_cache", "__pycache__", "venv", ".venv", "node_modules"}
FORBIDDEN_NAMES = {".env", "credentials.json", "service-account.json", "secrets.json", "id_rsa", "id_ed25519"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".env", ""}

PATTERNS = [
    ("private-key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "populated sensitive assignment",
        re.compile(
            r"(?im)^\s*(?:JWT_SECRET|SMTP_PASSWORD|FIREBASE_SERVICE_ACCOUNT_JSON|ALCHEMY_API_KEY|ANKR_API_KEY)\s*=\s*(?!\s*(?:$|YOUR_|CHANGE_ME|example|placeholder))([^\s#]{12,})"
        ),
    ),
]

failures: list[str] = []
for path in ROOT.rglob("*"):
    if any(part in SKIP_DIRS for part in path.parts):
        continue
    if not path.is_file():
        continue
    rel = path.relative_to(ROOT)
    if path.name in FORBIDDEN_NAMES:
        failures.append(f"forbidden sensitive filename: {rel}")
        continue
    if path.suffix.lower() not in TEXT_SUFFIXES:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    for label, pattern in PATTERNS:
        if pattern.search(text):
            failures.append(f"possible {label}: {rel}")

if failures:
    print("Public repository guard failed:")
    for failure in failures:
        print(f"- {failure}")
    raise SystemExit(1)

print("Public repository guard passed: no forbidden files or obvious credential material found.")
