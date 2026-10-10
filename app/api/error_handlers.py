"""Global translation of authentication failures into HTTP responses.

Services raise the typed errors of app.services.auth_service; registering
one handler per error here keeps every router free of repeated
try/except-and-translate blocks and guarantees that each failure produces
the same status and body shape wherever it is raised (endpoint or
dependency).
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.security import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH
from app.services.auth_service import (
    AccountLockedError,
    AccountNotLinkedError,
    AuthenticationFailedError,
    GoogleAuthUnavailableError,
    InvalidResetTokenError,
    InvalidTokenError,
    RefreshRejectedError,
)

_REFRESH_REJECTED_DETAIL = "Session expired or invalid."

type _Handler = Callable[[Request, Exception], Awaitable[JSONResponse]]


def _json_error_handler(
    status_code: int, headers: dict[str, str] | None = None
) -> _Handler:
    async def handler(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status_code, content={"detail": str(exc)}, headers=headers
        )

    return handler


async def _refresh_rejected_handler(_request: Request, _exc: Exception) -> JSONResponse:
    # The reason (reuse, expiry, lock) stays server-side; the client only
    # learns that it has to sign in again, and its dead cookie is removed.
    response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": _REFRESH_REJECTED_DETAIL},
    )
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
    return response


def register_auth_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        InvalidTokenError,
        _json_error_handler(
            status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"}
        ),
    )
    app.add_exception_handler(
        AuthenticationFailedError, _json_error_handler(status.HTTP_401_UNAUTHORIZED)
    )
    app.add_exception_handler(RefreshRejectedError, _refresh_rejected_handler)
    app.add_exception_handler(
        AccountLockedError, _json_error_handler(status.HTTP_403_FORBIDDEN)
    )
    app.add_exception_handler(
        InvalidResetTokenError, _json_error_handler(status.HTTP_400_BAD_REQUEST)
    )
    app.add_exception_handler(
        AccountNotLinkedError, _json_error_handler(status.HTTP_404_NOT_FOUND)
    )
    app.add_exception_handler(
        GoogleAuthUnavailableError,
        _json_error_handler(status.HTTP_503_SERVICE_UNAVAILABLE),
    )
