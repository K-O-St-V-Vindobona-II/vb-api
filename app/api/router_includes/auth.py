from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, oauth2_scheme
from app.core.arq_pool import get_arq_pool
from app.core.rate_limit import limiter
from app.core.security import (
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
    REFRESH_TOKEN_LIFETIME_DAYS,
    build_refresh_cookie_value,
)
from app.db.database import get_db
from app.schemas.auth import (
    ForgotPasswordRequest,
    GoogleLinkRequest,
    GoogleLoginRequest,
    ResetPasswordRequest,
    StatusMessageResponse,
)
from app.services import auth_service

auth_router = APIRouter()

COOKIE_MAX_AGE = REFRESH_TOKEN_LIFETIME_DAYS * 86400


def _build_login_response(
    access_token: str,
    session_id: str,
    refresh_secret: str,
) -> JSONResponse:
    response = JSONResponse(
        content={
            "access_token": access_token,
            "token_type": "bearer",
        }
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=build_refresh_cookie_value(session_id, refresh_secret),
        httponly=True,
        secure=True,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
        max_age=COOKIE_MAX_AGE,
    )
    return response


@auth_router.post("/login")
@limiter.limit("5/minute")  # type: ignore[reportUntypedFunctionDecorator]
def login(
    request: Request,  # noqa: ARG001
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """Authenticate with email and password, receive a JWT access token.

    Rate limit: 5/min.
    """
    member, _ = auth_service.authenticate_user(
        db, form_data.username, form_data.password
    )

    if not member:
        # The client only learns that the login failed, never why.
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "detail": "Ungültige E-Mail-Adresse oder Passwort,"
                " oder das Konto ist gesperrt.",
            },
        )

    access_token, session_id, refresh_secret = auth_service.create_user_session(
        db, member
    )
    return _build_login_response(access_token, session_id, refresh_secret)


@auth_router.post("/forgot-password")
@limiter.limit("3/minute")  # type: ignore[reportUntypedFunctionDecorator]
async def forgot_password(
    request: Request,  # noqa: ARG001
    data: ForgotPasswordRequest,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
) -> StatusMessageResponse:
    """Request a password reset email.

    Always returns 200 to prevent email enumeration.
    Rate limit: 3/min.
    """
    result = await run_in_threadpool(
        auth_service.process_forgot_password, db, data.email
    )
    if result is not None:
        email, token = result
        await arq_pool.enqueue_job("task_send_reset_email", email, token)
    return StatusMessageResponse(
        status="ok",
        message=(
            "Falls die E-Mail-Adresse registriert ist, wurde ein Reset-Link versendet."
        ),
    )


@auth_router.post("/reset-password")
@limiter.limit("5/minute")  # type: ignore[reportUntypedFunctionDecorator]
def reset_password(
    request: Request,  # noqa: ARG001
    data: ResetPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
) -> StatusMessageResponse:
    """Set a new password using a time-limited reset token (20 min TTL).

    Rate limit: 5/min.
    """
    auth_service.execute_password_reset(db, data.email, data.token, data.password)
    return StatusMessageResponse(
        status="ok",
        message="Passwort wurde erfolgreich aktualisiert.",
    )


@auth_router.post("/google")
@limiter.limit("10/minute")  # type: ignore[reportUntypedFunctionDecorator]
def login_with_google(
    request: Request,  # noqa: ARG001
    data: GoogleLoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """Authenticate via Google OAuth ID token. Account must be linked first.

    Rate limit: 10/min.
    """
    member = auth_service.authenticate_google_user(db, data.credential)
    access_token, session_id, refresh_secret = auth_service.create_user_session(
        db, member
    )
    return _build_login_response(access_token, session_id, refresh_secret)


@auth_router.post("/google/link")
@limiter.limit("5/minute")  # type: ignore[reportUntypedFunctionDecorator]
def link_google_account(
    request: Request,  # noqa: ARG001
    data: GoogleLinkRequest,
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """Link a Google account to an existing user by verifying email and password.

    Rate limit: 5/min.
    """
    member = auth_service.link_google_account(
        db, data.credential, data.email, data.password
    )
    access_token, session_id, refresh_secret = auth_service.create_user_session(
        db, member
    )
    return _build_login_response(access_token, session_id, refresh_secret)


@auth_router.delete("/google/link")
def unlink_google_account(
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> StatusMessageResponse:
    """Remove the Google OAuth link from the current user's account."""
    auth_service.unlink_google_account(db, current_user.id)
    return StatusMessageResponse(
        status="ok", message="Google-Verknüpfung wurde erfolgreich gelöst."
    )


@auth_router.post("/refresh")
@limiter.limit("10/minute")  # type: ignore[reportUntypedFunctionDecorator]
def refresh(request: Request, db: Annotated[Session, Depends(get_db)]) -> JSONResponse:
    """Exchange a refresh-token cookie for a new access token.

    Rotates the refresh token on each use. Rate limit: 10/min.
    """
    access_token, session_id, new_secret = auth_service.refresh_from_cookie(
        db, request.cookies.get(REFRESH_COOKIE_NAME)
    )
    return _build_login_response(access_token, session_id, new_secret)


@auth_router.post("/logout")
def logout(
    _request: Request,
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """Invalidate the current session and clear the refresh-token cookie."""
    auth_service.logout_user(db, token)
    response = JSONResponse(
        content={"status": "ok", "message": "Erfolgreich abgemeldet."}
    )
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
    return response
