"""Tests for the ActivityLoggingMiddleware."""

import asyncio
import json
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request
from starlette.responses import StreamingResponse

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
