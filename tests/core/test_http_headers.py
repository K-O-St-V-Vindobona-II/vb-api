"""Download names come from data (a member's name can hold any character) but
header values are Latin-1 and a line break would split the header."""

from email.message import Message
from urllib.parse import unquote

import pytest

from app.core.http_headers import attachment_disposition


def _parsed(value: str) -> Message:
    message = Message()
    message["Content-Disposition"] = value
    return message


def _plain_name(value: str) -> str:
    """The value of `filename=` as a client reads it."""
    return str(_parsed(value).get_param("filename", header="Content-Disposition"))


def _exact_name(value: str) -> str:
    """The value of `filename*=UTF-8''...` percent-decoded."""
    return unquote(value.split("filename*=UTF-8''", 1)[1])


class TestAttachmentDisposition:
    def test_a_plain_name_is_kept_in_both_forms(self):
        value = attachment_disposition("Abrechnung_2026-01-01.xlsx")

        assert value.startswith("attachment;")
        assert _plain_name(value) == "Abrechnung_2026-01-01.xlsx"
        assert _exact_name(value) == "Abrechnung_2026-01-01.xlsx"

    def test_a_name_outside_latin_1_still_fits_into_a_header(self):
        value = attachment_disposition("Beitragskonto_Řehoř Žák_2026.xlsx")

        value.encode("latin-1")
        assert _plain_name(value) == "Beitragskonto_Rehor Zak_2026.xlsx"
        assert _exact_name(value) == "Beitragskonto_Řehoř Žák_2026.xlsx"

    def test_umlauts_survive_in_the_exact_name_and_lose_their_marks_in_the_fallback(
        self,
    ):
        value = attachment_disposition("Müller_Ärger.xlsx")

        assert _plain_name(value) == "Muller_Arger.xlsx"
        assert _exact_name(value) == "Müller_Ärger.xlsx"

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("a/b\\c.xlsx", "a_b_c.xlsx"),
            ('quote"inside.xlsx', "quote_inside.xlsx"),
            ("a:b*c?d<e>f|g.xlsx", "a_b_c_d_e_f_g.xlsx"),
            ("../../etc/passwd", ".._.._etc_passwd"),
        ],
    )
    def test_separators_and_characters_no_file_system_accepts_become_underscores(
        self, name, expected
    ):
        value = attachment_disposition(name)

        assert _plain_name(value) == expected
        assert _exact_name(value) == expected

    @pytest.mark.parametrize(
        "breaker", ["\r\n", "\n", "\r", "\x00", "\x1f", "\x7f", "\t"]
    )
    def test_control_characters_cannot_break_out_of_the_header(self, breaker):
        value = attachment_disposition(f"a{breaker}Set-Cookie: x=y")

        assert not any(ord(c) < 32 or ord(c) == 127 for c in value)
        assert "\n" not in value
        assert "\r" not in value

    @pytest.mark.parametrize(
        ("name", "fallback"),
        [("", "download"), ("   ", "download"), ("\r\n", "__"), ("///", "___")],
    )
    def test_a_name_without_usable_characters_never_leaves_an_empty_fallback(
        self, name, fallback
    ):
        value = attachment_disposition(name)

        assert _plain_name(value) == fallback
        assert 'filename=""' not in value

    def test_a_name_that_is_all_non_ascii_never_leaves_an_empty_fallback(self):
        value = attachment_disposition("日本語")

        assert _plain_name(value) == "download"
        assert _exact_name(value) == "日本語"

    def test_the_quote_that_ends_the_filename_cannot_be_forged(self):
        value = attachment_disposition('x"; filename="evil.exe')

        assert value.count('"') == 2
