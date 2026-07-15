"""Tests for s3_mirror_service — mirror_prefix()."""

from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError

from app.core.storage import StorageClient
from app.services.s3_mirror_service import mirror_prefix

SOURCE_BUCKET = "mirror-source"
DEST_BUCKET = "mirror-dest"

_S3_CREDS = dict(
    region_name="us-east-1",
    aws_access_key_id="testing",
    aws_secret_access_key="testing",
)


@pytest.fixture(scope="session")
def mirror_buckets(_moto_env):
    """Create dedicated source/dest S3 buckets within the session mock."""
    client = boto3.client("s3", **_S3_CREDS)
    client.create_bucket(Bucket=SOURCE_BUCKET)
    client.create_bucket(Bucket=DEST_BUCKET)
    return SOURCE_BUCKET, DEST_BUCKET


@pytest.fixture(autouse=True)
def _clean_mirror_buckets(mirror_buckets):
    """Wipe both buckets before each test for isolation."""
    client = boto3.client("s3", **_S3_CREDS)
    for bucket in mirror_buckets:
        resp = client.list_objects_v2(Bucket=bucket)
        for obj in resp.get("Contents", []):
            client.delete_object(Bucket=bucket, Key=obj["Key"])


def _make_storage(bucket: str) -> StorageClient:
    return StorageClient(
        endpoint_url="https://s3.amazonaws.com",
        access_key="testing",
        secret_key="testing",
        bucket=bucket,
    )


@pytest.fixture
def source() -> StorageClient:
    return _make_storage(SOURCE_BUCKET)


@pytest.fixture
def dest() -> StorageClient:
    return _make_storage(DEST_BUCKET)


class TestMirrorPrefix:
    def test_copies_missing_keys(self, source, dest):
        source.upload("archive/store/x", b"content", "image/png")

        result = mirror_prefix(source, dest)

        assert result.synced == ["archive/store/x"]
        data, content_type = dest.download_with_metadata("archive/store/x")
        assert data == b"content"
        assert content_type == "image/png"

    def test_skips_keys_already_in_dest(self, source, dest):
        source.upload("archive/store/x", b"new", "text/plain")
        dest.upload("archive/store/x", b"old", "text/plain")

        result = mirror_prefix(source, dest)

        assert result.synced == []
        assert result.skipped == 1
        assert dest.download("archive/store/x") == b"old"

    def test_deletes_orphans_when_delete_orphans_true(self, source, dest):
        dest.upload("archive/store/orphan", b"stale")

        result = mirror_prefix(source, dest, delete_orphans=True)

        assert result.deleted == ["archive/store/orphan"]
        assert dest.exists("archive/store/orphan") is False

    def test_no_delete_orphans_leaves_dest_keys_alone(self, source, dest):
        dest.upload("archive/store/orphan", b"stale")

        result = mirror_prefix(source, dest, delete_orphans=False)

        assert result.deleted == []
        assert dest.exists("archive/store/orphan") is True

    def test_dry_run_makes_no_writes(self, source, dest):
        source.upload("archive/store/new", b"content")
        dest.upload("archive/store/orphan", b"stale")

        result = mirror_prefix(source, dest, dry_run=True)

        assert result.synced == ["archive/store/new"]
        assert result.deleted == ["archive/store/orphan"]
        assert dest.exists("archive/store/new") is False
        assert dest.exists("archive/store/orphan") is True

    def test_default_prefix_mirrors_entire_bucket(self, source, dest):
        source.upload("archive/store/a", b"a")
        source.upload("db-backups/b", b"b")

        result = mirror_prefix(source, dest)

        assert set(result.synced) == {"archive/store/a", "db-backups/b"}
        assert dest.exists("archive/store/a")
        assert dest.exists("db-backups/b")

    def test_scopes_to_given_prefix(self, source, dest):
        source.upload("archive/store/a", b"a")
        source.upload("db-backups/b", b"b")

        result = mirror_prefix(source, dest, prefix="archive/")

        assert result.synced == ["archive/store/a"]
        assert dest.exists("db-backups/b") is False

    def test_upload_error_is_recorded_not_raised(self, source, dest):
        source.upload("archive/store/x", b"content")
        source.upload("archive/store/y", b"content")
        original_put_object = dest._client.put_object

        def failing_put_object(**kwargs):
            if kwargs.get("Key") == "archive/store/x":
                raise ClientError(
                    {"Error": {"Code": "500", "Message": "fail"}},
                    "PutObject",
                )
            return original_put_object(**kwargs)

        with patch.object(dest._client, "put_object", side_effect=failing_put_object):
            result = mirror_prefix(source, dest)

        assert result.errors == ["archive/store/x"]
        assert result.synced == ["archive/store/y"]
        assert dest.exists("archive/store/y") is True

    def test_on_progress_called_during_copy(self, source, dest):
        source.upload("archive/store/a", b"a")
        source.upload("archive/store/b", b"b")
        on_progress = MagicMock()

        mirror_prefix(source, dest, on_progress=on_progress)

        on_progress.assert_called_with(2, 2)

    def test_empty_source_and_dest_is_a_no_op(self, source, dest):
        result = mirror_prefix(source, dest)

        assert result.synced == []
        assert result.skipped == 0
        assert result.deleted == []
        assert result.has_errors is False
