import logging
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, NoReturn

import jwt
import requests
from google.auth import transport as google_auth_transport
from google.auth.exceptions import TransportError
from google.auth.transport import Response as GoogleTransportResponse
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token
from requests.adapters import HTTPAdapter
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

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
    hash_reset_token,
    verify_password,
    verify_refresh_secret,
)
from app.models.auth_session import AuthSession
from app.models.enums import OauthProvider
from app.models.member import Member
from app.models.members_oauth2binding import MembersOauth2Binding
from app.models.password_reset import PasswordResetToken

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping

    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_GOOGLE_CERTS_TIMEOUT_SECONDS = 5
# Google publishes its signing certificates with a cache lifetime of hours;
# five minutes keeps outbound traffic negligible and lets a rotated key take
# effect quickly.
_GOOGLE_CERTS_CACHE_SECONDS = 300
_GOOGLE_AUTH_UNAVAILABLE_MESSAGE = (
    "Google-Anmeldung ist gerade nicht erreichbar. Bitte versuch es später erneut."
)
_GOOGLE_ALREADY_LINKED_MESSAGE = (
    "Dieser Account oder dieses Google-Konto ist bereits verknüpft."
)

# Cost-12 hash of a random value that was discarded right after hashing.
# Verified against when the e-mail address is unknown, so a login attempt costs
# the same bcrypt time whether or not the account exists.
_DUMMY_PASSWORD_HASH = "$2b$12$Sw3Wj/zUDN7QqY3Rqv.8AOty1Lf.suclJNg0HrIP5wSvtd7XmGfDm"  # noqa: S105 - hash of a discarded random value


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


class _CertsCachingRequest(google_auth_transport.Request):
    """google-auth transport that caches successful GET responses briefly.

    id_token.verify_oauth2_token() downloads Google's signing certificates on
    every call, before it even looks at the token. Without this cache every
    request to the unauthenticated Google endpoints, including one carrying
    garbage, costs one outbound HTTPS request. It also applies the short
    default timeout itself: google-auth's own transport passes 120 seconds
    explicitly, which the adapter's "no timeout given" default never replaces.
    """

    def __init__(self, session: requests.Session) -> None:
        self._transport = google_auth_requests.Request(session=session)
        self._cache: dict[str, tuple[float, GoogleTransportResponse]] = {}
        self._lock = threading.Lock()

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: int | None = None,
        **kwargs: object,
    ) -> GoogleTransportResponse:
        timeout = _GOOGLE_CERTS_TIMEOUT_SECONDS if timeout is None else timeout
        if method != "GET":
            return self._transport(url, method, body, headers, timeout, **kwargs)
        with self._lock:
            cached = self._cache.get(url)
        if cached and time.monotonic() - cached[0] < _GOOGLE_CERTS_CACHE_SECONDS:
            return cached[1]
        response = self._transport(url, method, body, headers, timeout, **kwargs)
        if response.status == HTTPStatus.OK:
            with self._lock:
                self._cache[url] = (time.monotonic(), response)
        return response


def _build_google_auth_request() -> _CertsCachingRequest:
    session = requests.Session()
    adapter = _TimeoutHTTPAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return _CertsCachingRequest(session=session)


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
    """Check e-mail and password; the reason is meant for server-side logging.

    Exactly one bcrypt comparison runs for every attempt, so unknown, locked and
    known accounts cannot be told apart by their response time.
    """
    member = (
        db.query(Member).filter(func.lower(Member.email) == func.lower(email)).first()
    )
    stored_hash = (member.auth_password if member else None) or _DUMMY_PASSWORD_HASH
    password_ok = verify_password(password, stored_hash)

    if not member:
        return _rejected("unknown_email")
    if member.auth_locked:
        return _rejected("account_locked")
    if not member.auth_password or not password_ok:
        return _rejected("wrong_password")
    return member, "ok"


def _rejected(reason: str) -> tuple[None, str]:
    """Log a failed login without the submitted address and return the reason."""
    logger.info("Login rejected: %s", reason)
    return None, reason


def process_forgot_password(
    db: Session,
    email: str,
) -> tuple[str, str] | None:
    """Create a reset token for the given email if a matching member
    exists, and return (email, token) for the caller to enqueue an ARQ
    reset-email task from — kept a plain sync function (only touches the
    DB) so the router can dispatch it via run_in_threadpool rather than
    running it directly on the event loop.

    Only a digest of the token is stored; the token itself exists solely in
    the returned value and, from there, in the e-mail.
    """
    member = (
        db.query(Member).filter(func.lower(Member.email) == func.lower(email)).first()
    )

    if not member:
        return None

    token = secrets.token_urlsafe(32)
    db.query(PasswordResetToken).filter(
        PasswordResetToken.member_id == member.id
    ).delete()
    db.add(PasswordResetToken(member_id=member.id, token_hash=hash_reset_token(token)))
    db.commit()

    return (member.email, token) if member.email else None


def execute_password_reset(
    db: Session,
    email: str,
    token: str,
    new_password: str,
) -> None:
    member = (
        db.query(Member).filter(func.lower(Member.email) == func.lower(email)).first()
    )
    reset_entry = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.member_id == member.id,
            PasswordResetToken.token_hash == hash_reset_token(token),
        )
        .first()
        if member
        else None
    )

    # An unknown address and a wrong token get the same answer.
    if not member or not reset_entry:
        msg = "Ungültiger Token oder E-Mail-Adresse."
        raise ValueError(msg)

    if datetime.now(UTC) - reset_entry.created_at > timedelta(minutes=20):
        db.delete(reset_entry)
        db.commit()
        msg = "Der Reset-Token ist abgelaufen."
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
    )
    db.add(db_token)
    member.auth_lastlogin = now
    db.commit()

    return access_token, session_id, refresh_secret


def _ensure_tz_aware(dt: datetime) -> datetime:
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
    if (now - last_used) > timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES):
        _invalidate_session(db, session, "Session expired due to inactivity")

    created = _ensure_tz_aware(session.created_at)
    if (now - created) > timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS):
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
            MembersOauth2Binding.provider == OauthProvider.GOOGLE,
            MembersOauth2Binding.remote_id == google_id,
        )
        .one_or_none()
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


def _insert_google_binding(
    db: Session, member_id: uuid.UUID, google_id: str, google_name: str
) -> None:
    """Add the binding inside a savepoint. The unique constraints decide a race
    between two concurrent link requests: the loser's savepoint is rolled back
    and it gets the same answer as if the check before had found the binding.
    """
    try:
        with db.begin_nested():
            db.add(
                MembersOauth2Binding(
                    member_id=member_id,
                    provider=OauthProvider.GOOGLE,
                    remote_id=google_id,
                    remote_name=google_name,
                )
            )
    except IntegrityError:
        raise ValueError(_GOOGLE_ALREADY_LINKED_MESSAGE) from None


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
    if not google_id:
        msg = "Der Google-Token ist ungültig oder abgelaufen."
        raise ValueError(msg)
    google_name = id_info.get("name", "Unknown")

    # 3. Check if this Google account is already linked to ANOTHER user
    # OR if this local member already has a binding.
    existing_binding = (
        db.query(MembersOauth2Binding)
        .filter(
            MembersOauth2Binding.provider == OauthProvider.GOOGLE,
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
        raise ValueError(_GOOGLE_ALREADY_LINKED_MESSAGE)

    # 4. Create the binding in the database. Not committed here either -
    # same reasoning, create_user_session()'s commit covers this too.
    _insert_google_binding(db, member.id, google_id, google_name)

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


def unlink_google_account(db: Session, member_id: uuid.UUID) -> None:
    """
    Removes the Google binding for a specific user.
    """
    db.query(MembersOauth2Binding).filter(
        MembersOauth2Binding.member_id == member_id,
        MembersOauth2Binding.provider == OauthProvider.GOOGLE,
    ).delete()
    db.commit()
