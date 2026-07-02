"""Helpers for testing scripts/ modules the way they actually run in
production: as a fresh, standalone `python scripts/foo.py` process.

conftest.py imports `app.db.base` at module level for the whole test
session, which pre-registers every SQLAlchemy model before any test body
runs. A plain in-process `importlib.import_module(...)` + `configure_
mappers()` check therefore can't detect a script that forgets to import
`app.db.base` itself — it only fails for real when the script is launched
standalone (e.g. `podman run ... python scripts/migrate_to_s3.py`), where
no prior import has registered the models yet.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def assert_module_imports_and_configures_mappers(module_name: str) -> None:
    # module_name is always a hardcoded literal from our own test files,
    # never external input — S603 doesn't apply here.
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            f"import {module_name}; "
            "from sqlalchemy.orm import configure_mappers; "
            "configure_mappers()",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
