"""Regression tests for scripts/migration_archive/migrate_to_s3.py."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import scripts.migration_archive.migrate_to_s3 as migrate_to_s3
from app.models.archive_store_item import ArchiveStoreItem
from app.models.member import Member
from app.models.standesdb_image import StandesdbImage
from tests.scripts._subprocess_helpers import (
    assert_module_imports_and_configures_mappers,
)


def test_standalone_import_configures_mappers_without_error() -> None:
    """Run as a fresh process (not sharing pytest's conftest-populated
    SQLAlchemy registry) — this is what actually catches a missing
    `import app.db.base`, which crashes real standalone execution
    (`python scripts/migration_archive/migrate_to_s3.py`) with
    `InvalidRequestError: ... failed to locate a name ('Member')` on the
    ArchiveStoreItem -> Member relationship, since StandesdbImage/
    ArchiveStoreItem are queried but Member is never otherwise imported by
    this script."""
    assert_module_imports_and_configures_mappers(
        "scripts.migration_archive.migrate_to_s3"
    )


def test_get_s3_client_defaults_to_none_endpoint_when_unset(monkeypatch) -> None:
    """A hardcoded localhost:9000 fallback would make this script try to
    hit a local MinIO instead of real AWS S3 in production, where
    S3_ENDPOINT_URL is intentionally left unset."""
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    with patch.object(migrate_to_s3.boto3, "client") as mock_client:
        migrate_to_s3.get_s3_client()
        assert mock_client.call_args.kwargs["endpoint_url"] is None


def test_get_s3_client_uses_s3_region_env_var(monkeypatch) -> None:
    """region_name must come from S3_REGION, not be hardcoded to
    us-east-1 — a hardcoded region causes a SigV4 region mismatch against
    buckets in other regions (e.g. the real prod bucket in eu-central-1)."""
    monkeypatch.setenv("S3_REGION", "eu-central-1")
    with patch.object(migrate_to_s3.boto3, "client") as mock_client:
        migrate_to_s3.get_s3_client()
        assert mock_client.call_args.kwargs["region_name"] == "eu-central-1"


def test_get_s3_client_region_defaults_to_us_east_1(monkeypatch) -> None:
    monkeypatch.delenv("S3_REGION", raising=False)
    with patch.object(migrate_to_s3.boto3, "client") as mock_client:
        migrate_to_s3.get_s3_client()
        assert mock_client.call_args.kwargs["region_name"] == "us-east-1"


def test_build_content_type_map_reads_correct_columns(db_session) -> None:
    """Regression test for the column-only rewrite: build_content_type_map
    must still return the same {sha256_hash: content_type} mapping as
    before, now via db.query(Model.col1, Model.col2) instead of
    db.query(Model).all() (the latter eagerly joins ArchiveStoreItem.member
    and OOM-killed the process in production on 27k+ rows)."""
    member = Member(vorname="Test", nachname="User")
    db_session.add(member)
    db_session.commit()

    now = datetime.now(UTC)
    db_session.add(
        StandesdbImage(
            owner_member_id=member.id,
            type="image/jpeg",
            sha256_hash="img_hash_1",
        )
    )
    db_session.add(
        StandesdbImage(
            owner_member_id=member.id,
            type=None,
            sha256_hash="img_hash_no_type",
        )
    )
    db_session.add(
        ArchiveStoreItem(
            name="testfile",
            extension="pdf",
            mime_type="application/pdf",
            size=100,
            sha256_hash="arch_hash_1",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    image_types, archive_types = migrate_to_s3.build_content_type_map(db_session)

    assert image_types == {"img_hash_1": "image/jpeg"}
    assert archive_types == {"arch_hash_1": "application/pdf"}


def test_verify_excludes_soft_deleted_standesdb_images(db_session) -> None:
    """The deleted_at filter must survive the column-only rewrite."""
    member = Member(vorname="Test", nachname="User")
    db_session.add(member)
    db_session.commit()

    db_session.add(
        StandesdbImage(
            owner_member_id=member.id,
            sha256_hash="active_hash",
        )
    )
    db_session.add(
        StandesdbImage(
            owner_member_id=member.id,
            sha256_hash="deleted_hash",
            deleted_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    mock_client = MagicMock()
    mock_client.head_object.return_value = {}

    migrate_to_s3.verify(mock_client, "test-bucket", db_session)

    checked_keys = {
        call.kwargs["Key"] for call in mock_client.head_object.call_args_list
    }
    assert "standesdb/images/active_hash" in checked_keys
    assert "standesdb/images/deleted_hash" not in checked_keys
