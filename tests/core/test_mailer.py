"""Tests für Mailer-Hilfsfunktionen und Edge Cases."""

from datetime import date
from unittest.mock import patch

from app.core.mailer import (
    _format_diff_value,
    send_entry_changed_email,
    send_to_recipients,
)
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

    @patch("app.db.database.SessionLocal")
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
