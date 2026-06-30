import os
import sys

_VALID = frozenset({"development", "test", "qa", "production"})

_env = os.environ.get("APP_ENVIRONMENT")
if not _env:
    sys.stderr.write(
        "FATAL: APP_ENVIRONMENT is not set. "
        f"Required values: {sorted(_VALID)}. Aborting.\n"
    )
    sys.stderr.flush()
    sys.exit(1)
if _env not in _VALID:
    sys.stderr.write(
        f"FATAL: APP_ENVIRONMENT='{_env}' is invalid. "
        f"Valid values: {sorted(_VALID)}. Aborting.\n"
    )
    sys.stderr.flush()
    sys.exit(1)

APP_ENVIRONMENT: str = _env
