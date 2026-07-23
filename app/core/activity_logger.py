import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

import jwt
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from app.core.security import ALGORITHM, SECRET_KEY
from app.db.database import SessionLocal
from app.models.client_user_agent import ClientUserAgent
from app.models.member import Member
from app.models.request_log import RequestLog

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

LOGGED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

SKIP_PATHS = {
    "/api/auth/refresh",
    "/",
    "/docs",
    "/openapi.json",
}

SKIP_SUFFIXES = (
    "/download",
    "/download/xs",
    "/download/sm",
    "/download/md",
    "/download/lg",
)

LOGGED_GET_PREFIXES = (
    "/api/standesdb/members/",
    "/api/standesdb/contacts/",
    "/api/standesdb/export",
    "/api/archive/dirs/",
    "/api/archive/files/",
)

LOGIN_PATHS = {"/api/auth/login", "/api/auth/google"}

SENSITIVE_KEYS = {"password", "password_confirmation", "credential"}


def _should_log(method: str, path: str) -> bool:
    if path in SKIP_PATHS:
        return False
    if any(path.endswith(s) for s in SKIP_SUFFIXES):
        return False
    if method in LOGGED_METHODS:
        return True
    if method == "GET":
        return any(path.startswith(p) for p in LOGGED_GET_PREFIXES)
    return False


def _sanitize_input(body: bytes, content_type: str = "") -> str | None:
    if not body:
        return None
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        if "application/x-www-form-urlencoded" in content_type:
            parsed = parse_qs(body.decode("utf-8", errors="replace"))
            data = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
        else:
            return None
    if isinstance(data, dict):
        for key in SENSITIVE_KEYS:
            if key in data:
                data[key] = "***"
    return json.dumps(data, ensure_ascii=False, default=str)


def _email_from_token(token_str: str) -> str | None:
    try:
        payload = jwt.decode(
            token_str,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"verify_exp": False},
        )
        return payload.get("sub")
    except (jwt.DecodeError, ValueError):
        return None


def _extract_email(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    return _email_from_token(auth_header[7:])


def _resolve_member_id(db: "Session", email: str | None) -> int | None:
    if not email:
        return None
    member = db.query(Member.id).filter(Member.email == email).first()
    return member.id if member else None


def _get_or_create_user_agent(
    db: "Session",
    ua_string: str,
) -> int | None:
    if not ua_string:
        return None
    existing = (
        db.query(ClientUserAgent).filter(ClientUserAgent.string == ua_string).first()
    )
    if existing:
        return existing.id
    new_ua = ClientUserAgent(string=ua_string)
    db.add(new_ua)
    db.flush()
    return new_ua.id


class ActivityLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        method = request.method
        path = request.url.path

        if not _should_log(method, path):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        if client_ip == "testclient":
            return await call_next(request)

        body = await request.body()
        email = _extract_email(request)

        response = await call_next(request)

        email, response_content, response = await self._handle_login_response(
            path, email, response
        )

        try:
            self._persist_log(
                request,
                method,
                path,
                client_ip,
                body,
                email,
                response_content,
                response,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Activity logging failed: %s", e)

        return response

    async def _handle_login_response(
        self,
        path: str,
        email: str | None,
        response: Response,
    ) -> tuple[str | None, str | None, Response]:
        if path not in LOGIN_PATHS:
            return email, None, response
        if not email and response.status_code == 200:
            email, response = await self._extract_email_from_response(response)
            return email, None, response
        if response.status_code == 401:
            response_content, response = await self._read_response_body(response)
            return email, response_content, response
        return email, None, response

    @staticmethod
    def _persist_log(
        request: Request,
        method: str,
        path: str,
        client_ip: str,
        body: bytes,
        email: str | None,
        response_content: str | None,
        response: Response,
    ) -> None:
        now = datetime.now(UTC)
        ua_string = request.headers.get("user-agent", "")

        db = SessionLocal()
        try:
            member_id = _resolve_member_id(db, email)
            ua_id = _get_or_create_user_agent(db, ua_string)
            log_entry = RequestLog(
                client_ip=client_ip,
                client_ips=request.headers.get("x-forwarded-for"),
                client_user_agent_id=ua_id,
                member_id=member_id,
                request_method=method,
                request_path=path,
                request_input=_sanitize_input(
                    body,
                    request.headers.get("content-type", ""),
                ),
                response_content=response_content,
                response_status=response.status_code,
                memory_usage=0,
                created_at=now,
                updated_at=now,
            )
            db.add(log_entry)
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    async def _extract_email_from_response(
        response: Response,
    ) -> tuple[str | None, Response]:
        body_bytes = b""
        if not isinstance(response, StreamingResponse):
            return None, response
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                body_bytes += chunk
            elif isinstance(chunk, str):
                body_bytes += chunk.encode("utf-8")
            else:
                body_bytes += bytes(chunk)

        email = None
        try:
            data = json.loads(body_bytes)
            token = data.get("access_token")
            if token:
                email = _email_from_token(token)
        except (json.JSONDecodeError, KeyError):
            pass

        new_response = Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
        return email, new_response

    @staticmethod
    async def _read_response_body(
        response: Response,
    ) -> tuple[str | None, Response]:
        body_bytes = b""
        if not isinstance(response, StreamingResponse):
            return None, response
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                body_bytes += chunk
            elif isinstance(chunk, str):
                body_bytes += chunk.encode("utf-8")
            else:
                body_bytes += bytes(chunk)

        new_response = Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
        try:
            return body_bytes.decode("utf-8"), new_response
        except UnicodeDecodeError:
            return None, new_response
