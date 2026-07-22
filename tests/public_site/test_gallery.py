"""Tests for the public gallery: public read endpoint + admin CRUD."""

import io
import uuid
from datetime import date

import bcrypt
from PIL import Image as PILImage

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.public_gallery_image import PublicGalleryImage
from app.models.role import Role
from app.services.auth_service import create_user_session


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Role(
                id="internetreferent",
                group="funktion",
                label="Internetreferent",
                order=1,
            ),
        ]
    )
    db.commit()


def _admin(db):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="admin@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Admin",
        nachname="User",
        org_id="vbw",
    )
    db.add(m)
    db.commit()
    db.add(
        MemberRole(
            member_id=m.id,
            role_id="internetreferent",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    return m


def _nonadmin(db):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="user@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Normal",
        nachname="User",
        org_id="vbw",
    )
    db.add(m)
    db.commit()
    return m


def _headers(db, member):
    token, _, _ = create_user_session(db, member)
    return {"Authorization": f"Bearer {token}"}


def _make_jpeg(width=100, height=100):
    img = PILImage.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf


class TestPublicGalleryList:
    def test_empty_gallery(self, client, db_session):
        resp = client.get("/api/public/gallery")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_lists_published_images_in_order(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        r1 = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(80, 80), "image/jpeg")},
            data={"caption": "Erstes Bild"},
        )
        r2 = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("b.jpg", _make_jpeg(90, 90), "image/jpeg")},
            data={"caption": "Zweites Bild"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200

        resp = client.get("/api/public/gallery")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["caption"] == "Erstes Bild"
        assert data[1]["caption"] == "Zweites Bild"
        assert data[0]["url"].startswith("http")

    def test_unpublished_images_excluded(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        upload = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = upload.json()["id"]
        client.put(
            f"/api/public-gallery-admin/images/{img_id}",
            headers=headers,
            json={"caption": None, "is_published": False},
        )

        resp = client.get("/api/public/gallery")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_response_never_cacheable(self, client, db_session):
        resp = client.get("/api/public/gallery")
        assert resp.headers["Cache-Control"] == "no-store"


class TestAdminUpload:
    def test_upload_requires_permission(self, client, db_session):
        _seed(db_session)
        user = _nonadmin(db_session)
        headers = _headers(db_session, user)

        resp = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")},
        )
        assert resp.status_code == 403

    def test_upload_requires_authentication(self, client, db_session):
        resp = client.post(
            "/api/public-gallery-admin/images",
            files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")},
        )
        assert resp.status_code == 401

    def test_upload_success(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        resp = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")},
            data={"caption": "Testbild"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["caption"] == "Testbild"
        assert data["is_published"] is True
        assert data["sort_order"] == 1

        img = db_session.get(PublicGalleryImage, uuid.UUID(data["id"]))
        assert img is not None
        assert img.width == 100

    def test_upload_gif_rejected(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        buf = io.BytesIO(
            b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04"
            b"\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D"
            b"\x01\x00;"
        )
        resp = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.gif", buf, "image/gif")},
        )
        assert resp.status_code == 422

    def test_upload_too_large_rejected(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        large_buf = io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * (9 * 1024 * 1024))
        resp = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("big.jpg", large_buf, "image/jpeg")},
        )
        assert resp.status_code == 422

    def test_second_upload_gets_next_sort_order(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(80, 80), "image/jpeg")},
        )
        r2 = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("b.jpg", _make_jpeg(90, 90), "image/jpeg")},
        )
        assert r2.json()["sort_order"] == 2


class TestAdminList:
    def test_list_includes_unpublished(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        upload = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = upload.json()["id"]
        client.put(
            f"/api/public-gallery-admin/images/{img_id}",
            headers=headers,
            json={"caption": None, "is_published": False},
        )

        resp = client.get("/api/public-gallery-admin/images", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["is_published"] is False


class TestAdminUpdate:
    def test_update_caption_too_long_rejected(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        upload = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = upload.json()["id"]

        resp = client.put(
            f"/api/public-gallery-admin/images/{img_id}",
            headers=headers,
            json={"caption": "x" * 200, "is_published": True},
        )
        assert resp.status_code == 422

    def test_update_not_found(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        resp = client.put(
            "/api/public-gallery-admin/images/00000000-0000-0000-0000-000000000000",
            headers=headers,
            json={"caption": None, "is_published": True},
        )
        assert resp.status_code == 404


class TestAdminMove:
    def test_move_up_swaps_sort_order(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        r1 = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(80, 80), "image/jpeg")},
        )
        r2 = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("b.jpg", _make_jpeg(90, 90), "image/jpeg")},
        )
        id1, id2 = r1.json()["id"], r2.json()["id"]

        resp = client.post(
            f"/api/public-gallery-admin/images/{id2}/move",
            headers=headers,
            json={"direction": "up"},
        )
        assert resp.status_code == 200

        db_session.expire_all()
        assert db_session.get(PublicGalleryImage, uuid.UUID(id2)).sort_order == 1
        assert db_session.get(PublicGalleryImage, uuid.UUID(id1)).sort_order == 2

    def test_move_up_at_top_is_noop(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        r1 = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")},
        )
        id1 = r1.json()["id"]

        resp = client.post(
            f"/api/public-gallery-admin/images/{id1}/move",
            headers=headers,
            json={"direction": "up"},
        )
        assert resp.status_code == 200
        db_session.expire_all()
        assert db_session.get(PublicGalleryImage, uuid.UUID(id1)).sort_order == 1


class TestAdminDelete:
    def test_delete_removes_row(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        upload = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = upload.json()["id"]

        resp = client.delete(
            f"/api/public-gallery-admin/images/{img_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert db_session.get(PublicGalleryImage, uuid.UUID(img_id)) is None

    def test_reupload_after_delete_succeeds(self, client, db_session, mock_s3):
        # sha256_hash is unique per row - after a row is deleted, uploading
        # the exact same content again must succeed (not falsely collide).
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        same_content = _make_jpeg(123, 123).getvalue()
        r1 = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.jpg", io.BytesIO(same_content), "image/jpeg")},
        )
        img_id = r1.json()["id"]

        client.delete(
            f"/api/public-gallery-admin/images/{img_id}",
            headers=headers,
        )

        r2 = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("b.jpg", io.BytesIO(same_content), "image/jpeg")},
        )
        assert r2.status_code == 200


class TestAdminDuplicateUpload:
    def test_uploading_identical_content_twice_rejected(
        self, client, db_session, mock_s3
    ):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(db_session, admin)

        same_content = _make_jpeg(64, 64).getvalue()
        r1 = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("a.jpg", io.BytesIO(same_content), "image/jpeg")},
        )
        assert r1.status_code == 200

        r2 = client.post(
            "/api/public-gallery-admin/images",
            headers=headers,
            files={"file": ("b.jpg", io.BytesIO(same_content), "image/jpeg")},
        )
        assert r2.status_code == 422
