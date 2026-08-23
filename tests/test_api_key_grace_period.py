"""TDD suite for the API-key regeneration grace period
(merchant_service.matches_current_or_grace_period_key) - real feedback
this addresses: regenerating a key used to invalidate the old one
INSTANTLY, breaking every already-deployed integration (this gateway's
own tradeos-backend one included) with zero migration window. Industry
standard (AWS access keys, GitHub PATs) instead keeps the old credential
alive for a bounded window."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import merchant_service


def _merchant(**overrides):
    base = dict(api_key_hash="current-hash", previous_api_key_hash=None, previous_api_key_expires_at=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def test_current_key_always_matches():
    merchant = _merchant(api_key_hash="current-hash")
    assert merchant_service.matches_current_or_grace_period_key(merchant, "current-hash") is True


def test_previous_key_matches_while_grace_period_is_open():
    merchant = _merchant(
        previous_api_key_hash="old-hash",
        previous_api_key_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert merchant_service.matches_current_or_grace_period_key(merchant, "old-hash") is True


def test_previous_key_rejected_once_grace_period_expires():
    merchant = _merchant(
        previous_api_key_hash="old-hash",
        previous_api_key_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert merchant_service.matches_current_or_grace_period_key(merchant, "old-hash") is False


def test_previous_key_rejected_when_no_grace_period_was_ever_set():
    """A merchant who has never regenerated has previous_api_key_hash=None
    - an attacker guessing an empty-string hash must never match None."""
    merchant = _merchant()
    assert merchant_service.matches_current_or_grace_period_key(merchant, "") is False
    assert merchant_service.matches_current_or_grace_period_key(merchant, None) is False


def test_unrelated_key_never_matches():
    merchant = _merchant(
        previous_api_key_hash="old-hash",
        previous_api_key_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert merchant_service.matches_current_or_grace_period_key(merchant, "some-other-hash") is False


def test_explicit_now_is_respected_for_deterministic_boundary_testing():
    expires_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    merchant = _merchant(previous_api_key_hash="old-hash", previous_api_key_expires_at=expires_at)

    just_before = expires_at - timedelta(seconds=1)
    just_after = expires_at + timedelta(seconds=1)
    assert merchant_service.matches_current_or_grace_period_key(merchant, "old-hash", now=just_before) is True
    assert merchant_service.matches_current_or_grace_period_key(merchant, "old-hash", now=just_after) is False
