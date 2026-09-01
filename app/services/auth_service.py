import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NoReturn

import jwt
import requests
from google.auth.exceptions import TransportError
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token
from requests.adapters import HTTPAdapter
from sqlalchemy import func

from app.core.config import get_settings
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
from app.models.auth_session import AuthSession
from app.models.member import Member
from app.models.members_oauth2binding import MembersOauth2Binding
from app.models.password_reset import PasswordResetToken

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_GOOGLE_CERTS_TIMEOUT_SECONDS = 5
_GOOGLE_AUTH_UNAVAILABLE_MESSAGE = (
    "Google-Anmeldung ist gerade nicht erreichbar. Bitte versuch es später erneut."
)


class _TimeoutHTTPAdapter(HTTPAdapter):
    """Applies a default timeout to every request unless the caller already set one.

    id_token.verify_oauth2_token() fetches Google's public certs internally
    and exposes no way to pass a timeout through to that call - without this
    adapter, a stalled connection blocks for google-auth's own internal
    default of 120 seconds (per attempted address) instead of failing fast.
    Mounting a custom adapter on the session is the transport-level hook
    google-auth's own docs recommend for bounding this call.
    """

    def send(  # noqa: PLR0917 - matches base class signature, must stay positional
        self,
        request: requests.PreparedRequest,
        stream: bool = False,  # noqa: FBT001, FBT002 - matches base class signature
        timeout: float | tuple[float | None, float | None] | None = None,
        verify: bool | str = True,  # noqa: FBT001, FBT002 - matches base class signature
        cert: str | tuple[str, str] | None = None,
        proxies: dict[str, str] | None = None,
    ) -> requests.Response:
        if timeout is None:
            timeout = _GOOGLE_CERTS_TIMEOUT_SECONDS
        return super().send(
            request,
            stream=stream,
            timeout=timeout,
            verify=verify,
            cert=cert,
            proxies=proxies,
        )


def _build_google_auth_request() -> google_auth_requests.Request:
    session = requests.Session()
    adapter = _TimeoutHTTPAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return google_auth_requests.Request(session=session)


# Module-level singleton, reused across calls for connection pooling instead
# of building a fresh session on every single login/link attempt.
_google_auth_request = _build_google_auth_request()


class AccountNotLinkedError(Exception):
    """
    Signals the router that the Google token is valid,
    but not yet linked to an account.
    """


class GoogleAuthUnavailableError(Exception):
    """
    Signals the router that Google's token verification service could not be
    reached (network error/timeout), as opposed to the token itself being
    invalid.
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
    email: str,
) -> tuple[str, str] | None:
    """Create a reset token for the given email if a matching member
    exists, and return (email, token) for the caller to enqueue an ARQ
    reset-email task from — kept a plain sync function (only touches the
    DB) so the router can dispatch it via run_in_threadpool rather than
    running it directly on the event loop.
    """
    member = (
        db.query(Member).filter(func.lower(Member.email) == func.lower(email)).first()
    )

    if not member:
        return None

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

    return (member.email, token) if member.email else None


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

    db.query(AuthSession).filter(
        AuthSession.member_id == member.id,
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

    db_token = AuthSession(
        member_id=member.id,
        jti=session_id,
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


def _invalidate_session(db: Session, session: AuthSession, reason: str) -> NoReturn:
    db.delete(session)
    db.commit()
    raise ValueError(reason)


def _validate_refresh_token(
    db: Session,
    session: AuthSession,
    refresh_secret: str,
) -> None:
    if not session.refresh_token_hash or not verify_refresh_secret(
        refresh_secret, session.refresh_token_hash
    ):
        _invalidate_session(db, session, "Token reuse detected")


def _validate_session_expiry(
    db: Session,
    session: AuthSession,
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
    session = db.query(AuthSession).filter(AuthSession.jti == session_id).first()
    if not session:
        msg = "Invalid session"
        raise ValueError(msg)

    _validate_refresh_token(db, session, refresh_secret)

    now = datetime.now(UTC)
    _validate_session_expiry(db, session, now)

    member = db.query(Member).filter(Member.id == session.member_id).first()
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
    client_id = get_settings().google_client_id
    if not client_id:
        msg = "Google Login ist auf dem Server nicht konfiguriert."
        raise ValueError(msg)

    try:
        id_info = id_token.verify_oauth2_token(
            credential_token,
            _google_auth_request,
            client_id,
        )
    except TransportError as e:
        raise GoogleAuthUnavailableError(_GOOGLE_AUTH_UNAVAILABLE_MESSAGE) from e
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
        # Known account -> Update timestamp and return member. Not
        # committed here: the caller always follows up with
        # create_user_session(), whose commit covers this too - one
        # atomic "log the user in" operation instead of two commits.
        binding.lastuse_at = datetime.now(UTC)
        db.flush()
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
    client_id = get_settings().google_client_id
    try:
        id_info = id_token.verify_oauth2_token(
            credential_token,
            _google_auth_request,
            client_id,
        )
    except TransportError as e:
        raise GoogleAuthUnavailableError(_GOOGLE_AUTH_UNAVAILABLE_MESSAGE) from e
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
            # Not committed here: the caller always follows up with
            # create_user_session(), whose commit covers this too.
            existing_binding.lastuse_at = datetime.now(UTC)
            db.flush()
            return member
        msg = "Dieser Account oder dieses Google-Konto ist bereits verknüpft."
        raise ValueError(msg)

    # 4. Create the binding in the database. Not committed here either -
    # same reasoning, create_user_session()'s commit covers this too.
    new_binding = MembersOauth2Binding(
        member_id=member.id,
        provider="google",
        remote_id=google_id,
        remote_name=google_name,
        bound_at=datetime.now(UTC),
        lastuse_at=datetime.now(UTC),
    )
    db.add(new_binding)
    db.flush()

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

        session = db.query(AuthSession).filter(AuthSession.jti == token_id).first()
        if not session:
            return

        member = db.query(Member).filter(Member.id == session.member_id).first()
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
