"""Documentation guards: the README is bilingual by decision and its script
overview must keep up with the scripts that exist."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
SCRIPTS_README = (ROOT / "scripts/README.md").read_text(encoding="utf-8")
GERMAN_MARKER = "\n# Deutsch\n"
SCRIPTS_GERMAN_MARKER = "\n# Scripts (Deutsch)\n"
# Developer tooling, not an operational script run during regular ops.
_NOT_OPERATIONAL = {"check_router_soc.py", "check_coverage_gate.py"}
_OPERATIONAL_SCRIPTS = sorted(
    path.name
    for path in (ROOT / "scripts").glob("*.py")
    if path.name not in _NOT_OPERATIONAL
)


def _halves(text: str, marker: str) -> tuple[str, str]:
    english, _, german = text.partition(marker)
    return english, german


@pytest.mark.parametrize("script", _OPERATIONAL_SCRIPTS)
def test_every_operational_script_is_listed_in_both_languages(script: str):
    english, german = _halves(README, GERMAN_MARKER)

    assert f"`scripts/{script}`" in english
    assert f"`scripts/{script}`" in german


@pytest.mark.parametrize("script", _OPERATIONAL_SCRIPTS)
def test_every_operational_script_has_a_section_in_both_languages(script: str):
    english, german = _halves(SCRIPTS_README, SCRIPTS_GERMAN_MARKER)

    assert f"## `{script}`" in english
    assert f"## `{script}`" in german


def test_the_two_language_halves_are_both_present():
    english, german = _halves(README, GERMAN_MARKER)

    assert "## Tech Stack" in english
    assert "## Tech-Stack" in german


def test_the_scripts_readme_names_no_schedule_variable_that_does_not_exist():
    assert "BACKUP_INTERVAL_DAYS" not in SCRIPTS_README
    assert "BACKUP_INTERVAL_DAYS" not in README
