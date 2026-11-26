"""The plain-text alternative derived from an HTML mail."""

import pytest

from app.core.mail_text import html_to_text


class TestHtmlToText:
    def test_paragraphs_become_blank_line_separated_blocks(self):
        html = "<p>Erster   Absatz</p><p>Zweiter\nAbsatz</p>"

        assert html_to_text(html) == "Erster Absatz\n\nZweiter Absatz"

    def test_indentation_between_tags_adds_no_empty_lines(self):
        html = "<p>Eins</p>\n    <p>Zwei</p>\n  <ul>\n    <li>Drei</li>\n  </ul>"

        assert html_to_text(html) == "Eins\n\nZwei\n\n- Drei"

    def test_line_breaks_and_list_items(self):
        html = "Zeile eins<br>Zeile zwei<ul><li>Miete</li><li>Budenbetrieb</li></ul>"

        assert html_to_text(html) == "Zeile eins\nZeile zwei\n- Miete\n- Budenbetrieb"

    def test_table_rows_are_lines_and_cells_are_separated(self):
        html = "<table><tr><td>Mo</td><td>22.2.</td></tr><tr><td>Di</td></tr></table>"

        assert html_to_text(html) == "Mo 22.2.\nDi"

    def test_head_script_style_and_hidden_parts_are_dropped(self):
        html = (
            "<html><head><title>T</title><style>p{}</style></head><body>"
            '<div style="display: none; max-height:0">Vorschauzeile</div>'
            "<script>alert(1)</script><p>Inhalt<br></p></body></html>"
        )

        assert html_to_text(html) == "Inhalt"

    def test_nested_markup_inside_a_hidden_part_stays_hidden(self):
        html = '<div style="DISPLAY:NONE"><span>versteckt</span><br></div><div>da</div>'

        assert html_to_text(html) == "da"

    def test_entities_are_decoded(self):
        assert html_to_text("<p>&bdquo;Gr&uuml;&szlig;e&ldquo; &amp; mehr</p>") == (
            "„Grüße“ & mehr"
        )

    @pytest.mark.parametrize(
        ("html", "expected"),
        [
            ('<a href="https://x.at/a">Link</a>', "Link (https://x.at/a)"),
            ('<a href="https://x.at/a">https://x.at/a</a>', "https://x.at/a"),
            ('<a href="mailto:a@b.at">a@b.at</a>', "a@b.at"),
            ("<a>ohne Ziel</a>", "ohne Ziel"),
        ],
    )
    def test_links_keep_their_address_once(self, html, expected):
        assert html_to_text(html) == expected

    def test_headings_and_divs_stand_on_their_own_line(self):
        assert html_to_text("<h1>Titel</h1>Text<div>Block</div>Ende") == (
            "Titel\nText\nBlock\nEnde"
        )
