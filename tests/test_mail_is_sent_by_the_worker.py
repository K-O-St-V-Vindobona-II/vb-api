"""Outgoing mail is sent by the worker (through ARQ), never inside a request.

Routes and services may render a mail (render_template) but must not import a
send function from the mailer: a slow mail server would hold a request thread,
and a failed delivery would have no retry.
"""

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
REQUEST_SIDE_DIRS = (APP_DIR / "api", APP_DIR / "services")
MAILER_MODULE = "app.core.mailer"


def _mailer_send_imports(source: str) -> list[str]:
    tree = ast.parse(source)
    return [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == MAILER_MODULE
        for alias in node.names
        if alias.name.startswith("send_")
    ]


def _offenders() -> list[str]:
    return [
        f"{path.relative_to(APP_DIR)}: {name}"
        for directory in REQUEST_SIDE_DIRS
        for path in sorted(directory.rglob("*.py"))
        for name in _mailer_send_imports(path.read_text())
    ]


class TestMailIsSentByTheWorker:
    def test_no_route_or_service_imports_a_send_function_from_the_mailer(self):
        assert _offenders() == []

    def test_the_scan_detects_a_send_import(self):
        source = "from app.core.mailer import render_template, send_to_recipients\n"

        assert _mailer_send_imports(source) == ["send_to_recipients"]

    def test_rendering_alone_is_allowed(self):
        assert (
            _mailer_send_imports("from app.core.mailer import render_template\n") == []
        )
