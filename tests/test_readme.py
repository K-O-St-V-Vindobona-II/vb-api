"""Documentation guards: every README exists in English and German with the
same structure, and the scripts reference keeps up with the scripts that exist."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README_EN = ROOT / "README.md"
README_DE = ROOT / "README.de.md"
SCRIPTS_EN = ROOT / "scripts" / "README.md"
SCRIPTS_DE = ROOT / "scripts" / "README.de.md"
LANGUAGE_PAIRS = [(README_EN, README_DE), (SCRIPTS_EN, SCRIPTS_DE)]

# Developer tooling, not an operational script run during regular ops.
_DEVELOPER_HELPERS = {"check_router_soc.py", "check_coverage_gate.py", "run_pytest.sh"}
_OPERATIONAL_SCRIPTS = sorted(
    path.name
    for path in (ROOT / "scripts").glob("*.py")
    if path.name not in _DEVELOPER_HELPERS
)

_HEADING = re.compile(r"^(#{1,6})\s+(\d+(?:\.\d+)?)?")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _heading_structure(text: str) -> list[tuple[int, str]]:
    """Level and number of every heading outside code fences, in order."""
    structure: list[tuple[int, str]] = []
    in_code_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        match = None if in_code_fence else _HEADING.match(line)
        if match:
            structure.append((len(match.group(1)), match.group(2) or ""))
    return structure


@pytest.mark.parametrize("script", _OPERATIONAL_SCRIPTS)
def test_every_operational_script_has_a_section_in_both_languages(script: str):
    assert f"## `{script}`" in _read(SCRIPTS_EN)
    assert f"## `{script}`" in _read(SCRIPTS_DE)


@pytest.mark.parametrize("helper", sorted(_DEVELOPER_HELPERS))
def test_every_developer_helper_is_listed_in_both_languages(helper: str):
    assert f"`{helper}`" in _read(SCRIPTS_EN)
    assert f"`{helper}`" in _read(SCRIPTS_DE)


@pytest.mark.parametrize(("english", "german"), LANGUAGE_PAIRS)
def test_each_language_links_to_the_other(english: Path, german: Path):
    assert "[Deutsch](README.de.md)" in _read(english)
    assert "[English](README.md)" in _read(german)


@pytest.mark.parametrize(("english", "german"), LANGUAGE_PAIRS)
def test_both_languages_have_the_same_heading_structure(english: Path, german: Path):
    assert _heading_structure(_read(english)) == _heading_structure(_read(german))


@pytest.mark.parametrize("path", [README_EN, README_DE, SCRIPTS_EN, SCRIPTS_DE])
def test_the_readmes_name_no_schedule_variable_that_does_not_exist(path: Path):
    assert "BACKUP_INTERVAL_DAYS" not in _read(path)
