"""Regression tests for app/services/downsync_service.py.

Moved here from tests/scripts/test_downsync_prod.py's TestLoadAwsSecrets
when load_aws_env()/build_prod_storage() were extracted into a shared
service (used by both scripts/downsync_prod.py and the scheduler's
automated non-prod downsync job).
"""

from unittest.mock import patch

import pytest

from app.services.downsync_service import build_prod_storage, load_aws_env

SECRETS = {
    "AWS_ACCESS_KEY_ID": "key",
    "AWS_SECRET_ACCESS_KEY": "secret",
    "AWS_BUCKET": "vindobona2-at",
    "AWS_REGION": "eu-central-1",
}


class TestLoadAwsEnv:
    def test_missing_access_key_raises(self) -> None:
        with (
            patch("app.services.downsync_service._load_env_file", return_value={}),
            pytest.raises(RuntimeError, match="No AWS credentials found"),
        ):
            load_aws_env()

    def test_missing_bucket_raises(self) -> None:
        with (
            patch(
                "app.services.downsync_service._load_env_file",
                return_value={"AWS_ACCESS_KEY_ID": "key"},
            ),
            pytest.raises(RuntimeError, match="AWS_BUCKET not set"),
        ):
            load_aws_env()

    def test_returns_env_when_complete(self) -> None:
        with patch(
            "app.services.downsync_service._load_env_file", return_value=SECRETS
        ):
            result = load_aws_env()

        assert result == SECRETS


class TestBuildProdStorage:
    def test_builds_storage_client_from_env(self) -> None:
        storage = build_prod_storage(SECRETS)

        assert storage._bucket == SECRETS["AWS_BUCKET"]

    def test_defaults_region_when_not_set(self) -> None:
        env_without_region = {k: v for k, v in SECRETS.items() if k != "AWS_REGION"}

        # Should not raise despite the missing key (falls back to default).
        build_prod_storage(env_without_region)
