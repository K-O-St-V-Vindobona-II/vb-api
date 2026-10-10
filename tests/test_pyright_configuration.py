"""Strict typing means that a value from an untyped library call does not
travel through the code as Unknown, and that the application entry point is
checked like the rest."""

import tomllib
from pathlib import Path

import pytest

PYRIGHT = tomllib.loads(
    (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
)["tool"]["pyright"]


@pytest.mark.parametrize(
    "rule",
    [
        "reportUnknownMemberType",
        "reportUnknownVariableType",
        "reportUnknownArgumentType",
    ],
)
def test_the_unknown_type_checks_are_errors(rule: str):
    assert PYRIGHT["typeCheckingMode"] == "strict"
    assert PYRIGHT[rule] == "error"


def test_the_entry_point_is_inside_the_checked_scope():
    assert "main.py" in PYRIGHT["include"]
