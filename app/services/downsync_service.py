"""Builds a StorageClient for the prod AWS S3 bucket.

Shared between scripts/downsync_prod.py (manual, interactive) and
app/core/scheduler.py's automated non-prod downsync job — both need the
exact same prod credentials/StorageClient, just with different error
handling (CLI: print + exit; scheduler job: log + skip this run).
"""

from app.core.config import get_settings, require_setting
from app.core.storage import StorageClient


def build_prod_storage() -> StorageClient:
    """Build a StorageClient for the prod AWS S3 bucket.

    Raises RuntimeError if the required prod AWS credentials are unset.
    """
    settings = get_settings()
    return StorageClient(
        endpoint_url=None,
        access_key=require_setting(
            settings.aws_prod_access_key_id, "AWS_ACCESS_KEY_ID"
        ),
        secret_key=require_setting(
            settings.aws_prod_secret_access_key, "AWS_SECRET_ACCESS_KEY"
        ),
        bucket=require_setting(settings.aws_prod_bucket, "AWS_BUCKET"),
        region=settings.aws_prod_region,
    )
