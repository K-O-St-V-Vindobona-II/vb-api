"""Tests for s3_mirror_service — mirror_prefix()."""

from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError

from app.core.storage import StorageClient
from app.services.s3_mirror_service import mirror_light, mirror_prefix

SOURCE_BUCKET = "mirror-source"
DEST_BUCKET = "mirror-dest"

_S3_CREDS = {
    "region_name": "us-east-1",
    "aws_access_key_id": "testing",
    "aws_secret_access_key": "testing",
}


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
        source.upload("archive/store/kept", b"content")
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


class TestMirrorSafetyNet:
    def test_empty_source_does_not_erase_the_destination(self, source, dest):
        dest.upload("archive/a", b"x")
        dest.upload("archive/b", b"x")

        with pytest.raises(RuntimeError, match="has 2; refusing to delete"):
            mirror_prefix(source, dest, prefix="archive/")

        assert sorted(dest.list_keys(prefix="archive/")) == ["archive/a", "archive/b"]

    def test_the_refusal_comes_before_a_dry_run_report(self, source, dest):
        dest.upload("archive/a", b"x")

        with pytest.raises(RuntimeError, match="refusing to delete"):
            mirror_prefix(source, dest, prefix="archive/", dry_run=True)

    def test_empty_source_and_empty_destination_is_fine(self, source, dest):
        result = mirror_prefix(source, dest, prefix="unused-prefix/")

        assert result.deleted == []
        assert result.synced == []

    def test_orphans_are_still_deleted_when_the_source_has_objects(self, source, dest):
        source.upload("m/keep", b"1")
        dest.upload("m/keep", b"1")
        dest.upload("m/orphan", b"2")

        result = mirror_prefix(source, dest, prefix="m/")

        assert result.deleted == ["m/orphan"]
        assert dest.exists("m/keep")

    def test_a_destination_object_outside_the_prefix_does_not_trigger_the_refusal(
        self, source, dest
    ):
        dest.upload("other/x", b"1")

        result = mirror_prefix(source, dest, prefix="archive/")

        assert result.deleted == []
        assert dest.exists("other/x")

    def test_keeping_orphans_never_refuses(self, source, dest):
        dest.upload("archive/a", b"x")

        result = mirror_prefix(source, dest, prefix="archive/", delete_orphans=False)

        assert result.deleted == []
        assert dest.exists("archive/a")


OLD_DUMP = "db-backups/production-2026-10-01_03-00-00.dump"
NEW_DUMP = "db-backups/production-2026-10-02_03-00-00.dump"


class TestMirrorLight:
    def test_copies_the_light_prefixes_and_only_the_newest_backup(self, source, dest):
        for key in (
            "standesdb/images/a.jpg",
            "standesdb/cache/a.jpg",
            "public/gallery/g.jpg",
            "archive/store/x.pdf",
            "archive/cache/x.jpg",
            OLD_DUMP,
            NEW_DUMP,
        ):
            source.upload(key, b"content")

        result = mirror_light(source, dest)

        assert set(result.synced) == {
            "standesdb/images/a.jpg",
            "standesdb/cache/a.jpg",
            "public/gallery/g.jpg",
            NEW_DUMP,
        }
        assert set(dest.list_keys("")) == set(result.synced)

    def test_the_newest_backup_is_chosen_by_time_not_by_name(self, source, dest):
        source.upload("db-backups/test-2026-10-03_03-00-00.dump", b"newest")
        source.upload(NEW_DUMP, b"older but sorts later as text")

        result = mirror_light(source, dest)

        assert result.synced == ["db-backups/test-2026-10-03_03-00-00.dump"]

    def test_orphans_are_deleted_only_inside_the_mirrored_prefixes(self, source, dest):
        source.upload(NEW_DUMP, b"dump")
        source.upload("standesdb/images/kept.jpg", b"kept")
        dest.upload("standesdb/images/kept.jpg", b"kept")
        dest.upload("standesdb/images/gone.jpg", b"stale")
        dest.upload("archive/store/local-only.pdf", b"keep")
        dest.upload(OLD_DUMP, b"keep")

        result = mirror_light(source, dest)

        assert result.deleted == ["standesdb/images/gone.jpg"]
        assert dest.exists("archive/store/local-only.pdf")
        assert dest.exists(OLD_DUMP)

    def test_keeping_orphans_deletes_nothing(self, source, dest):
        source.upload(NEW_DUMP, b"dump")
        dest.upload("standesdb/images/gone.jpg", b"stale")

        result = mirror_light(source, dest, delete_orphans=False)

        assert result.deleted == []
        assert dest.exists("standesdb/images/gone.jpg")

    def test_dry_run_reports_without_writing(self, source, dest):
        source.upload("standesdb/images/a.jpg", b"a")
        source.upload(NEW_DUMP, b"dump")

        result = mirror_light(source, dest, dry_run=True)

        assert set(result.synced) == {"standesdb/images/a.jpg", NEW_DUMP}
        assert dest.list_keys("") == []

    def test_without_a_backup_in_the_source_nothing_is_copied(self, source, dest):
        source.upload("standesdb/images/a.jpg", b"a")

        with pytest.raises(RuntimeError, match="no database backup"):
            mirror_light(source, dest)

        assert dest.list_keys("") == []

    def test_names_without_a_timestamp_are_not_backups(self, source, dest):
        source.upload("db-backups/notes.txt", b"not a dump")

        with pytest.raises(RuntimeError, match="no database backup"):
            mirror_light(source, dest)

    def test_the_safety_net_of_the_prefix_mirror_still_applies(self, source, dest):
        source.upload(NEW_DUMP, b"dump")
        dest.upload("public/gallery/only-here.jpg", b"stale")

        with pytest.raises(RuntimeError, match="refusing to delete"):
            mirror_light(source, dest)

    def test_errors_of_all_prefixes_are_collected(self, source, dest):
        source.upload("standesdb/images/x.jpg", b"x")
        source.upload("public/gallery/y.jpg", b"y")
        source.upload(NEW_DUMP, b"dump")
        original_put_object = dest._client.put_object

        def failing_put_object(**kwargs):
            if kwargs.get("Key") in {"standesdb/images/x.jpg", NEW_DUMP}:
                raise ClientError(
                    {"Error": {"Code": "500", "Message": "fail"}}, "PutObject"
                )
            return original_put_object(**kwargs)

        with patch.object(dest._client, "put_object", side_effect=failing_put_object):
            result = mirror_light(source, dest)

        assert set(result.errors) == {"standesdb/images/x.jpg", NEW_DUMP}
        assert result.synced == ["public/gallery/y.jpg"]

    def test_skipped_objects_are_added_up(self, source, dest):
        for key in ("standesdb/images/a.jpg", NEW_DUMP):
            source.upload(key, b"same")
            dest.upload(key, b"same")

        result = mirror_light(source, dest)

        assert result.synced == []
        assert result.skipped == 2

    def test_progress_is_reported(self, source, dest):
        source.upload(NEW_DUMP, b"dump")
        on_progress = MagicMock()

        mirror_light(source, dest, on_progress=on_progress)

        on_progress.assert_called_with(1, 1)
