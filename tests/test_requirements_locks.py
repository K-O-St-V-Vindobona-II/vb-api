"""The lock files are what CI, the dev image and the production image install,
so they have to pin and hash everything and agree with each other."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_PIN = re.compile(r"^([A-Za-z0-9_.\-]+(?:\[[^\]]+\])?)==(\S+) \\$", re.MULTILINE)
_HASH = re.compile(r"^    --hash=sha256:[0-9a-f]{64}( \\)?$")
_STAGE = re.compile(r"^FROM .* AS (\w+)$", re.MULTILINE)
_PIP_INSTALL = re.compile(r"^RUN pip install .*$", re.MULTILINE)


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.split("[", maxsplit=1)[0]).lower()


def _pins(lock: str) -> dict[str, str]:
    text = (ROOT / lock).read_text()
    return {_normalise(m.group(1)): m.group(2) for m in _PIN.finditer(text)}


def _direct_requirements(path: str) -> set[str]:
    names: set[str] = set()
    for raw in (ROOT / path).read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith(("#", "-r")):
            names.add(_normalise(re.split(r"[\[<>=!~ ]", line, maxsplit=1)[0]))
    return names


def _stage_installs() -> dict[str, list[str]]:
    """Map every Dockerfile stage name to its ``RUN pip install`` lines."""
    text = (ROOT / "Dockerfile").read_text()
    headers = list(_STAGE.finditer(text))
    installs: dict[str, list[str]] = {}
    for position, header in enumerate(headers):
        end = headers[position + 1].start() if position + 1 < len(headers) else None
        installs[header.group(1)] = _PIP_INSTALL.findall(text[header.end() : end])
    return installs


@pytest.mark.parametrize("lock", ["requirements.lock", "requirements-dev.lock"])
class TestEachLock:
    def test_every_pin_has_hashes(self, lock: str):
        lines = (ROOT / lock).read_text().splitlines()
        pin_lines = [i for i, line in enumerate(lines) if _PIN.match(line + "\n")]

        assert pin_lines
        for index in pin_lines:
            assert _HASH.match(lines[index + 1]), f"no hash after {lines[index]}"

    def test_no_pin_is_left_without_a_version(self, lock: str):
        text = (ROOT / lock).read_text()

        unpinned = [
            line
            for line in text.splitlines()
            if line and not line.startswith((" ", "#")) and "==" not in line
        ]
        assert unpinned == []


class TestLocksAgree:
    def test_the_dev_lock_pins_what_production_pins_to_the_same_versions(self):
        production, dev = _pins("requirements.lock"), _pins("requirements-dev.lock")

        assert production
        assert {name: dev.get(name) for name in production} == production

    @pytest.mark.parametrize(
        ("requirements", "lock"),
        [
            ("requirements.txt", "requirements.lock"),
            ("requirements-dev.txt", "requirements-dev.lock"),
        ],
    )
    def test_every_direct_requirement_is_locked(self, requirements: str, lock: str):
        assert _direct_requirements(requirements) - set(_pins(lock)) == set()


class TestDockerfile:
    def test_every_stage_installs_its_lock_with_hashes_enforced(self):
        installs = _stage_installs()

        assert set(installs) == {"base", "builder", "dev", "prod"}
        assert len(installs["builder"]) == 1
        assert len(installs["dev"]) == 1
        assert "--require-hashes" in installs["builder"][0]
        assert "--require-hashes" in installs["dev"][0]

    def test_the_production_builder_installs_the_runtime_lock(self):
        (install,) = _stage_installs()["builder"]

        assert install.endswith("-r requirements.lock")

    def test_the_dev_image_installs_the_lock_that_ci_uses(self):
        (install,) = _stage_installs()["dev"]

        assert install.endswith("-r requirements-dev.lock")
