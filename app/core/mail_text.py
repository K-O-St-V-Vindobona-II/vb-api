"""Plain-text version of an HTML mail.

Every mail goes out as text and HTML alternatives: mail filters rate an
HTML-only message as suspicious, and text-only mail programs would show
nothing readable. Mails that have no hand-written text version get this
one, derived from their HTML.
"""

import re
from html.parser import HTMLParser

# Content of these elements never belongs in the text version.
_SKIPPED = frozenset({"head", "script", "style", "title"})
# Elements that start and end on a line of their own.
_BLOCKS = frozenset(
    {"div", "table", "tr", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}
)
# Elements separated from their surroundings by an empty line.
_PARAGRAPHS = frozenset({"p"})
# Elements without a closing tag: they must not count towards the ignored depth.
_VOID = frozenset({"br", "img", "hr", "meta", "link", "input"})
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        # Line breaks the text currently ends in; the start counts as a
        # paragraph break, so the text never begins with empty lines.
        self._trailing_breaks = 2
        self._ignored_depth = 0
        self._link: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._ignored_depth or tag in _SKIPPED or _is_hidden(attrs):
            if tag not in _VOID:
                self._ignored_depth += 1
            return
        self._open(tag, dict(attrs))

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_depth:
            self._ignored_depth -= 1
            return
        self._close(tag)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        # Line breaks in the HTML source are plain whitespace, as in a browser.
        text = _WHITESPACE.sub(" ", data)
        self._parts.append(text)
        if text.strip():
            self._trailing_breaks = 0
        if self._link is not None:
            self._link_text.append(text)

    def _break(self, count: int) -> None:
        """End the text in at least `count` line breaks, so adjacent block
        boundaries never stack up to extra empty lines."""
        self._parts.append("\n" * max(0, count - self._trailing_breaks))
        self._trailing_breaks = max(count, self._trailing_breaks)

    def _open(self, tag: str, attrs: dict[str, str | None]) -> None:
        if tag == "br":
            self._parts.append("\n")
            self._trailing_breaks += 1
        elif tag == "li":
            self._break(1)
            self._parts.append("- ")
        elif tag == "td":
            self._parts.append(" ")
        elif tag == "a":
            self._link = attrs.get("href")
            self._link_text = []
        else:
            self._block_boundary(tag)

    def _close(self, tag: str) -> None:
        if tag == "a":
            self._close_link()
            return
        self._block_boundary(tag)

    def _block_boundary(self, tag: str) -> None:
        """Start or end of a block: a paragraph stands between empty lines,
        any other block on a line of its own."""
        if tag in _PARAGRAPHS:
            self._break(2)
        elif tag in _BLOCKS:
            self._break(1)

    def _close_link(self) -> None:
        """A link keeps its address in the text, unless the visible text
        already is the address."""
        url, self._link = self._link, None
        if not url or url.startswith("mailto:"):
            return
        if " ".join(self._link_text).strip() == url:
            return
        self._parts.append(f" ({url})")
        self._trailing_breaks = 0

    def text(self) -> str:
        return "".join(self._parts)


def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    style = dict(attrs).get("style") or ""
    return bool(_HIDDEN_STYLE.search(style))


def _tidy(raw: str) -> str:
    """Single spaces within a line, no blank line runs, no outer blanks."""
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_to_text(html: str) -> str:
    """The readable text of an HTML mail: paragraphs, line breaks, list
    items and link addresses kept, markup, styles and hidden parts dropped."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    return _tidy(extractor.text())
