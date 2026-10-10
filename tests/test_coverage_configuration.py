"""The coverage gate may only skip what it says it skips. An entry of
`exclude_lines` is a regular expression searched in every source line, and a
matching line that opens a block excludes the whole block: a bare "pass" would
also hide every function or branch whose opening line holds "password",
"passed" or "bypass"."""

import re
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
PATTERNS = tomllib.loads(PYPROJECT.read_text())["tool"]["coverage"]["report"][
    "exclude_lines"
]


def _excluded(line: str) -> bool:
    return any(re.search(pattern, line) for pattern in PATTERNS)


class TestExcludeLines:
    @pytest.mark.parametrize(
        "line",
        [
            "def verify_password(plain: str, hashed: str) -> bool:",
            "    if password_ok:",
            "    password_bytes = plain_password.encode()",
            "    if passed:",
            "    bypass = True",
            "    compass = 1",
            "    return bypass",
            "    x = compass",
            "        passthrough()",
            "    # pass the token on",
        ],
    )
    def test_a_line_that_only_contains_the_word_is_measured(self, line):
        assert not _excluded(line)

    @pytest.mark.parametrize("line", ["pass", "    pass", "        pass", "\tpass"])
    def test_a_line_that_is_nothing_but_pass_is_skipped(self, line):
        assert _excluded(line)

    @pytest.mark.parametrize(
        "line",
        [
            "if TYPE_CHECKING:",
            "    def __repr__(self) -> str:",
            "        raise NotImplementedError",
            "    x = 1  # pragma: no cover",
        ],
    )
    def test_the_other_exclusions_still_apply(self, line):
        assert _excluded(line)

    def test_every_pattern_is_a_valid_regular_expression(self):
        for pattern in PATTERNS:
            re.compile(pattern)
