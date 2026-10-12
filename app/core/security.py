import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

import bcrypt
import jwt

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()

# Settings._validate_tier1 already exits the process if secret_key is unset,
# so by the time get_settings() returns, it is guaranteed non-None.
SECRET_KEY = cast("str", _settings.secret_key)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = _settings.session_lifetime_minutes
SESSION_IDLE_TIMEOUT_MINUTES = _settings.session_idle_timeout_minutes
REFRESH_TOKEN_LIFETIME_DAYS = _settings.refresh_token_lifetime_days
REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/auth"

# bcrypt only ever uses the first 72 bytes of its input (release 5 raises for
# longer values, earlier releases truncated silently).
BCRYPT_MAX_PASSWORD_BYTES = 72


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        if not hashed_password:
            return False
        if hashed_password.startswith("$2y$"):
            hashed_password = hashed_password.replace("$2y$", "$2b$", 1)
        # Existing hashes were created while over-long passwords were still
        # truncated silently, so compare on the same 72-byte prefix instead of
        # failing the login (and logging a stack trace) for such accounts.
        password_bytes = plain_password.encode("utf-8")[:BCRYPT_MAX_PASSWORD_BYTES]
        hash_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(password_bytes, hash_bytes)
    except ValueError:
        logger.exception("Bcrypt verification error")
        return False


def create_access_token(
    subject: str,
    expires_delta: timedelta | None = None,
    jti_override: str | None = None,
    auth_time: datetime | None = None,
) -> tuple[str, str]:
    """Signs an access token for `subject`.

    `auth_time` is when the session began (OIDC claim, whole seconds). A
    refresh passes the stored session start so the claim survives token
    rotation; a fresh login omits it and the session starts now.
    """
    issued_at = datetime.now(UTC)
    expire = issued_at + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    token_id = jti_override or str(uuid.uuid4())
    to_encode = {
        "exp": expire,
        "iat": issued_at,
        "auth_time": int((auth_time or issued_at).timestamp()),
        "sub": str(subject),
        "jti": token_id,
    }
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt, token_id


def get_password_hash(password: str) -> str:
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def generate_refresh_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def verify_refresh_secret(plain: str, hashed: str) -> bool:
    digest = hashlib.sha256(plain.encode()).hexdigest()
    return hmac.compare_digest(digest.encode(), hashed.encode())


def hash_reset_token(token: str) -> str:
    """Digest under which a password-reset token is stored (never the token itself)."""
    return hashlib.sha256(token.encode()).hexdigest()


def build_refresh_cookie_value(session_id: str, refresh_secret: str) -> str:
    return f"{session_id}:{refresh_secret}"


def parse_refresh_cookie(cookie_value: str) -> tuple[str, str]:
    parts = cookie_value.split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        msg = "Malformed refresh cookie"
        raise ValueError(msg)
    return parts[0], parts[1]
