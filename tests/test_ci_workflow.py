"""Policy tests for the pipeline: what may publish the production image.
PyYAML is available through uvicorn[standard]."""

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci-cd.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


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
