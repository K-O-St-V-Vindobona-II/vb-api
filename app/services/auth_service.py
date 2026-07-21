import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import jwt
from fastapi import BackgroundTasks
from google.auth.transport import requests
from google.oauth2 import id_token
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.mailer import send_reset_email
from app.core.security import (
    ALGORITHM,
    REFRESH_TOKEN_LIFETIME_DAYS,
    SECRET_KEY,
    SESSION_IDLE_TIMEOUT_MINUTES,
    create_access_token,
    generate_refresh_secret,
    get_password_hash,
    hash_refresh_secret,
    verify_password,
    verify_refresh_secret,
)
from app.models.member import Member
from app.models.members_oauth2binding import MembersOauth2Binding
from app.models.password_reset import PasswordResetToken
from app.models.personal_access_token import PersonalAccessToken


class AccountNotLinkedError(Exception):
    """
    Signals the router that the Google token is valid,
    but not yet linked to an account.
    """


def authenticate_user(
    db: Session,
    email: str,
    password: str,
) -> tuple[Member | None, str]:
    member = (
        db.query(Member).filter(func.lower(Member.email) == func.lower(email)).first()
    )

    if not member:
        return None, "unknown_email"
    if member.auth_locked:
        return None, "account_locked"
    if not member.auth_password or not verify_password(password, member.auth_password):
        return None, "wrong_password"
    return member, "ok"


def process_forgot_password(
    db: Session,
    background_tasks: BackgroundTasks,
    email: str,
) -> None:
    member = (
        db.query(Member).filter(func.lower(Member.email) == func.lower(email)).first()
    )

    if not member:
        return

    token = secrets.token_urlsafe(32)
    db.query(PasswordResetToken).filter(
        func.lower(PasswordResetToken.email) == func.lower(email)
    ).delete()

    reset_entry = PasswordResetToken(
        email=member.email,
        token=token,
        created_at=datetime.now(UTC),
    )
    db.add(reset_entry)
    db.commit()

    if member.email:
        background_tasks.add_task(send_reset_email, member.email, token)


def execute_password_reset(
    db: Session,
    email: str,
    token: str,
    new_password: str,
) -> None:
    reset_entry = (
        db.query(PasswordResetToken)
        .filter(
            func.lower(PasswordResetToken.email) == func.lower(email),
            PasswordResetToken.token == token,
        )
        .first()
    )

    if not reset_entry:
        msg = "Ungültiger Token oder E-Mail-Adresse."
        raise ValueError(msg)

    created_at = reset_entry.created_at
    if not created_at:
        msg = "Token hat kein Erstellungsdatum."
        raise ValueError(msg)
    # Handle legacy tokens stored before timezone-aware datetimes
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)

    token_age = datetime.now(UTC) - created_at

    if token_age > timedelta(minutes=20):
        db.delete(reset_entry)
        db.commit()
        msg = "Der Reset-Token ist abgelaufen."
        raise ValueError(msg)

    member = (
        db.query(Member).filter(func.lower(Member.email) == func.lower(email)).first()
    )
    if not member:
        msg = "Benutzerkonto nicht gefunden."
        raise ValueError(msg)

    member.auth_password = get_password_hash(new_password)
    member.email_verified_at = datetime.now(UTC)

    db.query(PersonalAccessToken).filter(
        PersonalAccessToken.tokenable_type == "Member",
        PersonalAccessToken.tokenable_id == member.id,
    ).delete()

    db.delete(reset_entry)
    db.commit()


def create_user_session(db: Session, member: Member) -> tuple[str, str, str]:
    if not member.email:
        msg = "Member hat keine E-Mail-Adresse."
        raise ValueError(msg)
    access_token, session_id = create_access_token(subject=member.email)
    refresh_secret = generate_refresh_secret()
    now = datetime.now(UTC)

    db_token = PersonalAccessToken(
        tokenable_type="Member",
        tokenable_id=member.id,
        name="session",
        token=session_id,
        refresh_token_hash=hash_refresh_secret(refresh_secret),
        last_used_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(db_token)
    member.auth_lastlogin = now
    db.commit()

    return access_token, session_id, refresh_secret


def _ensure_tz_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _invalidate_session(
    db: Session, session: PersonalAccessToken, reason: str
) -> NoReturn:
    db.delete(session)
    db.commit()
    raise ValueError(reason)


def _validate_refresh_token(
    db: Session,
    session: PersonalAccessToken,
    refresh_secret: str,
) -> None:
    if not session.refresh_token_hash or not verify_refresh_secret(
        refresh_secret, session.refresh_token_hash
    ):
        _invalidate_session(db, session, "Token reuse detected")


def _validate_session_expiry(
    db: Session,
    session: PersonalAccessToken,
    now: datetime,
) -> None:
    last_used = _ensure_tz_aware(session.last_used_at)
    if last_used and (now - last_used) > timedelta(
        minutes=SESSION_IDLE_TIMEOUT_MINUTES
    ):
        _invalidate_session(db, session, "Session expired due to inactivity")

    created = _ensure_tz_aware(session.created_at)
    if created and (now - created) > timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS):
        _invalidate_session(db, session, "Session expired")


def refresh_session(
    db: Session,
    session_id: str,
    refresh_secret: str,
) -> tuple[str, str]:
    session = (
        db.query(PersonalAccessToken)
        .filter(PersonalAccessToken.token == session_id)
        .first()
    )
    if not session:
        msg = "Invalid session"
        raise ValueError(msg)

    _validate_refresh_token(db, session, refresh_secret)

    now = datetime.now(UTC)
    _validate_session_expiry(db, session, now)

    member = db.query(Member).filter(Member.id == session.tokenable_id).first()
    if not member or member.auth_locked:
        _invalidate_session(db, session, "Account locked or deleted")

    if not member.email:
        _invalidate_session(db, session, "Account has no email")

    new_secret = generate_refresh_secret()
    session.refresh_token_hash = hash_refresh_secret(new_secret)
    session.last_used_at = now
    member.auth_lastsignal = now

    access_token, _ = create_access_token(subject=member.email, jti_override=session_id)
    db.commit()

    return access_token, new_secret


def authenticate_google_user(db: Session, credential_token: str) -> Member:
    """
    Verifies a Google token and returns the bound Member.
    Throws AccountNotLinkedError if the token is valid but not bound.
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    if not client_id:
        msg = "Google Login ist auf dem Server nicht konfiguriert."
        raise ValueError(msg)

    try:
        id_info = id_token.verify_oauth2_token(
            credential_token,
            requests.Request(),
            client_id,
        )
    except ValueError:
        msg = "Ungültiger Google-Token."
        raise ValueError(msg) from None

    google_id = id_info.get("sub")

    # Check if we already know this Google account
    binding = (
        db.query(MembersOauth2Binding)
        .filter(
            MembersOauth2Binding.provider == "google",
            MembersOauth2Binding.remote_id == google_id,
        )
        .first()
    )

    if binding:
        # Known account -> Update timestamp and return member
        binding.lastuse_at = datetime.now(UTC)
        db.commit()
        member = db.query(Member).filter(Member.id == binding.member_id).first()

        if not member or member.auth_locked:
            msg = "Dein Account ist gesperrt oder wurde gelöscht."
            raise ValueError(msg)
        return member

    # Unlinked Google account triggers special frontend linking flow
    raise AccountNotLinkedError


def link_google_account(
    db: Session,
    credential_token: str,
    email: str,
    password: str,
) -> Member:
    """
    Verifies local credentials AND the Google token, then links them together.
    """
    # 1. Verify local credentials
    member, _ = authenticate_user(db, email, password)
    if not member:
        msg = "Die lokale E-Mail-Adresse oder das Passwort ist falsch."
        raise ValueError(msg)

    # 2. Verify Google Token again
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    try:
        id_info = id_token.verify_oauth2_token(
            credential_token,
            requests.Request(),
            client_id,
        )
    except ValueError:
        msg = "Der Google-Token ist ungültig oder abgelaufen."
        raise ValueError(msg) from None

    google_id = id_info.get("sub")
    google_name = id_info.get("name", "Unknown")

    # 3. Check if this Google account is already linked to ANOTHER user
    # OR if this local member already has a binding.
    existing_binding = (
        db.query(MembersOauth2Binding)
        .filter(
            MembersOauth2Binding.provider == "google",
            (MembersOauth2Binding.remote_id == google_id)
            | (MembersOauth2Binding.member_id == member.id),
        )
        .first()
    )

    if existing_binding:
        if (
            existing_binding.member_id == member.id
            and existing_binding.remote_id == google_id
        ):
            existing_binding.lastuse_at = datetime.now(UTC)
            db.commit()
            return member
        msg = "Dieser Account oder dieses Google-Konto ist bereits verknüpft."
        raise ValueError(msg)

    # 4. Create the binding in the database
    new_binding = MembersOauth2Binding(
        member_id=member.id,
        provider="google",
        remote_id=google_id,
        remote_name=google_name,
        bound_at=datetime.now(UTC),
        lastuse_at=datetime.now(UTC),
    )
    db.add(new_binding)
    db.commit()

    return member


def logout_user(db: Session, token: str) -> None:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"verify_exp": False},
        )
        token_id = payload.get("jti")
        if not token_id:
            return

        session = (
            db.query(PersonalAccessToken)
            .filter(PersonalAccessToken.token == token_id)
            .first()
        )
        if not session:
            return

        member = db.query(Member).filter(Member.id == session.tokenable_id).first()
        if member:
            member.auth_lastlogout = datetime.now(UTC)

        db.delete(session)
        db.commit()

    except jwt.PyJWTError:
        pass


def unlink_google_account(db: Session, member_id: int) -> None:
    """
    Removes the Google binding for a specific user.
    """
    db.query(MembersOauth2Binding).filter(
        MembersOauth2Binding.member_id == member_id,
        MembersOauth2Binding.provider == "google",
    ).delete()
    db.commit()
