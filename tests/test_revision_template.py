"""A freshly generated revision already satisfies the project's own lint rules and
bounds its lock wait."""

import subprocess
import sys
from pathlib import Path

import pytest
from alembic.util import format_as_comma, template_to_file

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "alembic" / "script.py.mako"


def _render(tmp_path: Path, **context: object) -> str:
    destination = tmp_path / "revision.py"
    # The names Alembic itself passes when it generates a revision.
    defaults = {
        "comma": format_as_comma,
        "message": "example",
        "up_revision": "abc123def456",
        "down_revision": "9e5abc37f6b8",
        "create_date": "2026-09-21 12:00:00.000000",
        "branch_labels": None,
        "depends_on": None,
        "imports": "",
        "upgrades": "",
        "downgrades": "",
    }
    template_to_file(TEMPLATE, destination, "utf-8", **{**defaults, **context})
    return destination.read_text()


def _ruff(*arguments: str, source: str) -> subprocess.CompletedProcess[str]:
    # The rendered text is fed to ruff as if it lived in alembic/versions, so the
    # project's per-file rules for migrations apply.
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "ruff",
            *arguments,
            "--no-cache",
            "--stdin-filename",
            "alembic/versions/abc123def456_example.py",
            "-",
        ],
        cwd=ROOT,
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )


_WITH_OPERATIONS = {
    "imports": "from sqlalchemy.dialects import postgresql",
    "upgrades": (
        'op.add_column("members", sa.Column("note", sa.Text(), nullable=True))\n'
        '    postgresql.ENUM(name="x")'
    ),
    "downgrades": 'op.drop_column("members", "note")',
}


@pytest.mark.parametrize(
    "context",
    [
        {},
        _WITH_OPERATIONS,
        {
            "upgrades": 'op.add_column("members", sa.Column("n", sa.Text()))',
            "downgrades": 'op.drop_column("members", "n")',
        },
        {"down_revision": ("aaaaaaaaaaaa", "bbbbbbbbbbbb")},
        {"down_revision": None, "branch_labels": ("feature",)},
    ],
    ids=[
        "empty",
        "with operations",
        "sa without other imports",
        "merge",
        "first revision with a branch label",
    ],
)
class TestRevisionTemplate:
    def test_a_generated_revision_is_formatted(self, tmp_path, context):
        source = _render(tmp_path, **context)

        result = _ruff("format", "--check", source=source)

        assert result.returncode == 0, result.stderr + result.stdout

    def test_a_generated_revision_passes_the_lint_rules(self, tmp_path, context):
        source = _render(tmp_path, **context)

        result = _ruff("check", source=source)

        assert result.returncode == 0, result.stdout

    def test_both_directions_bound_their_lock_wait(self, tmp_path, context):
        source = _render(tmp_path, **context)

        assert source.count("SET LOCAL lock_timeout = '5s'") == 2

    def test_the_rendered_module_is_valid_python(self, tmp_path, context):
        compile(_render(tmp_path, **context), "revision.py", "exec")
