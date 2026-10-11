#!/usr/bin/env python3
"""Checks statement and branch coverage of the last test run against separate
minimums.

pytest-cov's --cov-fail-under judges a single figure that mixes statements and
branches, so a drop in one can hide behind the other. This gate reads the JSON
report of the run (coverage.json) and holds each figure to its own minimum.
The minimums live in pyproject.toml under [tool.coverage_gate].

Usage:
    python scripts/check_coverage_gate.py [path/to/coverage.json]
"""

import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
DEFAULT_REPORT = ROOT / "coverage.json"

EXIT_FAILED = 1
EXIT_UNUSABLE = 2


class GateError(Exception):
    """The gate cannot be evaluated: configuration or report is unusable."""

    @classmethod
    def not_a_number(cls, source: str, path: tuple[str, ...]) -> GateError:
        return cls(f"{source}: '{'.'.join(path)}' is missing or not a number")

    @classmethod
    def no_report(cls, report: Path) -> GateError:
        return cls(
            f"{report} not found: run the test suite first (it writes the report)"
        )

    @classmethod
    def invalid_json(cls, report: Path, error: json.JSONDecodeError) -> GateError:
        return cls(f"{report} is not valid JSON: {error}")


@dataclass(frozen=True)
class Figures:
    statements: float
    branches: float


def _number(root: object, path: tuple[str, ...], source: str) -> float:
    """Follow `path` through nested tables and return the number found there."""
    node = root
    for key in path:
        if not isinstance(node, dict):
            raise GateError.not_a_number(source, path)
        node = cast("dict[str, object]", node).get(key)
    if isinstance(node, bool) or not isinstance(node, int | float):
        raise GateError.not_a_number(source, path)
    return float(node)


def load_minimums(pyproject: Path) -> Figures:
    config: object = tomllib.loads(pyproject.read_text())
    source = pyproject.name
    return Figures(
        statements=_number(config, ("tool", "coverage_gate", "statements"), source),
        branches=_number(config, ("tool", "coverage_gate", "branches"), source),
    )


def load_measured(report: Path) -> Figures:
    try:
        data: object = json.loads(report.read_text())
    except FileNotFoundError as exc:
        raise GateError.no_report(report) from exc
    except json.JSONDecodeError as exc:
        raise GateError.invalid_json(report, exc) from exc
    source = report.name
    return Figures(
        statements=_number(data, ("totals", "percent_statements_covered"), source),
        branches=_number(data, ("totals", "percent_branches_covered"), source),
    )


def find_failures(measured: Figures, minimums: Figures) -> list[str]:
    failures: list[str] = []
    if measured.statements < minimums.statements:
        failures.append(
            f"statements {measured.statements:.2f} % < {minimums.statements:.2f} %"
        )
    if measured.branches < minimums.branches:
        failures.append(
            f"branches {measured.branches:.2f} % < {minimums.branches:.2f} %"
        )
    return failures


def main(argv: list[str]) -> int:
    report = Path(argv[1]) if len(argv) > 1 else DEFAULT_REPORT
    try:
        minimums = load_minimums(PYPROJECT)
        measured = load_measured(report)
    except GateError as exc:
        print(f"check_coverage_gate: {exc}")
        return EXIT_UNUSABLE

    failures = find_failures(measured, minimums)
    if not failures:
        print(
            "check_coverage_gate: OK - "
            f"statements {measured.statements:.2f} % "
            f"(min {minimums.statements:.2f} %), "
            f"branches {measured.branches:.2f} % "
            f"(min {minimums.branches:.2f} %)."
        )
        return 0

    print("check_coverage_gate: FAILED")
    for failure in failures:
        print(f"  {failure}")
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main(sys.argv))
