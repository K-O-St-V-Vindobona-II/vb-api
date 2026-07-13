import hashlib
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

logger = logging.getLogger(__name__)

SECRET_KEY = os.environ["SECRET_KEY"]
if len(SECRET_KEY) < 32:
    msg = "SECRET_KEY must be at least 32 characters long"
    raise ValueError(msg)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("SESSION_LIFETIME_MINUTES", 15))
SESSION_IDLE_TIMEOUT_MINUTES = int(os.environ.get("SESSION_IDLE_TIMEOUT_MINUTES", 120))
REFRESH_TOKEN_LIFETIME_DAYS = int(os.environ.get("REFRESH_TOKEN_LIFETIME_DAYS", 7))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        if not hashed_password:
            return False
        if hashed_password.startswith("$2y$"):
            hashed_password = hashed_password.replace("$2y$", "$2b$", 1)
        password_bytes = plain_password.encode("utf-8")
        hash_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(password_bytes, hash_bytes)
    except Exception:
        logger.exception("Bcrypt verification error")
        return False


def create_access_token(
    subject: str,
    expires_delta: timedelta | None = None,
    jti_override: str | None = None,
) -> tuple[str, str]:
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    token_id = jti_override or str(uuid.uuid4())
    to_encode = {
        "exp": expire,
        "iat": datetime.now(UTC),
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
    return hashlib.sha256(plain.encode()).hexdigest() == hashed


def build_refresh_cookie_value(session_id: str, refresh_secret: str) -> str:
    return f"{session_id}:{refresh_secret}"


def parse_refresh_cookie(cookie_value: str) -> tuple[str, str]:
    parts = cookie_value.split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        msg = "Malformed refresh cookie"
        raise ValueError(msg)
    return parts[0], parts[1]
