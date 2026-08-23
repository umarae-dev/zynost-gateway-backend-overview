"""Push notifications via Firebase Cloud Messaging's HTTP v1 API - built
on google-auth to mint short-lived OAuth2 access tokens straight from the
service account, rather than pulling in the much heavier firebase-admin
SDK just to send a message. Same design as tradeos-backend's own copy of
this file (see that repo for the fuller rationale) - kept as an
independent copy here rather than a shared import, matching this
codebase's "zero code relationship to tradeos-backend" rule; the two
share only the underlying device_tokens table (same Postgres DB).

Every call here is best-effort by design: a push failing must never block
or fail the real action that triggered it (a payment confirming, a key
regenerating, the gas tank running low) - notifications are a layer on
top of already-real state changes, never a dependency of them.
"""
import json
import logging
from datetime import datetime, timezone

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.device_token import DeviceToken

logger = logging.getLogger("gateway")

_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

_cached_credentials: service_account.Credentials | None = None


def _get_credentials() -> service_account.Credentials | None:
    global _cached_credentials
    if not settings.FIREBASE_SERVICE_ACCOUNT_JSON:
        return None
    if _cached_credentials is None:
        info = json.loads(settings.FIREBASE_SERVICE_ACCOUNT_JSON)
        _cached_credentials = service_account.Credentials.from_service_account_info(info, scopes=[_FCM_SCOPE])
    return _cached_credentials


def _access_token() -> str | None:
    creds = _get_credentials()
    if creds is None:
        return None
    if not creds.valid:
        creds.refresh(GoogleAuthRequest())
    return creds.token


async def _send_to_token(
    client: httpx.AsyncClient, fcm_token: str, title: str, body: str, data: dict | None,
) -> str:
    """Returns "sent", "dead" (prune it), or "failed" (transient)."""
    token = _access_token()
    if token is None or not settings.FIREBASE_PROJECT_ID:
        return "failed"
    url = f"https://fcm.googleapis.com/v1/projects/{settings.FIREBASE_PROJECT_ID}/messages:send"
    message = {
        "message": {
            "token": fcm_token,
            "notification": {"title": title, "body": body},
            "data": {k: str(v) for k, v in (data or {}).items()},
        }
    }
    try:
        resp = await client.post(url, json=message, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    except httpx.HTTPError:
        logger.warning("FCM send raised a network error", exc_info=True)
        return "failed"
    if resp.status_code == 200:
        return "sent"
    if resp.status_code in (400, 404):
        logger.info("FCM token is dead (%s), will prune: %s", resp.status_code, resp.text)
        return "dead"
    logger.warning("FCM send failed (%s): %s", resp.status_code, resp.text)
    return "failed"


async def send_push_to_user(
    db: AsyncSession, user_id, title: str, body: str, data: dict | None = None,
) -> int:
    """Pushes to every device this user (merchant) has registered. Never
    raises - callers must be able to treat this as fire-and-forget."""
    if not settings.FIREBASE_SERVICE_ACCOUNT_JSON or not settings.FIREBASE_PROJECT_ID:
        return 0
    try:
        result = await db.execute(select(DeviceToken).where(DeviceToken.user_id == user_id))
        tokens = result.scalars().all()
        if not tokens:
            return 0

        sent = 0
        dead_ids = []
        async with httpx.AsyncClient() as client:
            for dt in tokens:
                outcome = await _send_to_token(client, dt.fcm_token, title, body, data)
                if outcome == "sent":
                    sent += 1
                elif outcome == "dead":
                    dead_ids.append(dt.id)

        if dead_ids:
            await db.execute(delete(DeviceToken).where(DeviceToken.id.in_(dead_ids)))
            await db.commit()

        return sent
    except Exception:
        logger.warning("send_push_to_user failed for user_id=%s", user_id, exc_info=True)
        return 0


async def register_device(db: AsyncSession, user_id, fcm_token: str, platform: str) -> None:
    result = await db.execute(select(DeviceToken).where(DeviceToken.fcm_token == fcm_token))
    existing = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing:
        existing.user_id = user_id
        existing.platform = platform
        existing.last_seen_at = now
    else:
        db.add(DeviceToken(user_id=user_id, fcm_token=fcm_token, platform=platform, last_seen_at=now))
    await db.commit()


async def unregister_device(db: AsyncSession, fcm_token: str) -> None:
    await db.execute(delete(DeviceToken).where(DeviceToken.fcm_token == fcm_token))
    await db.commit()
