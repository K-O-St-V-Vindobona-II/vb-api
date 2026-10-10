"""The hooks that run before every commit: pinned to commits, in step with the
locked tools, and a guard that keeps environment files and shell history out
of a public repository."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_TEXT = (ROOT / ".pre-commit-config.yaml").read_text()
CONFIG = yaml.safe_load(CONFIG_TEXT)
REMOTE_REPOS = [repo for repo in CONFIG["repos"] if repo["repo"] != "local"]
LOCAL_HOOKS = {
    hook["id"]: hook
    for repo in CONFIG["repos"]
    if repo["repo"] == "local"
    for hook in repo["hooks"]
}
HOOK_IDS = {hook["id"] for repo in CONFIG["repos"] for hook in repo["hooks"]}


def _locked_version(tool: str) -> str:
    match = re.search(
        rf"^{tool}==([\d.]+)",
        (ROOT / "requirements-dev.lock").read_text(),
        re.MULTILINE,
    )
    assert match
    return match.group(1)


class TestToolPins:
    @pytest.mark.parametrize(
        "repo", REMOTE_REPOS, ids=lambda r: r["repo"].rsplit("/", 1)[-1]
    )
    def test_every_hook_repository_is_pinned_to_a_commit(self, repo):
        assert re.fullmatch(r"[0-9a-f]{40}", repo["rev"])

    def test_the_pin_comment_names_the_released_tag(self):
        tags = re.findall(r"rev: [0-9a-f]{40}\s+# frozen: (v[\d.]+)", CONFIG_TEXT)

        assert len(tags) == len(REMOTE_REPOS)

    def test_the_ruff_hook_is_the_locked_ruff(self):
        match = re.search(
            r"ruff-pre-commit\n\s+rev: [0-9a-f]{40}\s+# frozen: v([\d.]+)", CONFIG_TEXT
        )

        assert match
        assert match.group(1) == _locked_version("ruff")


class TestHookList:
    @pytest.mark.parametrize(
        "hook",
        [
            "detect-private-key",
            "check-merge-conflict",
            "check-case-conflict",
            "check-added-large-files",
            "forbid-secret-files",
            "ruff",
            "ruff-format",
            "check-router-soc",
            "pyright",
            "pytest-coverage",
        ],
    )
    def test_the_hook_is_configured(self, hook):
        assert hook in HOOK_IDS

    def test_the_test_hook_runs_on_every_commit(self):
        assert LOCAL_HOOKS["pytest-coverage"]["always_run"] is True


@pytest.fixture
def refuses():
    """Whether the guard stops a file: pre-commit selects a file for a hook
    when `files` matches and `exclude` does not (both are searched in the
    path), and a hook of the language `fail` fails for every selected file."""
    hook = LOCAL_HOOKS["forbid-secret-files"]

    def check(path: str) -> bool:
        selected = re.search(hook["files"], path)
        excluded = re.search(hook.get("exclude", "^$"), path)
        return bool(selected) and not excluded

    return check


class TestSecretFileGuard:
    def test_the_hook_always_fails_when_it_is_given_a_file(self):
        hook = LOCAL_HOOKS["forbid-secret-files"]

        assert hook["language"] == "fail"
        assert hook["entry"]

    @pytest.mark.parametrize(
        "path",
        [
            ".env",
            ".env.production",
            ".env.local",
            "sub/dir/.env.local",
            "deep/er/.env",
            ".bash_history",
            "home/.bash_history",
        ],
    )
    def test_it_refuses(self, refuses, path):
        assert refuses(path)

    @pytest.mark.parametrize(
        "path",
        [
            ".env.example",
            "sub/.env.example",
            "app/env.py",
            "alembic/env.py",
            "environment.md",
            ".envrc",
            "docs/dotenv.md",
            "bash_history.md",
            "scripts/not.bash_history.txt",
        ],
    )
    def test_it_lets_through(self, refuses, path):
        assert not refuses(path)
