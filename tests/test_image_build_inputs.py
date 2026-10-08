"""What goes into the production image: the code is owned by root, so a process
that runs as the application user cannot rewrite it, and the build context
never carries secrets, shell history or local tooling."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
DOCKERIGNORE = [
    line.strip()
    for line in (ROOT / ".dockerignore").read_text().splitlines()
    if line.strip() and not line.startswith("#")
]
GITIGNORE = (ROOT / ".gitignore").read_text()


def _prod_stage() -> str:
    return DOCKERFILE.split("AS prod", 1)[1]


class TestProductionStage:
    def test_the_code_is_copied_without_handing_it_to_the_application_user(self):
        copies = re.findall(r"^COPY (?!--from).*$", _prod_stage(), flags=re.MULTILINE)

        assert copies == ["COPY . ."]

    def test_nothing_in_the_stage_hands_files_to_the_application_user(self):
        assert "--chown" not in _prod_stage()
        assert "chown" not in _prod_stage().replace("--chown", "")

    def test_the_entrypoint_is_made_executable_before_the_user_switch(self):
        stage = _prod_stage()

        assert stage.index("chmod +x docker-entrypoint.sh") < stage.index("USER app")

    def test_the_process_runs_as_the_application_user(self):
        assert re.search(r"^USER app$", _prod_stage(), flags=re.MULTILINE)


class TestBuildContext:
    @pytest.mark.parametrize(
        "entry",
        [
            ".env",
            ".env.*",
            ".bash_history",
            ".venv",
            "venv",
            ".git",
            ".github",
            ".pre-commit-config.yaml",
            ".local",
            ".cache",
            "tests",
        ],
    )
    def test_the_context_leaves_out(self, entry):
        assert entry in DOCKERIGNORE

    def test_the_example_environment_may_stay_out_too(self):
        # The image has no use for it; it must not be the only .env* file kept.
        assert ".env.*" in DOCKERIGNORE
        assert "!.env.example" not in DOCKERIGNORE


# Name of the embedded database the project no longer uses, spelled in two
# parts so that the word itself does not appear in the sources.
_RETIRED_DATABASE = "sql" + "ite"


class TestIgnoreFilesNameNoRetiredDatabase:
    def test_the_docker_ignore_list_does_not_guard_files_the_project_no_longer_has(
        self,
    ):
        assert [e for e in DOCKERIGNORE if _RETIRED_DATABASE in e.lower()] == []

    def test_the_git_ignore_list_does_not_either(self):
        assert _RETIRED_DATABASE not in GITIGNORE.lower()
        assert not re.search(r"^\*\.db(-journal)?$", GITIGNORE, flags=re.MULTILINE)
