#!/usr/bin/env python3
"""Checks statement, branch and function coverage of the last test run against
separate minimums.

pytest-cov's --cov-fail-under judges a single figure that mixes statements and
branches, so a drop in one can hide behind the other. This gate reads the JSON
report of the run (coverage.json) and holds each figure to its own minimum.
Function coverage is the share of functions and methods of which at least one
statement was executed; the report lists every function with its own totals.
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
    def unusable(cls, source: str, path: tuple[str, ...]) -> GateError:
        return cls(f"{source}: '{'.'.join(path)}' is missing or has the wrong type")

    @classmethod
    def no_functions(cls, source: str) -> GateError:
        return cls(f"{source}: the report lists no functions to measure")

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
    functions: float


def _node(root: object, path: tuple[str, ...], source: str) -> object:
    """Follow `path` through nested tables and return what is found there."""
    node = root
    for key in path:
        if not isinstance(node, dict):
            raise GateError.unusable(source, path)
        node = cast("dict[str, object]", node).get(key)
    return node


def _table(root: object, path: tuple[str, ...], source: str) -> dict[str, object]:
    node = _node(root, path, source)
    if not isinstance(node, dict):
        raise GateError.unusable(source, path)
    return cast("dict[str, object]", node)


def _number(root: object, path: tuple[str, ...], source: str) -> float:
    node = _node(root, path, source)
    if isinstance(node, bool) or not isinstance(node, int | float):
        raise GateError.unusable(source, path)
    return float(node)


def _function_was_entered(function: object, source: str) -> bool | None:
    """True when a statement of the function ran, False when none did, None for
    a function without statements of its own (nothing to run)."""
    summary = ("summary", "num_statements")
    if _number(function, summary, source) == 0:
        return None
    return _number(function, ("summary", "covered_lines"), source) > 0


def _function_percent(data: object, source: str) -> float:
    entered: list[bool] = []
    for file_path, info in _table(data, ("files",), source).items():
        file_source = f"{source} {file_path}"
        for name, function in _table(info, ("functions",), file_source).items():
            # The unnamed entry holds the module level code, not a function.
            outcome = _function_was_entered(function, file_source) if name else None
            if outcome is not None:
                entered.append(outcome)
    if not entered:
        raise GateError.no_functions(source)
    return 100.0 * sum(entered) / len(entered)


def load_minimums(pyproject: Path) -> Figures:
    config: object = tomllib.loads(pyproject.read_text())
    source = pyproject.name
    return Figures(
        statements=_number(config, ("tool", "coverage_gate", "statements"), source),
        branches=_number(config, ("tool", "coverage_gate", "branches"), source),
        functions=_number(config, ("tool", "coverage_gate", "functions"), source),
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
        functions=_function_percent(data, source),
    )


def _compare(
    measured: Figures, minimums: Figures
) -> tuple[tuple[str, float, float], ...]:
    return (
        ("statements", measured.statements, minimums.statements),
        ("branches", measured.branches, minimums.branches),
        ("functions", measured.functions, minimums.functions),
    )


def find_failures(measured: Figures, minimums: Figures) -> list[str]:
    return [
        f"{name} {actual:.2f} % < {minimum:.2f} %"
        for name, actual, minimum in _compare(measured, minimums)
        if actual < minimum
    ]


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
        summary = ", ".join(
            f"{name} {actual:.2f} % (min {minimum:.2f} %)"
            for name, actual, minimum in _compare(measured, minimums)
        )
        print(f"check_coverage_gate: OK - {summary}.")
        return 0

    print("check_coverage_gate: FAILED")
    for failure in failures:
        print(f"  {failure}")
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main(sys.argv))
