"""Regression tests for app/services/downsync_service.py.

Moved here from tests/scripts/test_downsync_prod.py's TestLoadAwsSecrets
when build_prod_storage() was extracted into a shared service (used by
both scripts/downsync_prod.py and the scheduler's automated non-prod
downsync job).
"""

import pytest

from app.services.downsync_service import build_prod_storage

PROD_AWS_ENV = {
    "AWS_ACCESS_KEY_ID": "key",
    "AWS_SECRET_ACCESS_KEY": "secret",
    "AWS_BUCKET": "vindobona2-at",
    "AWS_REGION": "eu-central-1",
}


class TestBuildProdStorage:
    def test_missing_access_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)

        with pytest.raises(RuntimeError, match="AWS_ACCESS_KEY_ID is not set"):
            build_prod_storage()

    def test_missing_bucket_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "key")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
        monkeypatch.delenv("AWS_BUCKET", raising=False)

        with pytest.raises(RuntimeError, match="AWS_BUCKET is not set"):
            build_prod_storage()

    def test_builds_storage_client_from_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key, value in PROD_AWS_ENV.items():
            monkeypatch.setenv(key, value)

        storage = build_prod_storage()

        assert storage._bucket == PROD_AWS_ENV["AWS_BUCKET"]

    def test_defaults_region_when_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key, value in PROD_AWS_ENV.items():
            if key != "AWS_REGION":
                monkeypatch.setenv(key, value)
        monkeypatch.delenv("AWS_REGION", raising=False)

        # Should not raise despite the missing key (falls back to the default).
        build_prod_storage()
