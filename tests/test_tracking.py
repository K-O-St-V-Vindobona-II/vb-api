"""Tests for the Tracking module (sent emails + activity log)."""

from datetime import UTC, date, datetime

import bcrypt

from app.models.client_user_agent import ClientUserAgent
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.request_log import RequestLog
from app.models.role import Role
from app.models.sent_email import SentEmail
from app.models.state import State
from app.services.auth_service import create_user_session


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


def _insert_request_log(
    db, member_id: int, method: str = "POST", path: str = "/api/test"
):
    now = datetime.now(UTC)
    log = RequestLog(
        client_ip="127.0.0.1",
        member_id=member_id,
        request_method=method,
        request_path=path,
        response_status=200,
        memory_usage=0,
        created_at=now,
        updated_at=now,
    )
    db.add(log)
    db.commit()
    return log


# --- Email Templates ---


class TestEmailTemplates:
    def test_returns_all_registry_entries_empty_db(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get("/api/tracking/sent-emails/templates", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 8
        keys = {t["template_key"] for t in data}
        assert keys == {
            "password-reset",
            "entry-changed",
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
        assert len(data) == 8
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
        import re
        from pathlib import Path

        from app.api.router_includes.tracking import EMAIL_TEMPLATE_REGISTRY

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
        from app.api.router_includes.tracking import EMAIL_TEMPLATE_REGISTRY

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
        resp = client.get("/api/tracking/sent-emails/99999", headers=headers)
        assert resp.status_code == 404


# --- Activity Log List ---


class TestActivityList:
    def test_pagination(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        for _ in range(30):
            _insert_request_log(db_session, admin.id)
        resp = client.get("/api/tracking/activity?page=1&page_size=10", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 30
        assert len(data["items"]) == 10

    def test_member_id_filter(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        _insert_request_log(db_session, admin.id)
        _insert_request_log(db_session, 99999)
        resp = client.get(
            f"/api/tracking/activity?member_id={admin.id}",
            headers=headers,
        )
        data = resp.json()
        for item in data["items"]:
            assert item["member_id"] == admin.id


# --- Activity Detail ---


class TestActivityDetail:
    def test_returns_detail(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        log = _insert_request_log(db_session, admin.id, "POST", "/api/auth/login")
        resp = client.get(f"/api/tracking/activity/{log.id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["request_method"] == "POST"
        assert data["request_path"] == "/api/auth/login"
        assert data["action_label"] == "Anmeldung"

    def test_404_for_missing(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get("/api/tracking/activity/99999", headers=headers)
        assert resp.status_code == 404


# --- Activity Sessions ---


class TestActivitySessions:
    def test_groups_by_member_and_gap(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        now = datetime.now(UTC)
        for i in range(3):
            log = RequestLog(
                client_ip="127.0.0.1",
                member_id=admin.id,
                request_method="POST",
                request_path=f"/api/test/{i}",
                response_status=200,
                memory_usage=0,
                created_at=now,
                updated_at=now,
            )
            db_session.add(log)
        db_session.commit()
        date_str = now.strftime("%Y-%m-%d")
        resp = client.get(
            f"/api/tracking/activity/sessions?date_str={date_str}",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        session = data[0]
        assert session["member_name"]
        assert session["action_count"] >= 3


# --- Activity Stats ---


class TestActivityStats:
    def test_returns_stats(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        _insert_request_log(db_session, admin.id, "POST", "/api/auth/login")
        resp = client.get("/api/tracking/activity/stats", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_users_today"] >= 1
        assert data["total_actions_today"] >= 1
        assert isinstance(data["actions_by_type"], dict)


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
        assert created_at.endswith("+00:00") or created_at.endswith("Z")

    def test_templates_last_sent_has_utc(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        _insert_sent_email(db_session, "password-reset")
        resp = client.get("/api/tracking/sent-emails/templates", headers=headers)
        data = resp.json()
        for t in data:
            if t["last_sent"]:
                assert t["last_sent"].endswith("+00:00") or t["last_sent"].endswith("Z")


# --- Action Label Resolution ---


class TestResolveActionLabel:
    def test_exact_match(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert _resolve_action_label("POST", "/api/auth/login") == "Anmeldung"
        assert _resolve_action_label("POST", "/api/auth/logout") == "Abmeldung"
        assert (
            _resolve_action_label("POST", "/api/p4x/admin/fee-config")
            == "Beitragskonfiguration angelegt"
        )
        assert (
            _resolve_action_label("POST", "/api/p4x/admin/summary")
            == "Abrechnung erstellt"
        )

    def test_prefix_match(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("PUT", "/api/standesdb/members/42")
            == "Mitglied bearbeitet"
        )
        assert (
            _resolve_action_label("DELETE", "/api/archive/files/7") == "Datei gelöscht"
        )
        assert (
            _resolve_action_label("PUT", "/api/archive/files/7") == "Datei bearbeitet"
        )
        assert (
            _resolve_action_label("PUT", "/api/archive/dirs/5") == "Ordner bearbeitet"
        )
        assert (
            _resolve_action_label("DELETE", "/api/archive/dirs/5") == "Ordner gelöscht"
        )
        assert (
            _resolve_action_label("DELETE", "/api/p4x/admin/fee-config/2024-01")
            == "Beitragskonfiguration gelöscht"
        )
        assert (
            _resolve_action_label("POST", "/api/p4x/admin/fee-members/42")
            == "Beitragsdaten bearbeitet"
        )

    def test_exact_match_archive_dir_create(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert _resolve_action_label("POST", "/api/archive/dirs") == "Ordner erstellt"

    def test_subresource_image_upload(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("POST", "/api/standesdb/members/42/images")
            == "Profilbild hochgeladen"
        )
        assert (
            _resolve_action_label("POST", "/api/standesdb/contacts/42/images")
            == "Profilbild hochgeladen"
        )

    def test_subresource_image_edit_delete(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("PUT", "/api/standesdb/members/42/images/5")
            == "Profilbild bearbeitet"
        )
        assert (
            _resolve_action_label("DELETE", "/api/standesdb/members/42/images/5")
            == "Profilbild gelöscht"
        )
        assert (
            _resolve_action_label("DELETE", "/api/standesdb/contacts/42/images/5")
            == "Profilbild gelöscht"
        )

    def test_subresource_archive_restore(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("PATCH", "/api/archive/dirs/3/restore")
            == "Wiederhergestellt"
        )
        assert (
            _resolve_action_label("PATCH", "/api/archive/files/7/restore")
            == "Wiederhergestellt"
        )

    def test_subresource_archive_receive(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("POST", "/api/archive/dirs/5/receive")
            == "Dateien verschoben"
        )

    def test_subresource_archive_comments(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("POST", "/api/archive/files/7/comments")
            == "Kommentar erstellt"
        )
        assert (
            _resolve_action_label("DELETE", "/api/archive/files/7/comments/3")
            == "Kommentar gelöscht"
        )

    def test_subresource_p4x_import(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("POST", "/api/p4x/admin/accounts/1/import")
            == "Transaktionen importiert"
        )

    def test_subresource_p4x_transaction_ops(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("POST", "/api/p4x/admin/transactions/42/set-partner")
            == "Partner zugeordnet"
        )
        assert (
            _resolve_action_label(
                "POST", "/api/p4x/admin/transactions/42/set-category-direct"
            )
            == "Kategorie zugeordnet"
        )
        assert (
            _resolve_action_label(
                "DELETE", "/api/p4x/admin/transactions/42/unset-category-direct"
            )
            == "Kategoriezuordnung entfernt"
        )

    def test_subresource_p4x_filter2direct(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label(
                "POST", "/api/p4x/admin/category-filters/5/filter2direct"
            )
            == "Filter → Direkt konvertiert"
        )

    def test_subresource_download(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("GET", "/api/archive/files/7/download")
            == "Datei heruntergeladen"
        )
        assert (
            _resolve_action_label("GET", "/api/archive/files/7/download/sm")
            == "Datei heruntergeladen (Thumbnail)"
        )

    def test_get_view_member(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("GET", "/api/standesdb/members/42")
            == "Mitglied angezeigt"
        )

    def test_get_view_contact(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("GET", "/api/standesdb/contacts/42")
            == "Kontakt angezeigt"
        )

    def test_get_view_dir(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("GET", "/api/archive/dirs/5")
            == "Verzeichnis angezeigt"
        )

    def test_get_view_file(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert _resolve_action_label("GET", "/api/archive/files/7") == "Datei angezeigt"

    def test_download_takes_priority_over_file_view(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("GET", "/api/archive/files/7/download")
            == "Datei heruntergeladen"
        )
        assert _resolve_action_label("GET", "/api/archive/files/7") == "Datei angezeigt"

    def test_subresource_takes_priority_over_prefix(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("DELETE", "/api/standesdb/members/42/images/5")
            == "Profilbild gelöscht"
        )
        assert (
            _resolve_action_label("DELETE", "/api/archive/files/7/comments/3")
            == "Kommentar gelöscht"
        )

    def test_contact_deleted(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("DELETE", "/api/standesdb/contacts/42")
            == "Kontakt gelöscht"
        )

    def test_no_phantom_matches(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("DELETE", "/api/standesdb/members/42")
            == "DELETE /api/standesdb/members/42"
        )
        assert (
            _resolve_action_label("PUT", "/api/p4x/admin/fee-config")
            == "PUT /api/p4x/admin/fee-config"
        )

    def test_fallback(self):
        from app.api.router_includes.tracking import _resolve_action_label

        result = _resolve_action_label("GET", "/api/unknown/path")
        assert result == "GET /api/unknown/path"

    def test_failed_login(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert (
            _resolve_action_label("POST", "/api/auth/login", 401)
            == "Anmeldung fehlgeschlagen"
        )
        assert (
            _resolve_action_label("POST", "/api/auth/google", 401)
            == "Anmeldung fehlgeschlagen"
        )
        assert _resolve_action_label("POST", "/api/auth/login", 200) == "Anmeldung"

    def test_case_insensitive_method(self):
        from app.api.router_includes.tracking import _resolve_action_label

        assert _resolve_action_label("post", "/api/auth/login") == "Anmeldung"


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


# --- Coverage: Activity Sessions edge cases ---


class TestActivitySessionsCoverage:
    def test_no_date_defaults_to_today(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        now = datetime.now(UTC)
        db_session.add(
            RequestLog(
                client_ip="10.0.0.1",
                member_id=admin.id,
                request_method="GET",
                request_path="/api/test",
                response_status=200,
                memory_usage=0,
                created_at=now,
                updated_at=now,
            )
        )
        db_session.commit()
        resp = client.get("/api/tracking/activity/sessions", headers=headers)
        assert resp.status_code == 200

    def test_invalid_date_returns_400(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get(
            "/api/tracking/activity/sessions?date_str=not-a-date",
            headers=headers,
        )
        assert resp.status_code == 400

    def test_member_filter(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        now = datetime.now(UTC)
        db_session.add(
            RequestLog(
                client_ip="10.0.0.1",
                member_id=admin.id,
                request_method="GET",
                request_path="/api/test",
                response_status=200,
                memory_usage=0,
                created_at=now,
                updated_at=now,
            )
        )
        db_session.commit()
        date_str = now.strftime("%Y-%m-%d")
        resp = client.get(
            f"/api/tracking/activity/sessions?date_str={date_str}&member_id={admin.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        for session in data:
            assert session["member_id"] == admin.id

    def test_empty_result(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get(
            "/api/tracking/activity/sessions?date_str=2000-01-01",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []


# --- Coverage: Activity Detail with user agent ---


class TestActivityDetailUserAgent:
    def test_log_with_user_agent(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        ua = ClientUserAgent(string="Mozilla/5.0 TestAgent")
        db_session.add(ua)
        db_session.flush()
        now = datetime.now(UTC)
        log = RequestLog(
            client_ip="127.0.0.1",
            member_id=admin.id,
            request_method="GET",
            request_path="/api/test",
            response_status=200,
            memory_usage=0,
            client_user_agent_id=ua.id,
            created_at=now,
            updated_at=now,
        )
        db_session.add(log)
        db_session.commit()
        resp = client.get(f"/api/tracking/activity/{log.id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["client_user_agent"] == "Mozilla/5.0 TestAgent"


# --- Coverage: Activity List date filter error handling ---


class TestActivityListDateFilters:
    def test_invalid_date_from_ignored(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get(
            "/api/tracking/activity?date_from=invalid",
            headers=headers,
        )
        assert resp.status_code == 200

    def test_invalid_date_to_ignored(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        resp = client.get(
            "/api/tracking/activity?date_to=invalid",
            headers=headers,
        )
        assert resp.status_code == 200

    def test_valid_date_range(self, client, db_session):
        _seed(db_session)
        headers, admin = _login_admin(db_session)
        _insert_request_log(db_session, admin.id)
        resp = client.get(
            "/api/tracking/activity?date_from=2020-01-01&date_to=2030-12-31",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1


# --- Coverage: Template preview without preview data ---


class TestTemplatePreviewNoData:
    def test_template_without_preview_data_returns_404(self, client, db_session):
        _seed(db_session)
        headers, _ = _login_admin(db_session)
        from unittest.mock import patch

        from app.api.router_includes.tracking import EMAIL_TEMPLATE_REGISTRY

        fake_entry = {
            "key": "test-no-preview",
            "name": "No Preview",
            "source": "test",
            "file": "birthday.html",
        }
        patched_registry = [*EMAIL_TEMPLATE_REGISTRY, fake_entry]
        with patch(
            "app.api.router_includes.tracking.EMAIL_TEMPLATE_REGISTRY",
            patched_registry,
        ):
            resp = client.get(
                "/api/tracking/sent-emails/templates/test-no-preview/preview",
                headers=headers,
            )
        assert resp.status_code == 404
        assert "Vorschaudaten" in resp.json()["detail"]


# --- Coverage: _member_name_map with empty set ---


class TestMemberNameMap:
    def test_empty_set_returns_empty_dict(self):
        from app.api.router_includes.tracking import _member_name_map

        result = _member_name_map(None, set())
        assert result == {}
