"""Tests for image gallery — upload, update, delete, permissions."""

import io
import uuid
from datetime import date

import bcrypt
import pytest
from PIL import Image as PILImage

from app.core.storage import S3_PATH_STANDESDB_CACHE, S3_PATH_STANDESDB_IMAGES
from app.models.contact import Contact
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.standesdb_image import StandesdbImage
from app.models.state import State
from app.services import image_service
from app.services.auth_service import create_user_session


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", sort_order=1),
            State(id="fu", label="Fux", sort_order=1),
            Role(id="standesfuehrer", group="chc", label="Standesführer", sort_order=1),
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


def _self_member(db, email="self@vbw.at"):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email=email,
        auth_password=hashed,
        auth_locked=False,
        vorname="Self",
        nachname="Service",
        org_id="vbw",
    )
    db.add(m)
    db.commit()
    return m


def _target_contact(db, name="Target Contact"):
    c = Contact(kontakttyp="person", name=name)
    db.add(c)
    db.commit()
    return c


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
        assert resp.status_code == 201
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
        assert resp.status_code == 201

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
        assert img.is_default is True

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
        assert img2.is_default is False


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
        assert db_session.get(StandesdbImage, id1).is_default is False
        assert db_session.get(StandesdbImage, id2).is_default is True


class TestList:
    def test_list_flags_the_default_image(self, client, db_session):
        _seed(db_session)
        admin = _admin(db_session)
        target = _target_member(db_session)
        headers = _headers(client, db_session, admin)
        url = f"/api/standesdb/members/{target.id}/images"
        first = client.post(
            url, headers=headers, files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")}
        ).json()["id"]
        second = client.post(
            url, headers=headers, files={"file": ("b.png", _make_png(), "image/png")}
        ).json()["id"]

        body = client.get(url, headers=headers).json()

        assert {item["id"]: item["default"] for item in body["images"]} == {
            first: True,
            second: False,
        }
        assert body["owner"]["default_image"] == first


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
        assert resp2.status_code == 204
        assert resp2.content == b""
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
            f"/api/standesdb/members/{uuid.uuid4()}/images/{uuid.uuid4()}/url",
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

    def test_db_session_still_usable_after_download(self, client, db_session):
        """get_image_for_serving() closes the request-scoped DB session
        before the S3 round-trip (same pattern/incident as
        archive_service.get_presigned_url()). The session must still be
        safely reusable afterwards."""
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
        assert db_session.get(StandesdbImage, img_id) is not None

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
        assert db_session.get(StandesdbImage, img_id) is not None

    def test_presigned_url_unauthenticated(self, client, db_session):
        resp = client.get(
            f"/api/standesdb/members/1/images/{uuid.uuid4()}/url",
        )
        assert resp.status_code == 401


class TestSelfService:
    def test_self_can_list_own_images_without_admin_permission(
        self, client, db_session
    ):
        _seed(db_session)
        member = _self_member(db_session)
        headers = _headers(client, db_session, member)

        client.post(
            "/api/standesdb/members/me/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )

        resp = client.get("/api/standesdb/members/me/images", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["owner"]["id"] == str(member.id)
        assert len(data["images"]) == 1

    def test_self_list_own_images_unauthenticated_rejected(self, client, db_session):
        _seed(db_session)
        resp = client.get("/api/standesdb/members/me/images")
        assert resp.status_code == 401

    def test_self_can_upload_own_image_without_admin_permission(
        self, client, db_session
    ):
        _seed(db_session)
        member = _self_member(db_session)
        headers = _headers(client, db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
            data={"description": "Mein Bild"},
        )
        assert resp.status_code == 201
        img = (
            db_session.query(StandesdbImage)
            .filter_by(owner_member_id=member.id)
            .first()
        )
        assert img is not None
        assert img.description == "Mein Bild"

    def test_self_upload_first_image_becomes_default(self, client, db_session):
        _seed(db_session)
        member = _self_member(db_session)
        headers = _headers(client, db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img = db_session.get(StandesdbImage, resp.json()["id"])
        assert img.is_default is True

    def test_self_can_update_own_image_description(self, client, db_session):
        _seed(db_session)
        member = _self_member(db_session)
        headers = _headers(client, db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]

        resp2 = client.put(
            f"/api/standesdb/members/me/images/{img_id}",
            headers=headers,
            json={"description": "Neu", "default": False},
        )
        assert resp2.status_code == 200
        db_session.expire_all()
        assert db_session.get(StandesdbImage, img_id).description == "Neu"

    def test_self_can_set_own_image_default(self, client, db_session):
        _seed(db_session)
        member = _self_member(db_session)
        headers = _headers(client, db_session, member)

        r1 = client.post(
            "/api/standesdb/members/me/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(80, 80), "image/jpeg")},
        )
        r2 = client.post(
            "/api/standesdb/members/me/images",
            headers=headers,
            files={"file": ("b.jpg", _make_jpeg(90, 90), "image/jpeg")},
        )
        id1, id2 = r1.json()["id"], r2.json()["id"]

        client.put(
            f"/api/standesdb/members/me/images/{id2}",
            headers=headers,
            json={"description": None, "default": True},
        )
        db_session.expire_all()
        assert db_session.get(StandesdbImage, id1).is_default is False
        assert db_session.get(StandesdbImage, id2).is_default is True

    def test_self_can_delete_own_image(self, client, db_session):
        _seed(db_session)
        member = _self_member(db_session)
        headers = _headers(client, db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]

        resp2 = client.delete(
            f"/api/standesdb/members/me/images/{img_id}", headers=headers
        )
        assert resp2.status_code == 204
        db_session.expire_all()
        assert db_session.get(StandesdbImage, img_id).deleted_at is not None

    def test_self_upload_has_no_count_limit(self, client, db_session):
        """Mirrors admin-upload behavior: no cap on self-service image count."""
        _seed(db_session)
        member = _self_member(db_session)
        headers = _headers(client, db_session, member)

        for i in range(6):
            resp = client.post(
                "/api/standesdb/members/me/images",
                headers=headers,
                files={"file": (f"{i}.jpg", _make_jpeg(80 + i, 80 + i), "image/jpeg")},
            )
            assert resp.status_code == 201

        count = (
            db_session.query(StandesdbImage)
            .filter_by(owner_member_id=member.id, deleted_at=None)
            .count()
        )
        assert count == 6

    def test_self_cannot_update_another_members_image_via_me_route(
        self, client, db_session
    ):
        _seed(db_session)
        owner = _self_member(db_session, email="owner@vbw.at")
        other = _self_member(db_session, email="other@vbw.at")
        owner_headers = _headers(client, db_session, owner)
        other_headers = _headers(client, db_session, other)

        resp = client.post(
            "/api/standesdb/members/me/images",
            headers=owner_headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]

        resp2 = client.put(
            f"/api/standesdb/members/me/images/{img_id}",
            headers=other_headers,
            json={"description": "Hacked", "default": False},
        )
        assert resp2.status_code == 404

    def test_self_cannot_delete_another_members_image_via_me_route(
        self, client, db_session
    ):
        _seed(db_session)
        owner = _self_member(db_session, email="owner2@vbw.at")
        other = _self_member(db_session, email="other2@vbw.at")
        owner_headers = _headers(client, db_session, owner)
        other_headers = _headers(client, db_session, other)

        resp = client.post(
            "/api/standesdb/members/me/images",
            headers=owner_headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]

        resp2 = client.delete(
            f"/api/standesdb/members/me/images/{img_id}", headers=other_headers
        )
        assert resp2.status_code == 404
        db_session.expire_all()
        assert db_session.get(StandesdbImage, img_id).deleted_at is None

    def test_self_upload_unauthenticated_rejected(self, client, db_session):
        _seed(db_session)
        resp = client.post(
            "/api/standesdb/members/me/images",
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        assert resp.status_code == 401

    def test_me_route_registration_order_does_not_422(self, client, db_session):
        """Regression guard for the routing-order gotcha: /members/me/images
        must not be swallowed by /members/{member_id}/images and fail
        int-conversion with a 422."""
        _seed(db_session)
        member = _self_member(db_session)
        headers = _headers(client, db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        assert resp.status_code == 201

    def test_self_upload_notifies_org_admin_by_email(
        self, client, db_session, mock_arq_pool
    ):
        _seed(db_session)
        _admin(db_session)  # org admin recipient
        member = _self_member(db_session)
        headers = _headers(client, db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        assert resp.status_code == 201
        mock_arq_pool.enqueue_job.assert_called_once()
        args = mock_arq_pool.enqueue_job.call_args[0]
        assert args[0] == "task_send_own_image_changed_email"
        assert "admin@vbw.at" in args[1]
        assert args[3] == "upload"

    def test_self_update_notifies_org_admin_by_email(
        self, client, db_session, mock_arq_pool
    ):
        _seed(db_session)
        _admin(db_session)
        member = _self_member(db_session)
        headers = _headers(client, db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]
        mock_arq_pool.enqueue_job.reset_mock()

        client.put(
            f"/api/standesdb/members/me/images/{img_id}",
            headers=headers,
            json={"description": "x", "default": False},
        )
        mock_arq_pool.enqueue_job.assert_called_once()
        assert mock_arq_pool.enqueue_job.call_args[0][3] == "update"

    def test_self_delete_notifies_org_admin_by_email(
        self, client, db_session, mock_arq_pool
    ):
        _seed(db_session)
        _admin(db_session)
        member = _self_member(db_session)
        headers = _headers(client, db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        img_id = resp.json()["id"]
        mock_arq_pool.enqueue_job.reset_mock()

        client.delete(f"/api/standesdb/members/me/images/{img_id}", headers=headers)
        mock_arq_pool.enqueue_job.assert_called_once()
        assert mock_arq_pool.enqueue_job.call_args[0][3] == "delete"

    def test_self_upload_no_email_when_member_has_no_org(
        self, client, db_session, mock_arq_pool
    ):
        _seed(db_session)
        hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        member = Member(
            email="noorg@vbw.at",
            auth_password=hashed,
            auth_locked=False,
            vorname="No",
            nachname="Org",
            org_id=None,
        )
        db_session.add(member)
        db_session.commit()
        headers = _headers(client, db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/images",
            headers=headers,
            files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
        )
        assert resp.status_code == 201
        mock_arq_pool.enqueue_job.assert_not_called()


def _upload_contact_image(client, headers, contact, *, png=False):
    name, maker, mime = (
        ("b.png", _make_png, "image/png")
        if png
        else ("a.jpg", _make_jpeg, "image/jpeg")
    )
    resp = client.post(
        f"/api/standesdb/contacts/{contact.id}/images",
        headers=headers,
        files={"file": (name, maker(), mime)},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


class TestContactImages:
    """The same image routes for contacts: readable by any signed-in member,
    changed only with the contact administration permission."""

    def test_the_first_upload_becomes_the_default_and_both_are_listed(
        self, client, db_session
    ):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))
        contact = _target_contact(db_session)
        first = _upload_contact_image(client, headers, contact)
        second = _upload_contact_image(client, headers, contact, png=True)

        body = client.get(
            f"/api/standesdb/contacts/{contact.id}/images", headers=headers
        ).json()

        assert {item["id"]: item["default"] for item in body["images"]} == {
            first: True,
            second: False,
        }
        assert body["owner"]["type"] == "contact"
        assert body["owner"]["default_image"] == first

    def test_download_returns_the_stored_file_under_a_contact_file_name(
        self, client, db_session
    ):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))
        contact = _target_contact(db_session)
        image_id = _upload_contact_image(client, headers, contact)

        resp = client.get(
            f"/api/standesdb/contacts/{contact.id}/images/{image_id}/download",
            headers=headers,
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"
        assert "contact_" in resp.headers["content-disposition"]
        assert resp.content.startswith(b"\xff\xd8")

    def test_the_original_and_the_thumbnail_get_different_presigned_urls(
        self, client, db_session
    ):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))
        contact = _target_contact(db_session)
        image_id = _upload_contact_image(client, headers, contact)
        base = f"/api/standesdb/contacts/{contact.id}/images/{image_id}/url"

        original = client.get(base, headers=headers)
        thumb = client.get(base + "?thumb=true", headers=headers)

        assert original.status_code == thumb.status_code == 200
        assert original.json()["url"].startswith("http")
        assert thumb.json()["url"] != original.json()["url"]

    def test_an_image_is_only_reachable_through_its_own_contact(
        self, client, db_session
    ):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))
        owner = _target_contact(db_session, "Owner")
        other = _target_contact(db_session, "Other")
        image_id = _upload_contact_image(client, headers, owner)
        base = f"/api/standesdb/contacts/{other.id}/images/{image_id}"

        responses = [
            client.get(base + "/download", headers=headers),
            client.get(base + "/url", headers=headers),
            client.put(base, headers=headers, json={"description": "x"}),
            client.delete(base, headers=headers),
        ]

        assert [r.status_code for r in responses] == [404, 404, 404, 404]
        assert db_session.get(StandesdbImage, image_id).deleted_at is None

    def test_uploading_for_an_unknown_contact_is_not_found(self, client, db_session):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))

        resp = client.post(
            f"/api/standesdb/contacts/{uuid.uuid4()}/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")},
        )

        assert resp.status_code == 404

    def test_update_sets_the_description_and_moves_the_default(
        self, client, db_session
    ):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))
        contact = _target_contact(db_session)
        first = _upload_contact_image(client, headers, contact)
        second = _upload_contact_image(client, headers, contact, png=True)
        bystander = _upload_contact_image(
            client, headers, _target_contact(db_session, "Bystander")
        )

        resp = client.put(
            f"/api/standesdb/contacts/{contact.id}/images/{second}",
            headers=headers,
            json={"description": "Neues Foto", "default": True},
        )

        assert resp.status_code == 200
        db_session.expire_all()
        assert db_session.get(StandesdbImage, first).is_default is False
        moved = db_session.get(StandesdbImage, second)
        assert (moved.is_default, moved.description) == (True, "Neues Foto")
        assert db_session.get(StandesdbImage, bystander).is_default is True

    def test_delete_hides_the_image_and_its_download(self, client, db_session):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))
        contact = _target_contact(db_session)
        image_id = _upload_contact_image(client, headers, contact)
        base = f"/api/standesdb/contacts/{contact.id}/images"

        deleted = client.delete(f"{base}/{image_id}", headers=headers)

        assert deleted.status_code == 204
        db_session.expire_all()
        assert db_session.get(StandesdbImage, image_id).deleted_at is not None
        assert client.get(base, headers=headers).json()["images"] == []
        assert (
            client.get(f"{base}/{image_id}/download", headers=headers).status_code
            == 404
        )

    def test_any_member_may_read_but_only_the_administration_may_change(
        self, client, db_session
    ):
        _seed(db_session)
        admin_headers = _headers(client, db_session, _admin(db_session))
        member_headers = _headers(client, db_session, _nonadmin(db_session))
        contact = _target_contact(db_session)
        image_id = _upload_contact_image(client, admin_headers, contact)
        base = f"/api/standesdb/contacts/{contact.id}/images"

        reads = [
            client.get(base, headers=member_headers),
            client.get(f"{base}/{image_id}/download", headers=member_headers),
            client.get(f"{base}/{image_id}/url", headers=member_headers),
        ]
        writes = [
            client.post(
                base,
                headers=member_headers,
                files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")},
            ),
            client.put(
                f"{base}/{image_id}", headers=member_headers, json={"default": True}
            ),
            client.delete(f"{base}/{image_id}", headers=member_headers),
        ]

        assert [r.status_code for r in reads] == [200, 200, 200]
        assert [r.status_code for r in writes] == [403, 403, 403]
        assert db_session.get(StandesdbImage, image_id).deleted_at is None

    def test_every_contact_image_route_requires_a_login(self, client, db_session):
        contact_id, image_id = uuid.uuid4(), uuid.uuid4()
        base = f"/api/standesdb/contacts/{contact_id}/images"

        statuses = [
            client.get(base).status_code,
            client.get(f"{base}/{image_id}/download").status_code,
            client.get(f"{base}/{image_id}/url").status_code,
            client.post(
                base, files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")}
            ).status_code,
            client.put(f"{base}/{image_id}", json={"default": True}).status_code,
            client.delete(f"{base}/{image_id}").status_code,
        ]

        assert statuses == [401] * 6


class TestStorageFailures:
    """What the image routes answer when the object store no longer holds the
    file, or holds something that is not an image."""

    @staticmethod
    def _drop_cached_thumbnails(mock_s3):
        """The upload already writes a thumbnail into the cache, which would
        answer a thumbnail request without touching the original."""
        for key in mock_s3.list_keys(S3_PATH_STANDESDB_CACHE):
            mock_s3.delete(key)

    @staticmethod
    def _stored_original_key(db_session, image_id):
        db_session.expire_all()
        image = db_session.get(StandesdbImage, image_id)
        return f"{S3_PATH_STANDESDB_IMAGES}/{image.sha256_hash}"

    def test_a_missing_original_is_not_found_for_download_and_thumbnail(
        self, client, db_session, mock_s3
    ):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))
        target = _target_member(db_session)
        image_id = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")},
        ).json()["id"]
        mock_s3.delete(self._stored_original_key(db_session, image_id))
        self._drop_cached_thumbnails(mock_s3)
        base = f"/api/standesdb/members/{target.id}/images/{image_id}"

        download = client.get(base + "/download", headers=headers)
        thumbnail = client.get(base + "/url?thumb=true", headers=headers)

        assert download.status_code == 404
        assert thumbnail.status_code == 404
        assert download.json()["detail"] == "Originaldatei nicht gefunden."

    def test_an_original_that_is_not_an_image_gets_no_thumbnail(
        self, client, db_session, mock_s3
    ):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))
        target = _target_member(db_session)
        image_id = client.post(
            f"/api/standesdb/members/{target.id}/images",
            headers=headers,
            files={"file": ("a.jpg", _make_jpeg(), "image/jpeg")},
        ).json()["id"]
        mock_s3.upload(
            self._stored_original_key(db_session, image_id),
            b"this is not an image",
            "image/jpeg",
        )
        self._drop_cached_thumbnails(mock_s3)

        resp = client.get(
            f"/api/standesdb/members/{target.id}/images/{image_id}/url?thumb=true",
            headers=headers,
        )

        assert resp.status_code == 200
        assert mock_s3.list_keys(S3_PATH_STANDESDB_CACHE) == []

    def test_an_unknown_owner_type_is_refused(self, db_session):
        with pytest.raises(ValueError, match="Unbekannter owner_type: 'robot'"):
            image_service.get_images_for_owner(db_session, "robot", uuid.uuid4())
