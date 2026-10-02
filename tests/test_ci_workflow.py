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

        assert needs == {"lint", "quality", "security", "smoke-test-readonly"}

    def test_runs_of_one_ref_do_not_overtake_each_other(self, workflow):
        group = workflow["concurrency"]["group"]

        assert "github.ref" in group
        assert "github.event_name" in group
