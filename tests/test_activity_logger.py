"""Tests for the ActivityLoggingMiddleware."""

import json
from datetime import date, timedelta

import bcrypt

from app.core.activity_logger import (
    _email_from_token,
    _resolve_member_id,
    _sanitize_input,
    _should_log,
)
from app.core.security import create_access_token
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session


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
    """Middleware uses SessionLocal() directly — not testable via in-memory DB.
    These tests verify the dispatch logic via _should_log instead."""

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
