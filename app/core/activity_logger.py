import json
import logging
import resource
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, StreamingResponse

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.services import activity_log_service

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from pydantic import JsonValue
    from starlette.requests import Request

logger = logging.getLogger(__name__)

_SKIP_LOG_HEADER = "x-skip-request-log"

# The audit table stores at most 64 Ki characters of a response (see
# activity_log_service.cap_stored_size); a body that is larger than this is not
# stored at all, so it is streamed on instead of being collected in memory.
_MAX_CAPTURED_BODY_BYTES = 256 * 1024


async def _extract_request_input(request: Request) -> JsonValue:
    content_type = request.headers.get("content-type", "")
    body = await request.body()
    if not body:
        return None
    if "application/json" in content_type:
        try:
            return json.loads(body)
        except json.JSONDecodeError, UnicodeDecodeError:
            return None
    if (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
    ):
        form = await request.form()
        return {
            key: value for key, value in form.multi_items() if isinstance(value, str)
        }
    return None


def _try_parse_json(body: bytes) -> JsonValue:
    # Binary response bodies (generated XLSX/PDF exports, etc.) are common
    # in this app and reach this function on every request - json.loads()
    # rejects them with UnicodeDecodeError, not just JSONDecodeError, since
    # it decodes as UTF-8 internally before parsing.
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError, UnicodeDecodeError:
        return None


def _forwarded_ips(request: Request) -> list[str]:
    header = request.headers.get("x-forwarded-for")
    if not header:
        return [request.client.host] if request.client else []
    return [part.strip() for part in header.split(",") if part.strip()]


def _memory_usage_bytes() -> int:
    # Peak resident set size of the worker process - purely informational,
    # no business logic reads it. Linux containers report ru_maxrss in KiB.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def _persist_log(
    *,
    client_ip: str,
    client_ips: list[str],
    auth_header: str | None,
    request_method: str,
    request_path: str,
    request_input: JsonValue,
    response_status: int,
    response_content: JsonValue,
    memory_usage: int,
    user_agent_string: str | None,
) -> None:
    db = SessionLocal()
    try:
        activity_log_service.record_request(
            db,
            client_ip=client_ip,
            client_ips=client_ips,
            auth_header=auth_header,
            user_agent_string=user_agent_string,
            request_method=request_method,
            request_path=request_path,
            request_input=request_input,
            response_status=response_status,
            response_content=response_content,
            memory_usage=memory_usage,
        )
    except SQLAlchemyError, ValueError, TypeError:
        # A failed audit write (database error, or a payload the JSON column
        # rejects) must never break the actual response.
        logger.exception("Activity logging failed")
    finally:
        db.close()


async def _capture_body(
    body_iterator: AsyncIterator[bytes],
) -> tuple[list[bytes], AsyncIterator[bytes] | None]:
    """Read chunks until the body is complete or larger than the capture limit.

    Returns the chunks read so far and, when the limit was reached first, the
    iterator with the rest of the body (None when the body was read completely).
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in body_iterator:
        chunks.append(chunk)
        size += len(chunk)
        if size > _MAX_CAPTURED_BODY_BYTES:
            return chunks, body_iterator
    return chunks, None


async def _replay(
    chunks: list[bytes], rest: AsyncIterator[bytes]
) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk
    async for chunk in rest:
        yield chunk


def _is_test_client(request: Request) -> bool:
    return (
        get_settings().app_environment == "test"
        and request.client is not None
        and request.client.host == "testclient"
    )


class ActivityLoggingMiddleware(BaseHTTPMiddleware):
    """Global request/response audit logging - runs outside any request's
    own Depends() chain (ASGI middleware, not a route function), so it opens
    its own short-lived SessionLocal() per request. Logs every request
    except a small, business-neutral skip list (see
    app.services.activity_log_service.should_skip) - no endpoint allowlist,
    unlike this middleware's previous incarnation."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Starlette's TestClient reports its own host as "testclient" - a
        # pure passthrough here keeps the hundreds of unrelated tests using
        # it free of this middleware's DB side effects (it has its own
        # dedicated dispatch()-level tests instead).
        if _is_test_client(request):
            return await call_next(request)

        request_input = await _extract_request_input(request)
        response = await call_next(request)

        chunks, rest = await _capture_body(
            response.body_iterator  # type: ignore[attr-defined]
        )
        response_body = b"".join(chunks)

        skip_header_present = _SKIP_LOG_HEADER in response.headers
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() != _SKIP_LOG_HEADER
        }
        if rest is None:
            rebuilt: Response = Response(
                content=response_body,
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
                background=response.background,
            )
        else:
            rebuilt = StreamingResponse(
                _replay(chunks, rest),
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
                background=response.background,
            )

        if activity_log_service.should_skip(
            request.url.path, skip_header_present=skip_header_present
        ):
            return rebuilt

        client_ips = _forwarded_ips(request)
        await run_in_threadpool(
            _persist_log,
            client_ip=client_ips[0] if client_ips else "unknown",
            client_ips=client_ips,
            auth_header=request.headers.get("authorization"),
            request_method=request.method,
            request_path=request.url.path,
            request_input=request_input,
            response_status=response.status_code,
            response_content=None if rest else _try_parse_json(response_body),
            memory_usage=_memory_usage_bytes(),
            user_agent_string=request.headers.get("user-agent"),
        )
        return rebuilt
