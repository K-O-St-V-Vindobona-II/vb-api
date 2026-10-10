"""Mails about changed records name the fields in German, not by column."""

from unittest.mock import patch

import pytest

from app.core.field_labels import field_label
from app.core.mailer import (
    send_entry_changed_email,
    send_member_change_request_resolved_email,
    send_member_change_request_submitted_email,
)
from app.schemas.standesdb import ContactSaveRequest, MemberSaveRequest

DIFF = {
    "rufnummer_mobil": {"old": "0664 1", "new": "0664 2"},
    "arbeitgeber": {"old": "A", "new": "B"},
}


class TestFieldLabels:
    def test_a_known_key_has_its_german_label(self):
        assert field_label("rufnummer_mobil") == "Rufnummer (mobil)"

    def test_an_unknown_key_is_shown_as_it_is(self):
        assert field_label("some_new_column") == "some_new_column"

    @pytest.mark.parametrize(
        "key",
        sorted(
            {
                key
                for schema in (MemberSaveRequest, ContactSaveRequest)
                for key in schema.model_fields
                if not key.endswith("_accuracy")
            }
        ),
    )
    def test_every_form_field_has_a_label(self, key: str):
        assert field_label(key) != key


class TestMailsUseLabels:
    def test_the_mail_about_a_changed_record(self):
        with patch("app.core.mailer._send_to_multiple") as send:
            send_entry_changed_email(
                ["admin@x.at"],
                "member",
                "Erika",
                DIFF,
                change_type="update",
                modifier_cn="Max",
            )

        html = send.call_args.args[2]
        text = send.call_args.args[3]
        assert "Rufnummer (mobil)" in html
        assert "rufnummer_mobil" not in html
        assert "Rufnummer (mobil):" in text
        assert "rufnummer_mobil" not in text

    def test_the_mail_about_a_submitted_request(self):
        with patch("app.core.mailer.send_to_recipients") as send:
            send_member_change_request_submitted_email(["admin@x.at"], "Max", DIFF)

        html = send.call_args.kwargs["html_content"]
        assert "Rufnummer (mobil)" in html
        assert "rufnummer_mobil" not in html

    def test_the_mail_about_a_resolved_request_for_both_sections(self):
        decisions = {"rufnummer_mobil": "approved", "arbeitgeber": "rejected"}
        with patch("app.core.mailer.send_to_recipients") as send:
            send_member_change_request_resolved_email("m@x.at", DIFF, decisions)

        html = send.call_args.kwargs["html_content"]
        assert "Rufnummer (mobil)" in html
        assert "Arbeitgeber" in html
        assert "rufnummer_mobil" not in html
