from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, oauth2_scheme
from app.core.rate_limit import limiter
from app.core.security import (
    REFRESH_TOKEN_LIFETIME_DAYS,
    build_refresh_cookie_value,
    parse_refresh_cookie,
)
from app.db.database import get_db
from app.models.member import Member
from app.schemas.auth import (
    ForgotPasswordRequest,
    GoogleLinkRequest,
    GoogleLoginRequest,
    ResetPasswordRequest,
)
from app.services import auth_service
from app.services.auth_service import AccountNotLinkedError

auth_router = APIRouter()

COOKIE_MAX_AGE = REFRESH_TOKEN_LIFETIME_DAYS * 86400
COOKIE_PATH = "/api/auth"


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
        key="refresh_token",
        value=build_refresh_cookie_value(session_id, refresh_secret),
        httponly=True,
        secure=True,
        samesite="lax",
        path=COOKIE_PATH,
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
    member, reason = auth_service.authenticate_user(
        db, form_data.username, form_data.password
    )

    if not member:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "detail": "Ungültige E-Mail-Adresse oder Passwort,"
                " oder das Konto ist gesperrt.",
                "failure_reason": reason,
                "attempted_email": (
                    form_data.username if reason == "unknown_email" else None
                ),
            },
        )

    access_token, session_id, refresh_secret = auth_service.create_user_session(
        db, member
    )
    return _build_login_response(access_token, session_id, refresh_secret)


@auth_router.post("/forgot-password")
@limiter.limit("3/minute")  # type: ignore[reportUntypedFunctionDecorator]
def forgot_password(
    request: Request,  # noqa: ARG001
    data: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    """Request a password reset email.

    Always returns 200 to prevent email enumeration.
    Rate limit: 3/min.
    """
    auth_service.process_forgot_password(db, background_tasks, data.email)
    return {
        "status": "ok",
        "message": (
            "Falls die E-Mail-Adresse registriert ist, wurde ein Reset-Link versendet."
        ),
    }


@auth_router.post("/reset-password")
def reset_password(
    data: ResetPasswordRequest, db: Annotated[Session, Depends(get_db)]
) -> dict[str, str]:
    """Set a new password using a time-limited reset token (20 min TTL)."""
    try:
        auth_service.execute_password_reset(db, data.email, data.token, data.password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from None

    return {
        "status": "ok",
        "message": "Passwort wurde erfolgreich aktualisiert.",
    }


@auth_router.post("/google")
def login_with_google(
    data: GoogleLoginRequest, db: Annotated[Session, Depends(get_db)]
) -> JSONResponse:
    """Authenticate via Google OAuth ID token. Account must be linked first."""
    try:
        member = auth_service.authenticate_google_user(db, data.credential)
    except AccountNotLinkedError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ACCOUNT_NOT_LINKED",
        ) from None
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        ) from None

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
    try:
        member = auth_service.link_google_account(
            db, data.credential, data.email, data.password
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        ) from None

    access_token, session_id, refresh_secret = auth_service.create_user_session(
        db, member
    )
    return _build_login_response(access_token, session_id, refresh_secret)


@auth_router.delete("/google/link")
def unlink_google_account(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str]:
    """Remove the Google OAuth link from the current user's account."""
    auth_service.unlink_google_account(db, current_user.id)
    return {"status": "ok", "message": "Google-Verknüpfung wurde erfolgreich gelöst."}


@auth_router.post("/refresh")
@limiter.limit("10/minute")  # type: ignore[reportUntypedFunctionDecorator]
def refresh(request: Request, db: Annotated[Session, Depends(get_db)]) -> JSONResponse:
    """Exchange a refresh-token cookie for a new access token.

    Rotates the refresh token on each use. Rate limit: 10/min.
    """
    cookie_value = request.cookies.get("refresh_token")
    if not cookie_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token",
        )

    try:
        session_id, refresh_secret = parse_refresh_cookie(cookie_value)
        access_token, new_secret = auth_service.refresh_session(
            db, session_id, refresh_secret
        )
    except ValueError:
        response = JSONResponse(
            status_code=401,
            content={"detail": "Session expired or invalid."},
        )
        response.delete_cookie("refresh_token", path=COOKIE_PATH)
        return response

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
    response.delete_cookie("refresh_token", path=COOKIE_PATH)
    return response
