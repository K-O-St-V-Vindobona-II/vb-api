"""Regression coverage for the TIMESTAMP -> TIMESTAMPTZ migration (Alembic
revision 740805d424aa): psycopg2 now returns tz-aware datetimes for every
converted column, where it previously returned naive ones. These tests lock
in that the existing naive-input guards become harmless no-ops on aware
input, and that endpoints backed by tz-aware columns produce correctly
offset-suffixed output.

Note: app/schemas/tracking.py and app/schemas/standesdb.py used to each
declare their own identical _ensure_utc/UtcDatetime pair; that duplication
was consolidated into app/schemas/base.py (see tests/test_schemas_base.py
for its unit tests). app/schemas/archive.py used to bypass Pydantic
validation entirely (str-typed timestamp fields, hand-formatted via
archive_service._ts()); it now uses the same UtcDatetime type, which is why
there is no archive-specific _ts()-level test here anymore - the
end-to-end endpoint tests below cover the same ground."""

from datetime import UTC, date, datetime

import bcrypt

from app.api.deps import _ensure_tz_aware as _deps_ensure_tz_aware
from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
from app.models.archive_store_item import ArchiveStoreItem
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.public_gallery_image import PublicGalleryImage
from app.models.role import Role
from app.schemas.public_gallery import GalleryImageAdminResponse
from app.services.auth_service import _ensure_tz_aware as _auth_ensure_tz_aware
from app.services.auth_service import create_user_session

AWARE_DT = datetime(2026, 1, 15, 12, 30, 0, tzinfo=UTC)


def _login_admin(db) -> dict[str, str]:
    """Org 'vbw' + role 'internetreferent' grants both archiveAdmin and
    publicContentEditor (see app/services/permission_service.py) — one
    seed covers both endpoint tests below."""
    db.add(Org(id="vbw", label="VBW", order=1))
    db.add(Role(id="internetreferent", group="funktion", label="IR", order=0))
    db.commit()

    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    member = Member(
        email="tz-admin@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        org_id="vbw",
    )
    db.add(member)
    db.commit()
    db.add(
        MemberRole(
            member_id=member.id_uuid,
            role_id="internetreferent",
            startdate=date(2000, 1, 1),
        )
    )
    db.commit()
    token, _, _ = create_user_session(db, member)
    return {"Authorization": f"Bearer {token}"}


class TestEnsureTzAwareNoOpOnAwareInput:
    """app/api/deps.py and app/services/auth_service.py each declare an
    _ensure_tz_aware guard used before comparing a DB-read datetime against
    a freshly generated datetime.now(UTC). Both must be no-ops once the DB
    itself returns aware values."""

    def test_deps_ensure_tz_aware_passes_through_aware_datetime(self):
        assert _deps_ensure_tz_aware(AWARE_DT) == AWARE_DT

    def test_auth_service_ensure_tz_aware_passes_through_aware_datetime(self):
        assert _auth_ensure_tz_aware(AWARE_DT) == AWARE_DT


class TestPublicGalleryOffsetSerialization:
    """GalleryImageAdminResponse.created_at is a plain, unwrapped datetime
    field with no validator — it relies entirely on the incoming value
    already being tz-aware to serialize with an offset."""

    def test_created_at_serializes_with_utc_offset(self):
        response = GalleryImageAdminResponse(
            id="12345678-1234-5678-1234-567812345678",
            url="https://example.com/img.jpg",
            sort_order=1,
            is_published=True,
            width=100,
            height=100,
            size=1000,
            created_at=AWARE_DT,
        )
        serialized = response.model_dump(mode="json")["created_at"]
        assert serialized == "2026-01-15T12:30:00Z"


class TestArchiveEndpointReturnsOffsetTimestamp:
    """End-to-end proof that a real API response carries a UTC offset for a
    timestamp read back from the migrated columns, via a real HTTP
    round-trip through TestClient against the actual Postgres-backed
    session — covers both the dir and file response shapes in
    app/schemas/archive.py."""

    def test_get_dir_created_at_has_utc_offset(self, client, db_session):
        headers = _login_admin(db_session)
        dir_ = ArchiveDir(name="tz-endpoint-test", created_at=datetime.now(UTC))
        db_session.add(dir_)
        db_session.commit()

        resp = client.get(f"/api/archive/dirs/{dir_.id}", headers=headers)

        assert resp.status_code == 200
        created_at = resp.json()["created_at"]
        assert created_at is not None
        assert created_at.endswith(("Z", "+00:00"))

    def test_get_file_created_at_has_utc_offset(self, client, db_session):
        headers = _login_admin(db_session)
        now = datetime.now(UTC)
        item = ArchiveStoreItem(
            name="tz-file",
            extension="jpg",
            mime_type="image/jpeg",
            size=1000,
            sha256_hash="b" * 64,
            created_at=now,
            updated_at=now,
        )
        db_session.add(item)
        db_session.flush()
        file_ = ArchiveFile(
            archive_dir_id=None,
            description="tz-endpoint-test",
            archive_store_item_id=item.id,
            created_at=now,
            updated_at=now,
        )
        db_session.add(file_)
        db_session.commit()

        resp = client.get(f"/api/archive/files/{file_.id}", headers=headers)

        assert resp.status_code == 200
        created_at = resp.json()["created_at"]
        assert created_at is not None
        assert created_at.endswith(("Z", "+00:00"))


class TestPublicGalleryEndpointReturnsOffsetTimestamp:
    """Same end-to-end proof for the public_gallery_admin router."""

    def test_list_images_created_at_has_utc_offset(self, client, db_session):
        headers = _login_admin(db_session)
        now = datetime.now(UTC)
        db_session.add(
            PublicGalleryImage(
                sha256_hash="a" * 64,
                extension="jpg",
                content_type="image/jpeg",
                size=1000,
                width=100,
                height=100,
                sort_order=1,
                created_at=now,
                updated_at=now,
            )
        )
        db_session.commit()

        resp = client.get("/api/public-gallery-admin/images", headers=headers)

        assert resp.status_code == 200
        images = resp.json()
        assert len(images) == 1
        created_at = images[0]["created_at"]
        assert created_at is not None
        assert created_at.endswith(("Z", "+00:00"))
