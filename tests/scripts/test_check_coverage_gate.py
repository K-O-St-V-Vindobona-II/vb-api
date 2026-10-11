"""Tests for scripts/check_coverage_gate.py: the separate minimums for
statement and branch coverage."""

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.check_coverage_gate as gate
from scripts.check_coverage_gate import (
    Figures,
    GateError,
    find_failures,
    load_measured,
    load_minimums,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_report(path: Path, statements: float, branches: float) -> Path:
    path.write_text(
        json.dumps(
            {
                "totals": {
                    "percent_statements_covered": statements,
                    "percent_branches_covered": branches,
                }
            }
        )
    )
    return path


def _write_pyproject(path: Path, statements: float, branches: float) -> Path:
    path.write_text(
        f"[tool.coverage_gate]\nstatements = {statements}\nbranches = {branches}\n"
    )
    return path


class TestLoadMeasured:
    def test_reads_both_figures_of_the_report_totals(self, tmp_path):
        report = _write_report(tmp_path / "coverage.json", 98.5, 93.25)

        assert load_measured(report) == Figures(statements=98.5, branches=93.25)

    def test_a_missing_report_asks_for_a_test_run_first(self, tmp_path):
        with pytest.raises(GateError, match="run the test suite first"):
            load_measured(tmp_path / "coverage.json")

    def test_a_report_that_is_not_json_is_refused(self, tmp_path):
        report = tmp_path / "coverage.json"
        report.write_text("not json")

        with pytest.raises(GateError, match="not valid JSON"):
            load_measured(report)

    @pytest.mark.parametrize(
        "content",
        [
            [],
            {},
            {"totals": []},
            {"totals": {"percent_statements_covered": 99.0}},
            {
                "totals": {
                    "percent_statements_covered": 99.0,
                    "percent_branches_covered": "93",
                }
            },
            {
                "totals": {
                    "percent_statements_covered": 99.0,
                    "percent_branches_covered": True,
                }
            },
            {
                "totals": {
                    "percent_statements_covered": None,
                    "percent_branches_covered": 93.0,
                }
            },
        ],
    )
    def test_a_report_without_both_numbers_is_refused(self, tmp_path, content):
        report = tmp_path / "coverage.json"
        report.write_text(json.dumps(content))

        with pytest.raises(GateError, match="missing or not a number"):
            load_measured(report)

    def test_whole_numbers_are_accepted(self, tmp_path):
        report = _write_report(tmp_path / "coverage.json", 100, 90)

        assert load_measured(report) == Figures(statements=100.0, branches=90.0)


class TestLoadMinimums:
    def test_reads_both_minimums_of_the_gate_section(self, tmp_path):
        pyproject = _write_pyproject(tmp_path / "pyproject.toml", 97, 92.5)

        assert load_minimums(pyproject) == Figures(statements=97.0, branches=92.5)

    def test_a_pyproject_without_the_gate_section_is_refused(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.ruff]\nline-length = 88\n")

        with pytest.raises(GateError, match="missing or not a number"):
            load_minimums(pyproject)

    def test_a_gate_section_with_one_minimum_is_refused(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("[tool.coverage_gate]\nstatements = 97\n")

        with pytest.raises(GateError, match=r"tool\.coverage_gate\.branches"):
            load_minimums(pyproject)


class TestFindFailures:
    minimums = Figures(statements=97.0, branches=92.0)

    def test_figures_above_the_minimums_pass(self):
        assert find_failures(Figures(98.0, 93.0), self.minimums) == []

    def test_figures_exactly_at_the_minimums_pass(self):
        assert find_failures(Figures(97.0, 92.0), self.minimums) == []

    def test_low_statements_alone_fail_even_with_excellent_branches(self):
        failures = find_failures(Figures(96.99, 100.0), self.minimums)

        assert len(failures) == 1
        assert failures[0].startswith("statements 96.99 %")

    def test_low_branches_alone_fail_even_with_excellent_statements(self):
        failures = find_failures(Figures(100.0, 91.99), self.minimums)

        assert len(failures) == 1
        assert failures[0].startswith("branches 91.99 %")

    def test_both_failures_are_reported(self):
        assert len(find_failures(Figures(90.0, 80.0), self.minimums)) == 2


class TestMain:
    @pytest.fixture
    def pyproject(self, tmp_path):
        return _write_pyproject(tmp_path / "pyproject.toml", 97, 92)

    def _run(self, pyproject: Path, argv: list[str]) -> int:
        with patch.object(gate, "PYPROJECT", pyproject):
            return main(["check_coverage_gate.py", *argv])

    def test_passes_and_reports_both_figures(self, tmp_path, pyproject, capsys):
        report = _write_report(tmp_path / "coverage.json", 98.87, 93.78)

        assert self._run(pyproject, [str(report)]) == 0
        output = capsys.readouterr().out
        assert "OK" in output
        assert "statements 98.87 % (min 97.00 %)" in output
        assert "branches 93.78 % (min 92.00 %)" in output

    def test_fails_with_exit_code_1_and_names_the_figure(
        self, tmp_path, pyproject, capsys
    ):
        report = _write_report(tmp_path / "coverage.json", 99.0, 90.0)

        assert self._run(pyproject, [str(report)]) == 1
        output = capsys.readouterr().out
        assert "FAILED" in output
        assert "branches 90.00 % < 92.00 %" in output
        assert "statements" not in output.split("FAILED")[1]

    def test_an_unusable_report_gives_exit_code_2(self, tmp_path, pyproject, capsys):
        assert self._run(pyproject, [str(tmp_path / "missing.json")]) == 2
        assert "run the test suite first" in capsys.readouterr().out

    def test_an_unusable_configuration_gives_exit_code_2(self, tmp_path, capsys):
        report = _write_report(tmp_path / "coverage.json", 99.0, 99.0)
        empty = tmp_path / "pyproject.toml"
        empty.write_text("")

        assert self._run(empty, [str(report)]) == 2
        assert "tool.coverage_gate" in capsys.readouterr().out

    def test_reads_the_default_report_when_no_path_is_given(self, tmp_path, pyproject):
        report = _write_report(tmp_path / "default.json", 99.0, 99.0)

        with patch.object(gate, "DEFAULT_REPORT", report):
            assert self._run(pyproject, []) == 0


class TestAsAStandaloneScript:
    def test_runs_as_a_fresh_process_the_way_the_pipeline_calls_it(self, tmp_path):
        report = _write_report(tmp_path / "coverage.json", 100.0, 100.0)

        # The arguments are literals and a path made by this test; S603 does
        # not apply.
        result = subprocess.run(  # noqa: S603
            [sys.executable, "scripts/check_coverage_gate.py", str(report)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK" in result.stdout

    def test_exits_with_1_in_a_fresh_process_when_a_figure_is_too_low(self, tmp_path):
        report = _write_report(tmp_path / "coverage.json", 100.0, 1.0)

        result = subprocess.run(  # noqa: S603
            [sys.executable, "scripts/check_coverage_gate.py", str(report)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 1


class TestTheConfiguredMinimums:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())

    def test_both_minimums_are_percentages(self):
        minimums = load_minimums(gate.PYPROJECT)

        assert 0 < minimums.statements <= 100
        assert 0 < minimums.branches <= 100

    def test_the_statement_minimum_is_not_below_the_combined_floor_of_the_test_run(
        self,
    ):
        addopts = self.config["tool"]["pytest"]["ini_options"]["addopts"]
        match = re.search(r"--cov-fail-under=(\d+(?:\.\d+)?)", addopts)
        assert match is not None
        floor = float(match.group(1))
        assert load_minimums(gate.PYPROJECT).statements >= floor

    def test_the_test_run_writes_the_json_report_the_gate_reads(self):
        addopts = self.config["tool"]["pytest"]["ini_options"]["addopts"]

        assert "--cov-report=json" in addopts
        assert gate.DEFAULT_REPORT.name == "coverage.json"

    def test_branch_measurement_is_on_or_the_branch_figure_would_be_empty(self):
        assert self.config["tool"]["coverage"]["run"]["branch"] is True
