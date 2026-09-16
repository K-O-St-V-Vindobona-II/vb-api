"""Tests for the Tracking module (sent emails + email templates)."""

import re
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import bcrypt

from app.models.client_user_agent import ClientUserAgent
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.sent_email import SentEmail
from app.models.state import State
from app.services.auth_service import create_user_session
from app.services.tracking_service import EMAIL_TEMPLATE_REGISTRY


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            State(id="bi", label="Bandinhaber", order=1),
            Role(
                id="internetreferent",
                group="funktion",
                label="Internetreferent",
                order=1,
            ),
        ]
    )
    db.commit()


def _login_admin(db):
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
            role_id="internetreferent",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}, m


def _login_unprivileged(db):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="user@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Normal",
        nachname="User",
        org_id="vbw",
        state_id="bi",
    )
    db.add(m)
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}


def _insert_sent_email(
    db, template_key: str, subject: str = "Test", to: str = "a@b.at"
):
    now = datetime.now(UTC)
    e = SentEmail(
        mail_from="test@vb.at",
        to=to,
        subject=subject,
        body="<p>test</p>",
        headers=template_key,
        mailer="smtp",
        created_at=now,
        updated_at=now,
    )
    db.add(e)
    db.commit()
    return e


# --- Email Templates ---


class TestEmailTemplates:
    def test_returns_all_registry_entries_empty_db(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get("/api/tracking/sent-emails/templates", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 11
        keys = {t["template_key"] for t in data}
        assert keys == {
            "password-reset",
            "entry-changed",
            "member-change-request-submitted",
            "member-change-request-resolved",
            "own-image-changed",
            "birthday",
            "debtor_reminder",
            "chronicles",
            "archive_health_check",
            "standesdb_health_check",
            "public-contact-form",
        }
        for t in data:
            assert t["count"] == 0
            assert t["last_sent"] is None
            assert t["source_location"]
            assert t["template_name"]

    def test_counts_increase_with_data(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        _insert_sent_email(db_session, "password-reset")
        _insert_sent_email(db_session, "password-reset")
        _insert_sent_email(db_session, "entry-changed")
        resp = client.get("/api/tracking/sent-emails/templates", headers=headers)
        data = resp.json()
        counts = {t["template_key"]: t["count"] for t in data}
        assert counts["password-reset"] == 2
        assert counts["entry-changed"] == 1
        assert "p4x-summary" not in counts

    def test_unknown_template_keys_excluded(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        _insert_sent_email(db_session, "legacy-junk")
        resp = client.get("/api/tracking/sent-emails/templates", headers=headers)
        data = resp.json()
        assert len(data) == 11
        keys = {t["template_key"] for t in data}
        assert "legacy-junk" not in keys

    def test_requires_permission(self, client, db_session):
        _seed(db_session)
        headers = _login_unprivileged(db_session)
        resp = client.get("/api/tracking/sent-emails/templates", headers=headers)
        assert resp.status_code == 403


# --- Template Registry Guard ---


class TestTemplateRegistryGuard:
    def test_all_template_keys_in_code_are_registered(self):
        registry_keys = {t["key"] for t in EMAIL_TEMPLATE_REGISTRY}

        scan_dirs = [
            Path("app/core"),
            Path("app/api"),
            Path("app/services"),
        ]
        pattern = re.compile(r'template_key\s*=\s*["\']([a-z0-9_-]+)["\']')
        found_keys: set[str] = set()

        for scan_dir in scan_dirs:
            if not scan_dir.exists():
                continue
            for py_file in scan_dir.rglob("*.py"):
                content = py_file.read_text()
                found_keys.update(pattern.findall(content))

        found_keys.discard("generic")

        missing = found_keys - registry_keys
        assert not missing, (
            f"template_key(s) {missing} found in code but missing "
            f"from EMAIL_TEMPLATE_REGISTRY in tracking.py"
        )


# --- Template Preview ---


class TestTemplatePreview:
    def test_preview_all_templates(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        for entry in EMAIL_TEMPLATE_REGISTRY:
            key = entry["key"]
            resp = client.get(
                f"/api/tracking/sent-emails/templates/{key}/preview",
                headers=headers,
            )
            assert resp.status_code == 200, f"Preview failed for {key}"
            data = resp.json()
            assert data["template_key"] == key
            assert data["template_name"] == entry["name"]
            assert len(data["html"]) > 50

    def test_preview_unknown_template_returns_404(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get(
            "/api/tracking/sent-emails/templates/nonexistent/preview",
            headers=headers,
        )
        assert resp.status_code == 404

    def test_preview_requires_permission(self, client, db_session):
        _seed(db_session)
        headers = _login_unprivileged(db_session)
        resp = client.get(
            "/api/tracking/sent-emails/templates/birthday/preview",
            headers=headers,
        )
        assert resp.status_code == 403


# --- Sent Emails List ---


class TestSentEmailsList:
    def test_pagination(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        for i in range(30):
            _insert_sent_email(db_session, "password-reset", subject=f"Email {i}")
        resp = client.get(
            "/api/tracking/sent-emails?page=1&page_size=10", headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 30
        assert len(data["items"]) == 10
        assert data["page"] == 1

    def test_search_filter(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        _insert_sent_email(db_session, "password-reset", subject="Passwort Reset")
        _insert_sent_email(db_session, "entry-changed", subject="Datenbankänderung")
        resp = client.get("/api/tracking/sent-emails?search=Passwort", headers=headers)
        data = resp.json()
        assert data["total"] == 1
        assert "Passwort" in data["items"][0]["subject"]

    def test_year_month_filter(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        now = datetime.now(UTC)
        _insert_sent_email(db_session, "password-reset")
        resp = client.get(
            f"/api/tracking/sent-emails?year={now.year}&month={now.month}",
            headers=headers,
        )
        data = resp.json()
        assert data["total"] >= 1


# --- Sent Email Detail ---


class TestSentEmailDetail:
    def test_returns_detail(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        email = _insert_sent_email(db_session, "password-reset", subject="Detail Test")
        resp = client.get(f"/api/tracking/sent-emails/{email.id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["subject"] == "Detail Test"
        assert data["body"] == "<p>test</p>"

    def test_404_for_missing(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get(f"/api/tracking/sent-emails/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404


# --- Timezone ---


class TestTimezone:
    def test_datetime_has_utc_marker(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        _insert_sent_email(db_session, "password-reset")
        resp = client.get(
            "/api/tracking/sent-emails?page=1&page_size=1", headers=headers
        )
        data = resp.json()
        created_at = data["items"][0]["created_at"]
        assert created_at.endswith(("+00:00", "Z"))

    def test_templates_last_sent_has_utc(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        _insert_sent_email(db_session, "password-reset")
        resp = client.get("/api/tracking/sent-emails/templates", headers=headers)
        data = resp.json()
        for t in data:
            if t["last_sent"]:
                assert t["last_sent"].endswith("+00:00") or t["last_sent"].endswith("Z")


# --- Tracking Config ---


class TestTrackingConfig:
    def test_returns_retention_months(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get("/api/tracking/config", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "retention_months" in data
        assert isinstance(data["retention_months"], int)

    def test_requires_permission(self, client, db_session):
        _seed(db_session)
        headers = _login_unprivileged(db_session)
        resp = client.get("/api/tracking/config", headers=headers)
        assert resp.status_code == 403


# --- Coverage: December month boundary ---


class TestSentEmailsDecemberBoundary:
    def test_december_filter(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        dec_email = SentEmail(
            mail_from="test@vb.at",
            to="a@b.at",
            subject="December",
            body="<p>dec</p>",
            headers="password-reset",
            mailer="smtp",
            created_at=datetime(2025, 12, 15, tzinfo=UTC),
            updated_at=datetime(2025, 12, 15, tzinfo=UTC),
        )
        db_session.add(dec_email)
        db_session.commit()
        resp = client.get(
            "/api/tracking/sent-emails?year=2025&month=12",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1


# --- Coverage: Template preview without preview data ---


class TestTemplatePreviewNoData:
    def test_template_without_preview_data_returns_404(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        fake_entry = {
            "key": "test-no-preview",
            "name": "No Preview",
            "source": "test",
            "file": "birthday.html",
        }
        patched_registry = [*EMAIL_TEMPLATE_REGISTRY, fake_entry]
        with patch(
            "app.services.tracking_service.EMAIL_TEMPLATE_REGISTRY",
            patched_registry,
        ):
            resp = client.get(
                "/api/tracking/sent-emails/templates/test-no-preview/preview",
                headers=headers,
            )
        assert resp.status_code == 404
        assert "Vorschaudaten" in resp.json()["detail"]


class TestSentEmailUuidDefault:
    """Guards the UUID-PK migration's central assumption (see
    22fc473b0891_sent_emails_and_scheduled_task_runs_ids_.py): id is
    generated exclusively by the database's uuidv7() server_default, not
    by any Python-side default."""

    def test_id_defaults_to_a_valid_uuid7(self, db_session):
        email = SentEmail(mail_from="test@vb.at", to="a@b.at", subject="Test")
        db_session.add(email)
        db_session.flush()

        assert isinstance(email.id, uuid.UUID)
        assert email.id.version == 7


class TestClientUserAgentIdUuidDefault:
    """Guards ClientUserAgent's own UUID primary key: id is generated
    exclusively by the database's uuidv7() server_default, not by any
    Python-side default - same guard as every other migrated table's
    primary key."""

    def test_id_defaults_to_a_valid_uuid7(self, db_session):
        ua = ClientUserAgent(string="Phase A guard/1.0")
        db_session.add(ua)
        db_session.flush()

        assert isinstance(ua.id, uuid.UUID)
        assert ua.id.version == 7
