import os
import sys
from typing import NoReturn

_VALID_ENVIRONMENTS = frozenset({"development", "test", "qa", "production"})


def _fail(message: str) -> NoReturn:
    sys.stderr.write(f"FATAL: {message} Aborting.\n")
    sys.stderr.flush()
    sys.exit(1)


_env = os.environ.get("APP_ENVIRONMENT")
if not _env:
    _fail(
        f"APP_ENVIRONMENT is not set. Required values: {sorted(_VALID_ENVIRONMENTS)}."
    )
if _env not in _VALID_ENVIRONMENTS:
    _fail(
        f"APP_ENVIRONMENT='{_env}' is invalid. "
        f"Valid values: {sorted(_VALID_ENVIRONMENTS)}."
    )

APP_ENVIRONMENT: str = _env

_cors_origins_raw = os.environ.get("CORS_ORIGINS")
if not _cors_origins_raw:
    _fail(
        "CORS_ORIGINS is not set. "
        "Provide a comma-separated list of allowed frontend origins."
    )

CORS_ORIGINS: list[str] = [
    origin.strip() for origin in _cors_origins_raw.split(",") if origin.strip()
]
if not CORS_ORIGINS:
    _fail("CORS_ORIGINS is set but contains no valid origins.")
