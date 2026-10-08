"""Tests for the ActivityLoggingMiddleware."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.exc import SQLAlchemyError
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from app.core.activity_logger import (
    ActivityLoggingMiddleware,
    _extract_request_input,
    _forwarded_ips,
    _try_parse_json,
)
from app.core.security import create_access_token
from app.models.member import Member
from app.models.org import Org
from app.models.request_log import RequestLog


def _streaming(
    content: bytes = b"", status_code: int = 200, headers: dict[str, str] | None = None
) -> StreamingResponse:
    """call_next()'s real Starlette implementation always returns a
    streaming-shaped response with a body_iterator, regardless of what the
    wrapped endpoint returned - this stand-in mirrors that contract for the
    dispatch() tests below."""

    async def _gen():
        yield content

    return StreamingResponse(_gen(), status_code=status_code, headers=headers)


def _make_request(headers: dict[str, str] | None = None, body: bytes = b"") -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "headers": raw_headers}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _make_asgi_request(
    method: str,
    path: str,
    client_host: str,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": (client_host, 12345),
        "headers": raw_headers,
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


class TestExtractRequestInput:
    def test_empty_body_returns_none(self) -> None:
        request = _make_request()
        assert asyncio.run(_extract_request_input(request)) is None

    def test_parses_json_body(self) -> None:
        body = json.dumps({"email": "a@b.at"}).encode()
        request = _make_request({"content-type": "application/json"}, body=body)
        assert asyncio.run(_extract_request_input(request)) == {"email": "a@b.at"}

    def test_invalid_json_returns_none(self) -> None:
        request = _make_request({"content-type": "application/json"}, body=b"not json")
        assert asyncio.run(_extract_request_input(request)) is None

    def test_parses_form_urlencoded_body(self) -> None:
        body = b"username=test%40vbw.at&password=secret"
        request = _make_request(
            {"content-type": "application/x-www-form-urlencoded"}, body=body
        )
        result = asyncio.run(_extract_request_input(request))
        assert result == {"username": "test@vbw.at", "password": "secret"}

    def test_unknown_content_type_returns_none(self) -> None:
        request = _make_request({"content-type": "application/pdf"}, body=b"%PDF-1.4")
        assert asyncio.run(_extract_request_input(request)) is None


class TestTryParseJson:
    def test_empty_body_returns_none(self) -> None:
        assert _try_parse_json(b"") is None

    def test_parses_valid_json(self) -> None:
        assert _try_parse_json(b'{"ok": true}') == {"ok": True}

    def test_invalid_json_returns_none(self) -> None:
        assert _try_parse_json(b"not json") is None


class TestForwardedIps:
    def test_falls_back_to_client_host(self) -> None:
        request = _make_asgi_request("GET", "/api/x", "203.0.113.7")
        assert _forwarded_ips(request) == ["203.0.113.7"]

    def test_parses_forwarded_chain(self) -> None:
        request = _make_asgi_request(
            "GET",
            "/api/x",
            "203.0.113.7",
            headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"},
        )
        assert _forwarded_ips(request) == ["1.2.3.4", "5.6.7.8"]


class TestDispatch:
    @patch("app.core.activity_logger.SessionLocal")
    def test_logs_a_plain_request(self, mock_session_local, db_session) -> None:
        mock_session_local.return_value = db_session
        request = _make_asgi_request(
            "GET",
            "/api/standesdb/members",
            "203.0.113.7",
        )

        async def call_next(_req):
            return _streaming(b'{"total": 0}', status_code=200)

        response = asyncio.run(
            ActivityLoggingMiddleware(app=None).dispatch(request, call_next)
        )

        assert response.status_code == 200
        entry = db_session.query(RequestLog).one()
        assert entry.client_ip == "203.0.113.7"
        assert entry.request_path == "/api/standesdb/members"
        assert entry.response_status == 200

    @patch("app.core.activity_logger.SessionLocal")
    def test_redacts_sensitive_request_fields(
        self, mock_session_local, db_session
    ) -> None:
        mock_session_local.return_value = db_session
        request = _make_asgi_request(
            "POST",
            "/api/auth/login",
            "203.0.113.7",
            body=json.dumps({"password": "secret", "email": "a@b.at"}).encode(),
            headers={"content-type": "application/json"},
        )

        async def call_next(_req):
            return _streaming(status_code=401)

        asyncio.run(ActivityLoggingMiddleware(app=None).dispatch(request, call_next))

        entry = db_session.query(RequestLog).one()
        assert entry.request_input == {"password": "***", "email": "a@b.at"}

    @patch("app.core.activity_logger.SessionLocal")
    def test_resolves_member_from_bearer_token(
        self, mock_session_local, db_session
    ) -> None:
        mock_session_local.return_value = db_session
        db_session.add(Org(id="vbw", label="VBW", order=1))
        member = Member(
            email="found@vbw.at", vorname="Test", nachname="User", org_id="vbw"
        )
        db_session.add(member)
        db_session.commit()
        member_id = member.id  # captured before _persist_log's db.close() detaches it
        token, _ = create_access_token(subject="found@vbw.at")

        request = _make_asgi_request(
            "GET",
            "/api/standesdb/members/1",
            "203.0.113.7",
            headers={"authorization": f"Bearer {token}"},
        )

        async def call_next(_req):
            return _streaming(status_code=200)

        asyncio.run(ActivityLoggingMiddleware(app=None).dispatch(request, call_next))

        entry = db_session.query(RequestLog).one()
        assert entry.member_id == member_id

    def test_testclient_host_is_never_logged(self, db_session) -> None:
        # No SessionLocal patch at all - if this reached _persist_log, it
        # would try to open a real (non-test) DB session and fail/hang.
        request = _make_asgi_request("POST", "/api/standesdb/members", "testclient")

        async def call_next(_req):
            return _streaming(status_code=200)

        response = asyncio.run(
            ActivityLoggingMiddleware(app=None).dispatch(request, call_next)
        )

        assert response.status_code == 200

    def test_root_health_check_is_never_logged(self, db_session) -> None:
        # No SessionLocal patch at all - if this reached _persist_log, it
        # would try to open a real (non-test) DB session and fail/hang.
        request = _make_asgi_request("GET", "/", "203.0.113.7")

        async def call_next(_req):
            return _streaming(status_code=200)

        response = asyncio.run(
            ActivityLoggingMiddleware(app=None).dispatch(request, call_next)
        )

        assert response.status_code == 200

    def test_opt_out_header_skips_logging(self, db_session) -> None:
        request = _make_asgi_request("GET", "/api/x", "203.0.113.7")

        async def call_next(_req):
            return _streaming(status_code=200, headers={"x-skip-request-log": "1"})

        response = asyncio.run(
            ActivityLoggingMiddleware(app=None).dispatch(request, call_next)
        )

        assert response.status_code == 200
        assert "x-skip-request-log" not in response.headers

    @patch("app.core.activity_logger.SessionLocal")
    def test_logging_failure_does_not_break_the_response(
        self, mock_session_local, db_session
    ) -> None:
        mock_session_local.return_value = db_session
        request = _make_asgi_request("POST", "/api/standesdb/members", "203.0.113.7")

        async def call_next(_req):
            return _streaming(status_code=200)

        with patch.object(db_session, "commit", side_effect=SQLAlchemyError("boom")):
            response = asyncio.run(
                ActivityLoggingMiddleware(app=None).dispatch(request, call_next)
            )

        assert response.status_code == 200

    @patch("app.core.activity_logger.SessionLocal")
    def test_response_body_is_passed_through_unchanged(
        self, mock_session_local, db_session
    ) -> None:
        mock_session_local.return_value = db_session
        request = _make_asgi_request("GET", "/api/standesdb/members", "203.0.113.7")

        async def call_next(_req):
            return _streaming(b'{"total": 3}', status_code=200)

        response = asyncio.run(
            ActivityLoggingMiddleware(app=None).dispatch(request, call_next)
        )

        # dispatch() rebuilds a plain (non-streaming) Response after fully
        # reading call_next()'s body - .body is the direct byte payload.
        assert response.body == b'{"total": 3}'

    @patch("app.core.activity_logger.SessionLocal")
    def test_testclient_host_is_logged_outside_the_test_environment(
        self, mock_session_local, db_session
    ) -> None:
        """The host name of the test client is a bypass for tests only; a
        request that reports it in any other stage is logged like any other."""
        mock_session_local.return_value = db_session
        request = _make_asgi_request("POST", "/api/standesdb/members", "testclient")

        async def call_next(_req):
            return _streaming(b'{"ok": true}', status_code=200)

        with patch(
            "app.core.activity_logger.get_settings",
            return_value=SimpleNamespace(app_environment="production"),
        ):
            asyncio.run(
                ActivityLoggingMiddleware(app=None).dispatch(request, call_next)
            )

        assert db_session.query(RequestLog).one().client_ip == "testclient"

    @pytest.mark.parametrize("error", [ValueError("bad"), TypeError("bad")])
    @patch("app.core.activity_logger.SessionLocal")
    def test_a_payload_the_json_column_rejects_does_not_break_the_response(
        self, mock_session_local, db_session, error
    ) -> None:
        mock_session_local.return_value = db_session
        request = _make_asgi_request("POST", "/api/standesdb/members", "203.0.113.7")

        async def call_next(_req):
            return _streaming(status_code=200)

        with patch.object(db_session, "commit", side_effect=error):
            response = asyncio.run(
                ActivityLoggingMiddleware(app=None).dispatch(request, call_next)
            )

        assert response.status_code == 200

    @patch("app.core.activity_logger.SessionLocal")
    def test_a_programming_error_is_not_swallowed(
        self, mock_session_local, db_session
    ) -> None:
        mock_session_local.return_value = db_session
        request = _make_asgi_request("POST", "/api/standesdb/members", "203.0.113.7")

        async def call_next(_req):
            return _streaming(status_code=200)

        with (
            patch.object(db_session, "commit", side_effect=RuntimeError("bug")),
            pytest.raises(RuntimeError, match="bug"),
        ):
            asyncio.run(
                ActivityLoggingMiddleware(app=None).dispatch(request, call_next)
            )

    @patch("app.core.activity_logger.SessionLocal")
    def test_a_large_body_is_streamed_on_and_not_stored(
        self, mock_session_local, db_session
    ) -> None:
        mock_session_local.return_value = db_session
        request = _make_asgi_request("GET", "/api/archive/download", "203.0.113.7")
        chunk = b"x" * 65_536
        chunks_read = 0
        background = BackgroundTask(lambda: None)

        async def body():
            nonlocal chunks_read
            for _ in range(200):
                chunks_read += 1
                yield chunk

        async def call_next(_req):
            return StreamingResponse(
                body(),
                status_code=206,
                media_type="application/pdf",
                headers={"x-kept": "yes"},
                background=background,
            )

        async def run() -> tuple[Response, int, int]:
            response = await ActivityLoggingMiddleware(app=None).dispatch(
                request, call_next
            )
            read_before_sending = chunks_read
            received = 0
            async for part in response.body_iterator:  # type: ignore[attr-defined]
                received += len(part)
            return response, read_before_sending, received

        with patch("app.core.activity_logger._try_parse_json") as parse:
            response, read_before_sending, received = asyncio.run(run())

        # About 13 MB in total, of which only the capture limit was held back.
        assert read_before_sending < 10
        assert received == 200 * len(chunk)
        assert response.status_code == 206
        assert response.media_type == "application/pdf"
        assert response.headers["x-kept"] == "yes"
        assert response.background is background
        parse.assert_not_called()
        assert db_session.query(RequestLog).one().response_content is None

    def test_a_request_without_a_client_address_is_not_a_test_request(
        self, db_session
    ) -> None:
        request = _make_asgi_request("GET", "/", "203.0.113.7")
        request.scope["client"] = None

        async def call_next(_req):
            return _streaming(status_code=200)

        response = asyncio.run(
            ActivityLoggingMiddleware(app=None).dispatch(request, call_next)
        )

        assert response.status_code == 200

    @patch("app.core.activity_logger.SessionLocal")
    def test_a_body_at_the_capture_limit_is_still_collected(
        self, mock_session_local, db_session
    ) -> None:
        mock_session_local.return_value = db_session
        request = _make_asgi_request("GET", "/api/standesdb/members", "203.0.113.7")
        content = b"y" * (256 * 1024)

        async def call_next(_req):
            return _streaming(content, status_code=200)

        response = asyncio.run(
            ActivityLoggingMiddleware(app=None).dispatch(request, call_next)
        )

        assert not isinstance(response, StreamingResponse)
        assert response.body == content

    @patch("app.core.activity_logger.SessionLocal")
    def test_a_body_one_byte_over_the_limit_is_streamed_unchanged(
        self, mock_session_local, db_session
    ) -> None:
        mock_session_local.return_value = db_session
        request = _make_asgi_request("GET", "/api/standesdb/members", "203.0.113.7")
        content = b"y" * (256 * 1024 + 1)

        async def call_next(_req):
            return _streaming(content, status_code=200)

        async def run() -> tuple[bool, bytes]:
            response = await ActivityLoggingMiddleware(app=None).dispatch(
                request, call_next
            )
            parts = [part async for part in response.body_iterator]  # type: ignore[attr-defined]
            return isinstance(response, StreamingResponse), b"".join(parts)

        streamed, received = asyncio.run(run())

        assert streamed
        assert received == content
        assert db_session.query(RequestLog).one().response_content is None

    @patch("app.core.activity_logger.SessionLocal")
    def test_a_small_json_body_is_still_stored(
        self, mock_session_local, db_session
    ) -> None:
        mock_session_local.return_value = db_session
        request = _make_asgi_request("GET", "/api/standesdb/members", "203.0.113.7")

        async def call_next(_req):
            return _streaming(b'{"total": 3}', status_code=200)

        asyncio.run(ActivityLoggingMiddleware(app=None).dispatch(request, call_next))

        assert db_session.query(RequestLog).one().response_content == {"total": 3}
