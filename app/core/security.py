import hashlib
import jwt
from app.core.config import settings

# Same algorithm/claim shape tradeos-backend's create_access_token uses
# ({"sub": user_id, "exp": ...}, HS256) — this only DECODES tokens (never
# issues login sessions itself, since this service has no login/signup of
# its own), verified against the same JWT_SECRET value shared at the
# environment-variable level. A Zynost user's existing login session is
# what proves who they are here; there's no separate merchant password.
_ALGORITHM = "HS256"


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[_ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def hash_token(token: str) -> str:
    """SHA-256 hex digest — used for API key hashing (already-random,
    high-entropy secrets gain nothing from a slow password-hashing
    algorithm)."""
    return hashlib.sha256(token.encode()).hexdigest()
