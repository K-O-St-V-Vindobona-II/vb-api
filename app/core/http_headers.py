"""Helpers for HTTP header values that carry user-controlled text."""

import re
import unicodedata
from urllib.parse import quote

# Control characters (line breaks would split the header) and the characters
# that no file system accepts in a name.
_UNSAFE_FILENAME = re.compile(r'[\x00-\x1f\x7f\\/:*?"<>|]')


def attachment_disposition(filename: str) -> str:
    """Content-Disposition value for a download named `filename`.

    Header values must be Latin-1, and a member's name can hold any character,
    so the name goes out twice: an ASCII fallback (accents removed) for old
    clients and the exact name in the RFC 5987 `filename*` form.
    """
    cleaned = _UNSAFE_FILENAME.sub("_", filename).strip() or "download"
    ascii_name = (
        unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode()
        or "download"
    )
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(cleaned)}"
