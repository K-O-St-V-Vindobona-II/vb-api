"""Policy tests for the pipeline: what may publish the production image, and
what the actions run with. PyYAML is available through uvicorn[standard]."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci-cd.yml"
DEPENDABOT = ROOT / ".github/dependabot.yml"
_PINNED = re.compile(r"^[\w./-]+@[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _steps(workflow: dict):
    for job in workflow["jobs"].values():
        yield from job.get("steps", [])


class TestReleaseJob:
    def test_only_main_may_publish(self, workflow):
        condition = workflow["jobs"]["release-image"]["if"]

        assert "github.ref == 'refs/heads/main'" in condition

    def test_only_a_push_or_a_manual_run_publishes(self, workflow):
        condition = workflow["jobs"]["release-image"]["if"]

        assert "'push'" in condition
        assert "'workflow_dispatch'" in condition
        assert "pull_request" not in condition

    def test_release_waits_for_every_check(self, workflow):
        needs = set(workflow["jobs"]["release-image"]["needs"])

        assert needs == {
            "lint",
            "quality",
            "security",
            "dependency-audit",
            "smoke-test-readonly",
        }

    def test_runs_of_one_ref_do_not_overtake_each_other(self, workflow):
        group = workflow["concurrency"]["group"]

        assert "github.ref" in group
        assert "github.event_name" in group


class TestLockedDependencies:
    def test_the_locked_dependencies_are_audited_with_hashes_enforced(self, workflow):
        steps = workflow["jobs"]["dependency-audit"]["steps"]
        commands = "\n".join(step.get("run", "") for step in steps)

        assert "--require-hashes -r requirements-dev.lock" in commands
        assert "pip-audit -r requirements.lock" in commands
        assert "pip-audit -r requirements-dev.lock" in commands

    def test_every_install_from_a_lock_enforces_hashes(self, workflow):
        installs = [
            step["run"]
            for job in workflow["jobs"].values()
            for step in job["steps"]
            if "uv pip install" in step.get("run", "")
        ]

        assert len(installs) == 3
        assert all("--require-hashes" in command for command in installs)


class TestPermissions:
    def test_the_default_token_is_read_only(self, workflow):
        assert workflow["permissions"] == {"contents": "read"}

    def test_only_the_release_job_may_write_packages(self, workflow):
        writers = [
            name
            for name, job in workflow["jobs"].items()
            if "packages" in job.get("permissions", {})
        ]

        assert writers == ["release-image"]

    def test_the_code_scan_keeps_read_access_next_to_its_write_scope(self, workflow):
        permissions = workflow["jobs"]["security"]["permissions"]

        assert permissions == {"contents": "read", "security-events": "write"}

    def test_every_job_is_time_boxed(self, workflow):
        assert all("timeout-minutes" in job for job in workflow["jobs"].values())


class TestSupplyChain:
    def test_every_action_is_pinned_to_a_commit(self, workflow):
        used = [step["uses"] for step in _steps(workflow) if "uses" in step]

        assert used
        assert [u for u in used if not _PINNED.match(u)] == []

    def test_the_smoke_test_runs_with_the_production_hardening(self, workflow):
        smoke = workflow["jobs"]["smoke-test-readonly"]["steps"]
        runs = [s["run"] for s in smoke if "docker run" in s.get("run", "")]

        assert len(runs) == 2
        assert all("--cap-drop=ALL" in r and "no-new-privileges" in r for r in runs)
        assert all("--read-only" in r for r in runs)
        assert all("--tmpfs /tmp:size=256m" in r for r in runs)

    def test_dependabot_keeps_the_action_pins_and_the_base_image_current(self):
        config = yaml.safe_load(DEPENDABOT.read_text())
        updates = {u["package-ecosystem"]: u for u in config["updates"]}

        assert set(updates) == {"github-actions", "docker"}
        assert all(u["schedule"]["interval"] == "weekly" for u in updates.values())
        assert all(u["directory"] == "/" for u in updates.values())
