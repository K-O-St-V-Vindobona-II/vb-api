"""Loads prod AWS S3 credentials and builds a StorageClient for them.

Shared between scripts/downsync_prod.py (manual, interactive) and
app/core/scheduler.py's automated non-prod downsync job — both need the
exact same prod credentials/StorageClient, just with different error
handling (CLI: print + exit; scheduler job: log + skip this run).
"""

from pathlib import Path

from app.core.storage import StorageClient

AWS_ENV_PATH = "/run/secrets/aws-prod.env"


def _load_env_file(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        with Path(path).open() as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env


def load_aws_env() -> dict[str, str]:
    """Load prod AWS S3 credentials from AWS_ENV_PATH.

    Raises RuntimeError if credentials are missing or incomplete.
    """
    aws_env = _load_env_file(AWS_ENV_PATH)
    if not aws_env.get("AWS_ACCESS_KEY_ID"):
        msg = f"No AWS credentials found in {AWS_ENV_PATH}"
        raise RuntimeError(msg)
    if not aws_env.get("AWS_BUCKET"):
        msg = f"AWS_BUCKET not set in {AWS_ENV_PATH}"
        raise RuntimeError(msg)
    return aws_env


def build_prod_storage(aws_env: dict[str, str]) -> StorageClient:
    return StorageClient(
        endpoint_url=None,
        access_key=aws_env["AWS_ACCESS_KEY_ID"],
        secret_key=aws_env["AWS_SECRET_ACCESS_KEY"],
        bucket=aws_env["AWS_BUCKET"],
        region=aws_env.get("AWS_REGION", "eu-central-1"),
    )
