"""The contact form mail: fixed recipients, the visitor as reply-to."""

from unittest.mock import patch

# Captured at import time, before the autouse _block_all_emails fixture (see
# conftest.py) replaces the name on the app.core.mailer module for every test.
from app.core.mailer import send_contact_form_email as _real_send_contact_form_email

CONTACT_RECIPIENTS = ["philchc@vindobona2.at", "vindoboneninfo@gmail.com"]


class TestSendContactFormEmail:
    @patch("app.core.mailer.send_to_recipients")
    def test_recipients_are_fixed(self, mock_send):
        _real_send_contact_form_email("Max", "max@example.com", "Hallo!")

        assert mock_send.call_args.args[0] == CONTACT_RECIPIENTS

    @patch("app.core.mailer.send_to_recipients")
    def test_visitor_is_the_reply_to_address(self, mock_send):
        _real_send_contact_form_email("Max", "max@example.com", "Hallo!")

        assert mock_send.call_args.kwargs["reply_to"] == "max@example.com"

    @patch("app.core.mailer.send_to_recipients")
    def test_subject_template_key_and_content(self, mock_send):
        _real_send_contact_form_email(
            "Max Mustermann", "max@example.com", "Hallo, bitte um Rückruf!"
        )

        kwargs = mock_send.call_args.kwargs
        assert kwargs["subject"] == "Neue Kontaktaufnahme von Max Mustermann"
        assert kwargs["template_key"] == "public-contact-form"
        assert "Max Mustermann" in kwargs["html_content"]
        assert "max@example.com" in kwargs["html_content"]
        assert "Hallo, bitte um Rückruf!" in kwargs["html_content"]

    @patch("app.core.mailer.send_to_recipients")
    def test_markup_in_the_message_is_escaped(self, mock_send):
        _real_send_contact_form_email("Max", "max@example.com", "<script>x</script>")

        html = mock_send.call_args.kwargs["html_content"]
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;" in html
