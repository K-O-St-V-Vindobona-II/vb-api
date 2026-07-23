"""Tests for the ActivityLoggingMiddleware."""

import asyncio
import json
from datetime import date, timedelta
from unittest.mock import patch

import bcrypt
import pytest
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from app.core.activity_logger import (
    ActivityLoggingMiddleware,
    _email_from_token,
    _get_or_create_user_agent,
    _resolve_member_id,
    _sanitize_input,
    _should_log,
)
from app.core.security import create_access_token
from app.models.client_user_agent import ClientUserAgent
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.request_log import RequestLog
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session


def _make_request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw_headers})


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            State(id="bi", label="Bandinhaber", order=1),
            Role(id="standesfuehrer", group="chc", label="Standesführer", order=1),
        ]
    )
    db.commit()


def _login(db):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="admin@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Admin",
        nachname="Test",
        org_id="vbw",
        state_id="bi",
    )
    db.add(m)
    db.commit()
    db.add(
        MemberRole(
            member_id=m.id,
            role_id="standesfuehrer",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}, m


# --- _should_log unit tests ---


class TestShouldLog:
    def test_post_is_logged(self):
        assert _should_log("POST", "/api/standesdb/members") is True

    def test_put_is_logged(self):
        assert _should_log("PUT", "/api/standesdb/members/1") is True

    def test_patch_is_logged(self):
        assert _should_log("PATCH", "/api/archive/dirs/1/restore") is True

    def test_delete_is_logged(self):
        assert _should_log("DELETE", "/api/archive/files/1") is True

    def test_get_not_logged_by_default(self):
        assert _should_log("GET", "/api/p4x/admin/accounts") is False

    def test_get_export_is_logged(self):
        assert _should_log("GET", "/api/standesdb/export") is True
        assert _should_log("GET", "/api/standesdb/export/config") is True

    def test_get_archive_files_is_logged(self):
        assert _should_log("GET", "/api/archive/files/1") is True

    def test_download_paths_not_logged(self):
        assert _should_log("GET", "/api/archive/files/1/download") is False
        assert _should_log("GET", "/api/archive/files/42/download/xs") is False
        assert _should_log("GET", "/api/archive/files/42/download/md") is False
        assert _should_log("GET", "/api/standesdb/members/1/images/2/download") is False

    def test_get_members_is_logged(self):
        assert _should_log("GET", "/api/standesdb/members/42") is True

    def test_get_contacts_is_logged(self):
        assert _should_log("GET", "/api/standesdb/contacts/42") is True

    def test_get_archive_dirs_is_logged(self):
        assert _should_log("GET", "/api/archive/dirs/5") is True

    def test_get_list_endpoints_not_logged(self):
        assert _should_log("GET", "/api/standesdb/members") is False
        assert _should_log("GET", "/api/standesdb/contacts") is False
        assert _should_log("GET", "/api/archive/dirs") is False

    def test_skip_paths(self):
        assert _should_log("POST", "/api/auth/refresh") is False
        assert _should_log("GET", "/") is False
        assert _should_log("GET", "/docs") is False

    def test_get_search_not_logged(self):
        assert _should_log("GET", "/api/standesdb/search") is False
        assert _should_log("GET", "/api/p4x/partner/search") is False

    def test_other_methods_not_logged(self):
        assert _should_log("OPTIONS", "/api/standesdb/members") is False
        assert _should_log("HEAD", "/api/standesdb/export") is False


# --- _sanitize_input unit tests ---


class TestSanitizeInput:
    def test_empty_body(self):
        assert _sanitize_input(b"") is None

    def test_sanitizes_password(self):
        body = json.dumps({"email": "a@b.at", "password": "secret123"}).encode()
        result = _sanitize_input(body)
        data = json.loads(result)
        assert data["password"] == "***"
        assert data["email"] == "a@b.at"

    def test_sanitizes_credential(self):
        body = json.dumps({"credential": "google-token-xyz"}).encode()
        result = _sanitize_input(body)
        data = json.loads(result)
        assert data["credential"] == "***"

    def test_non_json_body(self):
        assert _sanitize_input(b"not json") is None

    def test_form_data_login(self):
        body = b"username=test%40vbw.at&password=secret123"
        result = _sanitize_input(body, "application/x-www-form-urlencoded")
        data = json.loads(result)
        assert data["username"] == "test@vbw.at"
        assert data["password"] == "***"

    def test_form_data_without_content_type(self):
        assert _sanitize_input(b"username=test%40vbw.at&password=x") is None


# --- _email_from_token unit tests ---


class TestEmailFromToken:
    def test_extracts_email_from_valid_token(self):
        token, _ = create_access_token(subject="admin@vbw.at")
        assert _email_from_token(token) == "admin@vbw.at"

    def test_returns_none_for_invalid_token(self):
        assert _email_from_token("not.a.valid.jwt") is None

    def test_returns_none_for_empty_string(self):
        assert _email_from_token("") is None

    def test_extracts_email_with_expired_token(self):
        token, _ = create_access_token(
            subject="test@vbw.at",
            expires_delta=timedelta(seconds=-1),
        )
        assert _email_from_token(token) == "test@vbw.at"


# --- _resolve_member_id unit tests ---


class TestResolveMemberId:
    def test_returns_none_for_none_email(self, db_session):
        assert _resolve_member_id(db_session, None) is None

    def test_returns_none_for_unknown_email(self, db_session):
        _seed(db_session)
        assert _resolve_member_id(db_session, "unknown@vbw.at") is None

    def test_resolves_existing_member(self, db_session):
        _seed(db_session)
        member = Member(
            email="found@vbw.at",
            auth_password="x",
            auth_locked=False,
            vorname="Test",
            nachname="User",
            org_id="vbw",
            state_id="bi",
        )
        db_session.add(member)
        db_session.commit()
        assert _resolve_member_id(db_session, "found@vbw.at") == member.id


# --- Integration: middleware creates log entries ---


class TestMiddlewareDispatch:
    """A few extra _should_log sanity checks under the dispatch-relevant
    method/path combinations; the real dispatch() flow is exercised by
    TestPersistLog/TestHandleLoginResponse/TestDispatch below."""

    def test_write_methods_always_logged(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert _should_log(method, "/api/some/endpoint") is True

    def test_refresh_never_logged(self):
        assert _should_log("POST", "/api/auth/refresh") is False

    def test_export_get_logged(self):
        assert _should_log("GET", "/api/standesdb/export/config") is True

    def test_archive_download_not_logged(self):
        assert _should_log("GET", "/api/archive/files/42/download/md") is False
        assert _should_log("GET", "/api/archive/files/42/download/lg") is False


# --- _get_or_create_user_agent unit tests ---


class TestGetOrCreateUserAgent:
    def test_returns_none_for_empty_string(self, db_session):
        assert _get_or_create_user_agent(db_session, "") is None

    def test_creates_new_user_agent(self, db_session):
        ua_id = _get_or_create_user_agent(db_session, "Mozilla/5.0 Test")
        assert ua_id is not None
        row = db_session.get(ClientUserAgent, ua_id)
        assert row is not None
        assert row.string == "Mozilla/5.0 Test"

    def test_reuses_existing_user_agent(self, db_session):
        first_id = _get_or_create_user_agent(db_session, "Mozilla/5.0 Test")
        second_id = _get_or_create_user_agent(db_session, "Mozilla/5.0 Test")
        assert first_id == second_id


# --- _persist_log unit tests ---


class TestPersistLog:
    @patch("app.core.activity_logger.SessionLocal")
    def test_persists_request_with_sanitized_input(
        self, mock_session_local, db_session
    ):
        mock_session_local.return_value = db_session
        request = _make_request(
            {"user-agent": "pytest-agent", "content-type": "application/json"}
        )
        body = json.dumps({"password": "secret", "email": "a@b.at"}).encode()

        ActivityLoggingMiddleware._persist_log(
            request,
            "POST",
            "/api/auth/login",
            "203.0.113.5",
            body,
            "a@b.at",
            None,
            Response(status_code=200),
        )

        entry = db_session.query(RequestLog).one()
        assert entry.client_ip == "203.0.113.5"
        assert entry.request_method == "POST"
        assert entry.request_path == "/api/auth/login"
        assert entry.response_status == 200
        assert json.loads(entry.request_input)["password"] == "***"
        assert entry.client_user_agent_id is not None

    @patch("app.core.activity_logger.SessionLocal")
    def test_resolves_member_id_from_email(self, mock_session_local, db_session):
        mock_session_local.return_value = db_session
        _seed(db_session)
        member = Member(
            email="found@vbw.at",
            auth_password="x",
            auth_locked=False,
            vorname="Test",
            nachname="User",
            org_id="vbw",
            state_id="bi",
        )
        db_session.add(member)
        db_session.commit()
        member_id = member.id  # captured before _persist_log's db.close() detaches it

        request = _make_request()
        ActivityLoggingMiddleware._persist_log(
            request,
            "GET",
            "/api/standesdb/members/1",
            "1.2.3.4",
            b"",
            "found@vbw.at",
            None,
            Response(status_code=200),
        )

        entry = db_session.query(RequestLog).one()
        assert entry.member_id == member_id

    @patch("app.core.activity_logger.SessionLocal")
    def test_rolls_back_and_reraises_on_db_error(self, mock_session_local, db_session):
        mock_session_local.return_value = db_session
        request = _make_request()
        with (
            patch.object(db_session, "commit", side_effect=SQLAlchemyError("boom")),
            pytest.raises(SQLAlchemyError),
        ):
            ActivityLoggingMiddleware._persist_log(
                request,
                "POST",
                "/api/x",
                "1.2.3.4",
                b"",
                None,
                None,
                Response(status_code=500),
            )


# --- _extract_email_from_response / _read_response_body unit tests ---


class TestExtractEmailFromResponse:
    def test_non_streaming_response_returns_none(self):
        response = Response(content="plain", status_code=200)
        email, resp = asyncio.run(
            ActivityLoggingMiddleware._extract_email_from_response(response)
        )
        assert email is None
        assert resp is response

    def test_extracts_email_from_access_token(self):
        token, _ = create_access_token(subject="new@vbw.at")
        body = json.dumps({"access_token": token}).encode()

        async def _gen():
            yield body

        response = StreamingResponse(_gen(), status_code=200)
        email, new_response = asyncio.run(
            ActivityLoggingMiddleware._extract_email_from_response(response)
        )
        assert email == "new@vbw.at"
        assert isinstance(new_response, Response)

    def test_missing_access_token_returns_none(self):
        async def _gen():
            yield b'{"foo": "bar"}'

        response = StreamingResponse(_gen(), status_code=200)
        email, _resp = asyncio.run(
            ActivityLoggingMiddleware._extract_email_from_response(response)
        )
        assert email is None

    def test_invalid_json_returns_none(self):
        async def _gen():
            yield b"not json"

        response = StreamingResponse(_gen(), status_code=200)
        email, _resp = asyncio.run(
            ActivityLoggingMiddleware._extract_email_from_response(response)
        )
        assert email is None


class TestReadResponseBody:
    def test_non_streaming_response_returns_none(self):
        response = Response(content="plain", status_code=401)
        content, resp = asyncio.run(
            ActivityLoggingMiddleware._read_response_body(response)
        )
        assert content is None
        assert resp is response

    def test_reads_utf8_body(self):
        async def _gen():
            yield b'{"detail": "Invalid credentials"}'

        response = StreamingResponse(_gen(), status_code=401)
        content, _resp = asyncio.run(
            ActivityLoggingMiddleware._read_response_body(response)
        )
        assert content == '{"detail": "Invalid credentials"}'

    def test_falls_back_to_none_on_decode_error(self):
        async def _gen():
            yield b"\xff\xfe not valid utf-8"

        response = StreamingResponse(_gen(), status_code=401)
        content, _resp = asyncio.run(
            ActivityLoggingMiddleware._read_response_body(response)
        )
        assert content is None


# --- _handle_login_response unit tests ---


class TestHandleLoginResponse:
    @staticmethod
    def _middleware() -> ActivityLoggingMiddleware:
        return ActivityLoggingMiddleware(app=None)

    def test_non_login_path_passthrough(self):
        response = Response(status_code=200)
        result = asyncio.run(
            self._middleware()._handle_login_response(
                "/api/standesdb/members", "a@b.at", response
            )
        )
        assert result == ("a@b.at", None, response)

    def test_login_success_extracts_email(self):
        token, _ = create_access_token(subject="new@vbw.at")

        async def _gen():
            yield json.dumps({"access_token": token}).encode()

        response = StreamingResponse(_gen(), status_code=200)
        email, content, _resp = asyncio.run(
            self._middleware()._handle_login_response("/api/auth/login", None, response)
        )
        assert email == "new@vbw.at"
        assert content is None

    def test_login_failure_captures_response_body(self):
        async def _gen():
            yield b'{"detail": "Invalid credentials"}'

        response = StreamingResponse(_gen(), status_code=401)
        email, content, _resp = asyncio.run(
            self._middleware()._handle_login_response("/api/auth/login", None, response)
        )
        assert email is None
        assert content == '{"detail": "Invalid credentials"}'

    def test_login_path_with_known_email_passthrough(self):
        response = Response(status_code=422)
        result = asyncio.run(
            self._middleware()._handle_login_response(
                "/api/auth/login", "known@vbw.at", response
            )
        )
        assert result == ("known@vbw.at", None, response)


# --- dispatch() integration tests ---


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


class TestDispatch:
    @patch("app.core.activity_logger.SessionLocal")
    def test_skips_persisting_for_testclient_host(self, mock_session_local, db_session):
        mock_session_local.return_value = db_session
        request = _make_asgi_request("POST", "/api/standesdb/members", "testclient")

        async def call_next(_req):
            return Response(status_code=200)

        middleware = ActivityLoggingMiddleware(app=None)
        response = asyncio.run(middleware.dispatch(request, call_next))

        assert response.status_code == 200
        assert db_session.query(RequestLog).count() == 0

    @patch("app.core.activity_logger.SessionLocal")
    def test_persists_loggable_request(self, mock_session_local, db_session):
        mock_session_local.return_value = db_session
        request = _make_asgi_request(
            "POST",
            "/api/standesdb/members",
            "203.0.113.7",
            body=json.dumps({"password": "secret"}).encode(),
            headers={"content-type": "application/json"},
        )

        async def call_next(_req):
            return Response(status_code=201)

        middleware = ActivityLoggingMiddleware(app=None)
        response = asyncio.run(middleware.dispatch(request, call_next))

        assert response.status_code == 201
        entry = db_session.query(RequestLog).one()
        assert entry.client_ip == "203.0.113.7"
        assert entry.response_status == 201
        assert json.loads(entry.request_input)["password"] == "***"

    def test_non_loggable_request_skips_persisting_entirely(self, db_session):
        # No SessionLocal patch at all — if this reached _persist_log, it
        # would try to open a real (non-test) DB session and fail/hang.
        request = _make_asgi_request("GET", "/api/standesdb/members", "203.0.113.7")

        async def call_next(_req):
            return Response(status_code=200)

        middleware = ActivityLoggingMiddleware(app=None)
        response = asyncio.run(middleware.dispatch(request, call_next))

        assert response.status_code == 200

    @patch("app.core.activity_logger.SessionLocal")
    def test_logging_failure_does_not_break_the_response(
        self, mock_session_local, db_session
    ):
        mock_session_local.return_value = db_session
        request = _make_asgi_request("POST", "/api/standesdb/members", "203.0.113.7")

        async def call_next(_req):
            return Response(status_code=200)

        middleware = ActivityLoggingMiddleware(app=None)
        with patch.object(db_session, "commit", side_effect=SQLAlchemyError("boom")):
            response = asyncio.run(middleware.dispatch(request, call_next))

        assert response.status_code == 200
