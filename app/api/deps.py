from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import ALGORITHM, SECRET_KEY, SESSION_IDLE_TIMEOUT_MINUTES
from app.db.database import get_db
from app.models.member import Member
from app.models.personal_access_token import PersonalAccessToken

# This defines the security scheme.
# It tells FastAPI (and the Swagger UI!) where a user can get a token.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Anmeldedaten ungültig.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _decode_token(token: str) -> tuple[str, str]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str | None = payload.get("sub")
        token_id: str | None = payload.get("jti")
        if email is None or token_id is None:
            raise _CREDENTIALS_EXCEPTION
    except jwt.PyJWTError:
        raise _CREDENTIALS_EXCEPTION from None
    else:
        return email, token_id


def _get_session_record(db: Session, token_id: str) -> PersonalAccessToken:
    session_record = (
        db.query(PersonalAccessToken)
        .filter(PersonalAccessToken.token == token_id)
        .first()
    )
    if not session_record:
        raise _CREDENTIALS_EXCEPTION
    return session_record


def _ensure_tz_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _enforce_idle_timeout(db: Session, session_record: PersonalAccessToken) -> None:
    now = datetime.now(UTC)
    last_used = session_record.last_used_at

    if not last_used:
        session_record.last_used_at = now
        db.commit()
        return

    last_used = _ensure_tz_aware(last_used)
    idle_duration = now - last_used

    if idle_duration > timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES):
        db.delete(session_record)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired due to inactivity.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if idle_duration > timedelta(minutes=1):
        session_record.last_used_at = now
        db.commit()


def _get_verified_user(db: Session, email: str) -> Member:
    user = db.query(Member).filter(Member.email == email).first()
    if user is None:
        raise _CREDENTIALS_EXCEPTION
    if user.auth_locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is locked"
        )
    return user


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Member:
    """
    Dependency that extracts the JWT, verifies it cryptographically,
    checks the server-side session, enforces inactivity timeouts,
    and returns the Member object.
    """
    email, token_id = _decode_token(token)
    session_record = _get_session_record(db, token_id)
    _enforce_idle_timeout(db, session_record)
    return _get_verified_user(db, email)
