"""Tests for image gallery — upload, update, delete, permissions."""

import io
from datetime import date

import bcrypt
from PIL import Image as PILImage

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.standesdb_image import StandesdbImage
from app.models.state import State
from app.services.auth_service import create_user_session


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            State(id="fu", label="Fux", order=1),
            Role(id="standesfuehrer", group="chc", label="Standesführer", order=1),
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
            role_id="standesfuehrer",
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


def _target_member(db):
    m = Member(
        email="target@vbw.at",
        org_id="vbw",
        vorname="Target",
        nachname="Member",
    )
    db.add(m)
    db.commit()
    return m


def _headers(_client, db, member):
    token, _, _ = create_user_session(db, member)
    return {"Authorization": f"Bearer {token}"}


def _make_jpeg(width=100, height=100):
    img = PILImage.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf


def _make_png(width=50, height=50):
    img = PILImage.new("RGBA", (width, height), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


class TestUpload:
    def test_upload_jpeg_success(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
            data={"description": "Testbild"},
        )
        assert resp.status_code == 200
        assert "id" in resp.json()

        img = (
            db_session.query(StandesdbImage)
            .filter_by(owner_member_id=target.id)
            .first()
        )
        assert img is not None
        assert img.description == "Testbild"
        assert img.extension == "jpg"
        assert img.width == 100
        assert img.height == 100

    def test_upload_png_success(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("test.png", _make_png(), "image/png")},
        )
        assert resp.status_code == 200

    def test_upload_gif_rejected(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        buf = io.BytesIO(
            b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        )
        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("test.gif", buf, "image/gif")},
        )
        assert resp.status_code == 422
        assert "JPEG" in resp.json()["detail"] or "PNG" in resp.json()["detail"]

    def test_upload_too_large_rejected(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        large_buf = io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * (6 * 1024 * 1024))
        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("big.jpg", large_buf, "image/jpeg")},
        )
        assert resp.status_code == 422

    def test_first_image_becomes_default(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]
        img = db_session.get(StandesdbImage, img_id)
        assert img.default is True

    def test_second_image_not_default(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(80, 80), "image/jpeg")},
        )
        resp2 = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("b.jpg", _make_jpeg(90, 90), "image/jpeg")},
        )
        img2 = db_session.get(StandesdbImage, resp2.json()["id"])
        assert img2.default is False


class TestUpdate:
    def test_update_description(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]

        resp2 = client.put(
            f"/api/standesdb/members/{target.id}/images/{img_id}",
            headers=headers,
            json={"description": "Neuer Text", "default": False},
        )
        assert resp2.status_code == 200
        db_session.expire_all()
        img = db_session.get(StandesdbImage, img_id)
        assert img.description == "Neuer Text"

    def test_set_default(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        r1 = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(80, 80), "image/jpeg")},
        )
        r2 = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("b.jpg", _make_jpeg(90, 90), "image/jpeg")},
        )

        id1 = r1.json()["id"]
        id2 = r2.json()["id"]

        client.put(
            f"/api/standesdb/members/{target.id}/images/{id2}",
            headers=headers,
            json={"description": None, "default": True},
        )
        db_session.expire_all()
        assert db_session.get(StandesdbImage, id1).default is False
        assert db_session.get(StandesdbImage, id2).default is True


class TestDelete:
    def test_soft_delete(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]

        resp2 = client.delete(
            f"/api/standesdb/members/{target.id}/images/{img_id}",
            headers=headers,
        )
        assert resp2.status_code == 200
        db_session.expire_all()
        img = db_session.get(StandesdbImage, img_id)
        assert img.deleted_at is not None

    def test_deleted_not_in_list(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]

        client.delete(
            f"/api/standesdb/members/{target.id}/images/{img_id}",
            headers=headers,
        )

        list_resp = client.get(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
        )
        assert len(list_resp.json()["images"]) == 0


class TestPermissions:
    def test_nonadmin_cannot_upload(self, client, db_session):
        _seed(db_session)
        _admin(db_session)
        user = _nonadmin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, user)

        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        assert resp.status_code == 403

    def test_nonadmin_cannot_delete(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        user = _nonadmin(db_session)
        target = _target_member(db_session)
        admin_headers = _headers(client, db_session, admin)
        user_headers = _headers(client, db_session, user)

        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=admin_headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]

        resp2 = client.delete(
            f"/api/standesdb/members/{target.id}/images/{img_id}",
            headers=user_headers,
        )
        assert resp2.status_code == 403


class TestPresignedUrl:
    def test_presigned_url_returns_url(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]

        url_resp = client.get(
            f"/api/standesdb/members/{target.id}/images/{img_id}/url",
            headers=headers,
        )
        assert url_resp.status_code == 200
        assert "url" in url_resp.json()
        assert url_resp.json()["url"].startswith("http")

    def test_presigned_url_thumb(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]

        url_resp = client.get(
            f"/api/standesdb/members/{target.id}/images/{img_id}/url?thumb=true",
            headers=headers,
        )
        assert url_resp.status_code == 200
        assert "url" in url_resp.json()

    def test_presigned_url_not_found(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)

        url_resp = client.get(
            "/api/standesdb/members/999/images/999/url",
            headers=headers,
        )
        assert url_resp.status_code == 404

    def test_download_still_works(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]

        dl_resp = client.get(
            f"/api/standesdb/members/{target.id}/images/{img_id}/download",
            headers=headers,
        )
        assert dl_resp.status_code == 200
        assert dl_resp.headers["content-type"] == "image/jpeg"

    def test_presigned_url_png_thumb(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)

        resp = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("test.png", _make_png(), "image/png")},
        )
        img_id = resp.json()["id"]

        url_resp = client.get(
            f"/api/standesdb/members/{target.id}/images/{img_id}/url?thumb=true",
            headers=headers,
        )
        assert url_resp.status_code == 200
        assert "url" in url_resp.json()

    def test_presigned_url_unauthenticated(self, client, db_session):
        resp = client.get(
            "/api/standesdb/members/1/images/1/url",
        )
        assert resp.status_code == 401
