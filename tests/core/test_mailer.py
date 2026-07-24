"""Tests for mailer helper functions and edge cases."""

from datetime import date
from email.mime.multipart import MIMEMultipart
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError

from app.core.mailer import (
    _build_from_header,
    _format_diff_value,
    _log_sent_email,
    _resolve_date_accuracy,
    _send_message,
    render_template,
    send_entry_changed_email,
    send_to_recipients,
)
from app.core.mailer import _send_to_multiple as _real_send_to_multiple

# Captured at import time, before the autouse _block_all_emails fixture
# (see conftest.py) replaces these two names on the app.core.mailer module
# for the duration of every test.
from app.core.mailer import send_reset_email as _real_send_reset_email
from app.models.sent_email import SentEmail


class TestFormatDiffValue:
    def test_none_returns_dash(self):
        assert _format_diff_value("vorname", None, {}) == "-"

    def test_string_value(self):
        assert _format_diff_value("vorname", "Max", {}) == "Max"

    def test_list_value(self):
        result = _format_diff_value("badges", ["Band1", "Band2"], {})
        assert "Band1" in result
        assert "Band2" in result

    def test_empty_list_returns_dash(self):
        assert _format_diff_value("badges", [], {}) == "-"

    def test_date_with_accuracy_3(self):
        diff = {"geburtsdatum_accuracy": {"new": 3}}
        result = _format_diff_value("geburtsdatum", date(1978, 7, 5), diff)
        assert "5" in result
        assert "Juli" in result
        assert "1978" in result

    def test_date_with_accuracy_2(self):
        diff = {"aufnahmedatum_accuracy": {"new": 2}}
        result = _format_diff_value("aufnahmedatum", date(1995, 12, 16), diff)
        assert "Dezember" in result
        assert "1995" in result
        assert "16" not in result

    def test_date_with_accuracy_1(self):
        diff = {"branderdatum_accuracy": {"new": 1}}
        result = _format_diff_value("branderdatum", date(1996, 6, 1), diff)
        assert result == "1996"

    def test_date_with_accuracy_0(self):
        diff = {"sterbedatum_accuracy": {"new": 0}}
        result = _format_diff_value("sterbedatum", date(2020, 1, 1), diff)
        assert result == "-"

    def test_non_date_field_with_date_value(self):
        result = _format_diff_value("vorname", date(2020, 1, 1), {})
        assert result == "2020-01-01"


class TestSendEntryChangedEmail:
    @patch("app.core.mailer._send_to_multiple")
    def test_empty_diff_no_send(self, mock_send):
        send_entry_changed_email(["a@b.at"], "member", "Test", {}, "update", "Admin")
        mock_send.assert_not_called()

    @patch("app.core.mailer._send_to_multiple")
    def test_empty_recipients_no_send(self, mock_send):
        send_entry_changed_email(
            [],
            "member",
            "Test",
            {"vorname": {"old": "A", "new": "B"}},
            "update",
            "Admin",
        )
        mock_send.assert_not_called()

    @patch("app.core.mailer._send_to_multiple")
    def test_valid_diff_sends(self, mock_send):
        send_entry_changed_email(
            ["admin@test.at"],
            "member",
            "Max Muster",
            {"vorname": {"old": "Alt", "new": "Neu"}},
            "update",
            "Admin User",
        )
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        assert args[0] == ["admin@test.at"]
        assert "Verbindungsdatenbank" in args[1]
        assert "Max Muster" in args[2]

    @patch("app.core.mailer._send_to_multiple")
    def test_store_type_in_subject(self, mock_send):
        send_entry_changed_email(
            ["a@b.at"],
            "contact",
            "Kontakt X",
            {"name": {"old": None, "new": "Kontakt X"}},
            "store",
            "Admin",
        )
        args = mock_send.call_args[0]
        html = args[2]
        assert "Neuanlage" in html

    @patch("app.core.mailer._send_to_multiple")
    def test_accuracy_fields_skipped_in_text(self, mock_send):
        send_entry_changed_email(
            ["a@b.at"],
            "member",
            "Test",
            {
                "geburtsdatum": {"old": None, "new": "1978-07-05"},
                "geburtsdatum_accuracy": {"old": 0, "new": 3},
            },
            "update",
            "Admin",
        )
        text = mock_send.call_args[0][3]
        assert "geburtsdatum_accuracy" not in text


class TestSendToRecipients:
    @patch("app.core.mailer._log_sent_email")
    @patch("app.core.mailer._send_message")
    def test_bcc_header_never_set_on_message(self, mock_send_message, mock_log):
        send_to_recipients(
            to_emails=["a@b.at"],
            subject="S",
            html_content="<p>hi</p>",
            bcc_emails=["c@d.at"],
            from_addr="noreply@test.at",
        )
        msg = mock_send_message.call_args[0][0]
        assert "Bcc" not in msg

    @patch("app.core.mailer._log_sent_email")
    @patch("app.core.mailer._send_message")
    def test_bcc_only_send_uses_placeholder_to_header(
        self, mock_send_message, mock_log
    ):
        send_to_recipients(
            to_emails=[],
            subject="S",
            html_content="<p>hi</p>",
            bcc_emails=["x@y.at"],
            from_addr="noreply@test.at",
        )
        msg = mock_send_message.call_args[0][0]
        assert msg["To"] == "Undisclosed-Recipients:;"

    @patch("app.core.mailer._log_sent_email")
    @patch("app.core.mailer._send_message")
    def test_bcc_only_send_reaches_smtp_recipients(self, mock_send_message, mock_log):
        send_to_recipients(
            to_emails=[],
            subject="S",
            html_content="<p>hi</p>",
            bcc_emails=["x@y.at"],
            from_addr="noreply@test.at",
        )
        recipients = mock_send_message.call_args[0][1]
        assert recipients == ["x@y.at"]

    @patch("app.core.mailer._log_sent_email")
    @patch("app.core.mailer._send_message")
    def test_empty_to_and_empty_bcc_no_send(self, mock_send_message, mock_log):
        send_to_recipients(to_emails=[], subject="S", html_content="<p>hi</p>")
        mock_send_message.assert_not_called()
        mock_log.assert_not_called()

    @patch("app.core.mailer._log_sent_email")
    @patch("app.core.mailer._send_message")
    def test_to_only_still_works(self, mock_send_message, mock_log):
        send_to_recipients(
            to_emails=["a@b.at", "c@d.at"],
            subject="S",
            html_content="<p>hi</p>",
            from_addr="noreply@test.at",
        )
        msg = mock_send_message.call_args[0][0]
        assert msg["To"] == "a@b.at, c@d.at"
        assert "Bcc" not in msg
        recipients = mock_send_message.call_args[0][1]
        assert recipients == ["a@b.at", "c@d.at"]

    @patch("app.core.mailer._log_sent_email")
    @patch("app.core.mailer._send_message")
    def test_from_name_overrides_smtp_from_name_env(self, mock_send_message, mock_log):
        send_to_recipients(
            to_emails=["a@b.at"],
            subject="S",
            html_content="<p>hi</p>",
            from_name="Philister-ChC Vindobona II",
            from_addr="noreply@test.at",
        )
        msg = mock_send_message.call_args[0][0]
        assert msg["From"].startswith('"Philister-ChC Vindobona II"')

    @patch("app.core.mailer.SessionLocal")
    @patch("app.core.mailer._send_message")
    def test_bcc_recipients_logged_to_sent_email(
        self, mock_send_message, mock_session_local, db_session
    ):
        mock_session_local.return_value = db_session
        send_to_recipients(
            to_emails=[],
            subject="S",
            html_content="<p>hi</p>",
            bcc_emails=["x@y.at", "z@y.at"],
            from_addr="noreply@test.at",
        )
        entry = db_session.query(SentEmail).one()
        assert entry.bcc == "x@y.at, z@y.at"

    @patch("app.core.mailer._log_sent_email")
    @patch("app.core.mailer._send_message")
    def test_reply_to_sets_header(self, mock_send_message, mock_log):
        send_to_recipients(
            to_emails=["a@b.at"],
            subject="S",
            html_content="<p>hi</p>",
            reply_to="reply@test.at",
            from_addr="noreply@test.at",
        )
        msg = mock_send_message.call_args[0][0]
        assert msg["Reply-To"] == "reply@test.at"


class TestBuildFromHeader:
    def test_default_from_name(self, monkeypatch):
        monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@test.at")
        monkeypatch.delenv("SMTP_FROM_NAME", raising=False)

        from_email, from_header = _build_from_header()

        assert from_email == "noreply@test.at"
        assert from_header == '"Vindobona" <noreply@test.at>'

    def test_custom_from_name(self, monkeypatch):
        monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@test.at")
        monkeypatch.setenv("SMTP_FROM_NAME", "Philister-ChC Vindobona II")

        from_email, from_header = _build_from_header()

        assert from_email == "noreply@test.at"
        assert from_header == '"Philister-ChC Vindobona II" <noreply@test.at>'


class TestSendMessage:
    def test_ssl_port_logs_in_and_sends(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.test.at")
        monkeypatch.setenv("SMTP_PORT", "465")
        monkeypatch.setenv("SMTP_USER", "user@test.at")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@test.at")
        msg = MIMEMultipart()
        msg["Subject"] = "S"

        with patch("app.core.mailer.smtplib.SMTP_SSL") as mock_ssl:
            mock_server = mock_ssl.return_value.__enter__.return_value
            _send_message(msg, ["a@b.at"])

        mock_ssl.assert_called_once_with("smtp.test.at", 465)
        mock_server.login.assert_called_once_with("user@test.at", "secret")
        mock_server.sendmail.assert_called_once_with(
            "noreply@test.at", ["a@b.at"], msg.as_string()
        )

    def test_ssl_port_skips_login_when_user_is_null(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.test.at")
        monkeypatch.setenv("SMTP_PORT", "465")
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@test.at")
        msg = MIMEMultipart()

        with patch("app.core.mailer.smtplib.SMTP_SSL") as mock_ssl:
            mock_server = mock_ssl.return_value.__enter__.return_value
            _send_message(msg, ["a@b.at"])

        mock_server.login.assert_not_called()
        mock_server.sendmail.assert_called_once()

    def test_starttls_used_when_available(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.test.at")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("SMTP_USER", "user@test.at")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@test.at")
        msg = MIMEMultipart()

        with patch("app.core.mailer.smtplib.SMTP") as mock_smtp:
            mock_server = mock_smtp.return_value.__enter__.return_value
            mock_server.has_extn.return_value = True
            _send_message(msg, ["a@b.at"])

        mock_smtp.assert_called_once_with("smtp.test.at", 587)
        mock_server.starttls.assert_called_once()
        assert mock_server.ehlo.call_count == 2
        mock_server.login.assert_called_once_with("user@test.at", "secret")

    def test_starttls_skipped_when_unavailable(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.test.at")
        monkeypatch.setenv("SMTP_PORT", "25")
        monkeypatch.delenv("SMTP_USER", raising=False)
        monkeypatch.delenv("SMTP_PASSWORD", raising=False)
        monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@test.at")
        msg = MIMEMultipart()

        with patch("app.core.mailer.smtplib.SMTP") as mock_smtp:
            mock_server = mock_smtp.return_value.__enter__.return_value
            mock_server.has_extn.return_value = False
            _send_message(msg, ["a@b.at"])

        mock_server.starttls.assert_not_called()
        assert mock_server.ehlo.call_count == 1
        mock_server.login.assert_not_called()


class TestLogSentEmailDbError:
    @patch("app.core.mailer.SessionLocal")
    def test_db_error_is_swallowed(self, mock_session_local, db_session):
        mock_session_local.return_value = db_session

        with patch.object(db_session, "commit", side_effect=SQLAlchemyError("boom")):
            _log_sent_email("a@b.at", "Subject", "<p>hi</p>", "generic")


class TestSendResetEmailReal:
    @patch("app.core.mailer._log_sent_email")
    @patch("app.core.mailer._send_message")
    def test_builds_reset_link_and_sends(
        self, mock_send_message, mock_log, monkeypatch
    ):
        monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@test.at")
        monkeypatch.setenv("FRONTEND_RESET_URL", "https://intern.vindobona2.at/reset")

        _real_send_reset_email("member@test.at", "tok123")

        msg = mock_send_message.call_args[0][0]
        assert msg["Subject"] == "Passwort zurücksetzen - Vindobona"
        assert msg["To"] == "member@test.at"
        recipients = mock_send_message.call_args[0][1]
        assert recipients == "member@test.at"

        log_args = mock_log.call_args[0]
        assert log_args[0] == "member@test.at"
        assert log_args[3] == "password-reset"
        assert "tok123" in log_args[2]


class TestSendToMultipleReal:
    def test_empty_to_emails_no_send(self):
        with patch("app.core.mailer._send_message") as mock_send_message:
            _real_send_to_multiple([], "S", "<p>hi</p>", "text")

        mock_send_message.assert_not_called()

    @patch("app.core.mailer._log_sent_email")
    @patch("app.core.mailer._send_message")
    def test_sends_to_all_recipients(self, mock_send_message, mock_log, monkeypatch):
        monkeypatch.setenv("SMTP_FROM_EMAIL", "noreply@test.at")

        _real_send_to_multiple(
            ["a@b.at", "c@d.at"], "Subject", "<p>hi</p>", "plain text"
        )

        msg = mock_send_message.call_args[0][0]
        assert msg["To"] == "a@b.at, c@d.at"
        recipients = mock_send_message.call_args[0][1]
        assert recipients == ["a@b.at", "c@d.at"]
        mock_log.assert_called_once()


class TestResolveDateAccuracy:
    def test_missing_accuracy_key_returns_zero(self):
        assert _resolve_date_accuracy("geburtsdatum", {}) == 0


class TestRenderTemplate:
    def test_renders_password_reset_template(self):
        html = render_template(
            "password_reset.html", reset_link="https://example.at/reset"
        )
        assert "https://example.at/reset" in html
