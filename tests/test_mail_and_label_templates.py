"""Reset link encoding, capped member-image report and label layout."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from weasyprint import HTML

from app.core.mailer import render_template, send_reset_email
from app.services.export_service import _jinja_env

MM_PER_INCH = 25.4
PX_PER_INCH = 96


def _collect(box, found: list) -> None:
    found.append(box)
    for child in getattr(box, "children", None) or []:
        _collect(child, found)


def _label_geometry_mm(entry: dict[str, str | int]) -> tuple[float, float, float]:
    """Return how far (bottom, right) the content of one label leaves its box,
    and the gap between the text and the right edge."""
    html = _jinja_env.get_template("labels.html").render(pages=[[entry]])
    document = HTML(string=html).render()
    boxes: list = []
    _collect(document.pages[0]._page_box, boxes)
    label = next(b for b in boxes if type(b).__name__ == "AbsolutePlaceholder")
    recipient, sender = label.children
    needed = recipient.margin_height() + sender.margin_height()
    texts: list = []
    _collect(label, texts)
    right_edge = label.content_box_x() + label.width
    text_right = max(
        t.position_x + t.width for t in texts if type(t).__name__ == "TextBox"
    )
    to_mm = MM_PER_INCH / PX_PER_INCH
    return (
        max(needed - label.height, 0) * to_mm,
        max(text_right - right_edge, 0) * to_mm,
        (right_edge - text_right) * to_mm,
    )


def _entry(name: str, address: str = "Musterstrasse 1", land: str = "Österreich"):
    return {
        "anrede": "Herr",
        "name": name,
        "address": address,
        "plz_ort": "1090 Wien",
        "land": land,
        "row": 0,
        "col": 0,
    }


class TestResetMail:
    @staticmethod
    def _sent(to_email: str, token: str):
        settings = SimpleNamespace(frontend_reset_url="https://intern.example.at/reset")
        with (
            patch("app.core.mailer.get_settings", return_value=settings),
            patch(
                "app.core.mailer._build_from_header",
                return_value=("a@b.at", "a@b.at"),
            ),
            patch("app.core.mailer._send_message") as send,
            patch("app.core.mailer._log_sent_email") as log,
        ):
            send_reset_email(to_email, token)
        return send.call_args.args[0], log.call_args.args[2]

    @staticmethod
    def _link_of(html: str) -> str:
        start = html.index('href="') + len('href="')
        return html[start : html.index('"', start)].replace("&amp;", "&")

    @pytest.mark.parametrize(
        "address", ["max@x.at", "max+verein@x.at", "a&b=c@x.at", "m#x@x.at"]
    )
    def test_the_address_and_token_survive_a_query_string_decoder(self, address):
        message, _logged = self._sent(address, "tok-en_1")
        html = message.get_payload()[1].get_payload(decode=True).decode()

        # parse_qs decodes "+" as a space, exactly like the router of the frontend.
        query = parse_qs(urlsplit(self._link_of(html)).query)

        assert query == {"token": ["tok-en_1"], "email": [address]}

    def test_the_plain_text_part_carries_the_same_link(self):
        message, _logged = self._sent("max+verein@x.at", "tok")
        text = message.get_payload()[0].get_payload(decode=True).decode()

        assert "email=max%2Bverein%40x.at" in text
        assert "token=tok" in text

    def test_the_footer_shows_the_current_year(self):
        with patch("app.core.mailer.local_today", return_value=date(2031, 5, 1)):
            message, _logged = self._sent("max@x.at", "tok")
        html = message.get_payload()[1].get_payload(decode=True).decode()

        assert "&copy; 2031 Vindobona" in html

    def test_the_stored_copy_keeps_the_placeholder_and_the_encoded_address(self):
        _message, logged = self._sent("max+verein@x.at", "secret-token")

        assert "token=[redacted]" in logged
        assert "secret-token" not in logged
        query = parse_qs(urlsplit(self._link_of(logged)).query)
        assert query["email"] == ["max+verein@x.at"]


class TestStandesdbHealthMail:
    def test_a_long_report_is_capped(self):
        keys = [f"images/{i}.jpg" for i in range(5000)]

        html = render_template(
            "standesdb_health_check.html", missing=keys, orphans=keys
        )

        assert html.count("<li>") == 200
        assert html.count("und 4900 weitere") == 2
        assert "5000 fehlende Datei(en)" in html
        assert len(html.encode()) < 20_000
        assert '<meta charset="utf-8">' in html

    def test_both_lists_are_capped_on_their_own(self):
        html = render_template(
            "standesdb_health_check.html",
            missing=[f"m{i}" for i in range(101)],
            orphans=["o"],
        )

        assert html.count("<li>") == 101
        assert html.count("und 1 weitere") == 1

    def test_a_list_of_exactly_the_limit_is_complete(self):
        html = render_template(
            "standesdb_health_check.html",
            missing=[f"m{i}" for i in range(100)],
            orphans=[],
        )

        assert html.count("<li>") == 100
        assert "weitere" not in html

    def test_exactly_the_limit_of_orphans_is_complete(self):
        html = render_template(
            "standesdb_health_check.html",
            missing=[],
            orphans=[f"o{i}" for i in range(100)],
        )

        assert html.count("<li>") == 100
        assert "weitere" not in html

    def test_a_short_report_is_complete(self):
        html = render_template(
            "standesdb_health_check.html", missing=["a.jpg", "b.jpg"], orphans=[]
        )

        assert html.count("<li>") == 2
        assert "weitere" not in html


class TestLabelLayout:
    @pytest.mark.parametrize(
        "entry",
        [
            _entry("Max Muster"),
            _entry("Dr. Maximilian Alexander Mustermann-Musterfrau MBA v/o Kater"),
            _entry(
                "Univ.-Prof. Dr. Maximilian Alexander Mustermann-Musterfrau MBA"
                " v/o Kater Zusatz",
                "c/o Musterfirma GmbH, Abteilung Recht und Steuern,"
                " Musterstrasse 123/4/5",
                "Vereinigte Staaten von Amerika",
            ),
            _entry("M" * 60),
        ],
        ids=["short", "long-name", "worst-case", "unbreakable-token"],
    )
    def test_nothing_leaves_the_label_box(self, entry):
        bottom, right, _gap = _label_geometry_mm(entry)

        assert bottom == 0
        assert right == 0

    def test_a_long_word_stops_before_the_edge_of_the_label(self):
        _bottom, _right, gap = _label_geometry_mm(_entry("M" * 60))

        assert gap >= 2.9

    def test_a_normal_label_keeps_the_normal_type(self):
        html = _jinja_env.get_template("labels.html").render(
            pages=[[_entry("Max Muster")]]
        )

        assert 'class="recipient"' in html
        assert "dense" not in html.split("</style>")[1]

    @pytest.mark.parametrize(
        "entry",
        [_entry("N" * 41), _entry("Max Muster", address="S" * 41)],
        ids=["long-name", "long-address"],
    )
    def test_a_long_label_uses_the_dense_type(self, entry):
        html = _jinja_env.get_template("labels.html").render(pages=[[entry]])

        assert 'class="recipient dense"' in html

    def test_the_limit_is_forty_characters(self):
        html = _jinja_env.get_template("labels.html").render(
            pages=[[_entry("N" * 40, address="S" * 40)]]
        )

        assert 'class="recipient"' in html
