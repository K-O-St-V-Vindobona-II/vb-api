"""Tests für GET /api/standesdb/keys — Schlüsselliste."""

from datetime import date
from typing import TYPE_CHECKING

import bcrypt

from app.models.key import Key
from app.models.member import Member
from app.models.member_key import MemberKey
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session

if TYPE_CHECKING:
    import uuid


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="fu", label="Fux", order=1),
        ]
    )
    db.commit()
    db.add_all(
        [
            Role(id="x", group="chc", label="Senior", order=0),
            Key(name="Bude"),
            Key(name="ChC"),
            Key(name="Post"),
        ]
    )
    db.commit()


def _key_id(db, name: str) -> uuid.UUID:
    """Look up a seeded key's id by its stable name - Final-Cutover means
    the seed rows' ids are no longer the predictable 1/2/3 an
    autoincrement sequence would assign."""
    return db.query(Key).filter_by(name=name).one().id


def _login(db, _client):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="user@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Test",
        nachname="User",
        org_id="vbw",
    )
    db.add(m)
    db.commit()
    db.add(
        MemberRole(
            member_id=m.id_uuid,
            role_id="x",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}, m


def _login_no_permission(db, _client):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="normal@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Normal",
        nachname="User",
        org_id="vbw",
    )
    db.add(m)
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}


class TestKeysListEndpoint:
    def test_requires_auth(self, client, db_session):
        resp = client.get("/api/standesdb/keys")
        assert resp.status_code == 401

    def test_requires_keylist_permission(self, client, db_session):
        _seed(db_session)
        headers = _login_no_permission(db_session, client)
        resp = client.get("/api/standesdb/keys", headers=headers)
        assert resp.status_code == 403

    def test_returns_key_names(self, client, db_session):
        _seed(db_session)
        headers, _ = _login(db_session, client)
        resp = client.get("/api/standesdb/keys", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "key_names" in data
        assert set(data["key_names"]) == {
            "Bude",
            "ChC",
            "Post",
        }

    def test_empty_when_no_keys_assigned(self, client, db_session):
        _seed(db_session)
        headers, _ = _login(db_session, client)
        resp = client.get("/api/standesdb/keys", headers=headers)
        assert resp.json()["members"] == []

    def test_returns_members_with_keys(self, client, db_session):
        _seed(db_session)
        headers, user = _login(db_session, client)

        db_session.add(
            MemberKey(member_id=user.id_uuid, key_id=_key_id(db_session, "Bude"))
        )
        db_session.add(
            MemberKey(member_id=user.id_uuid, key_id=_key_id(db_session, "ChC"))
        )
        db_session.commit()

        resp = client.get("/api/standesdb/keys", headers=headers)
        assert resp.status_code == 200
        members = resp.json()["members"]
        assert len(members) == 1
        assert members[0]["nachname"] == "User"
        assert members[0]["vorname"] == "Test"
        assert members[0]["keys"]["Bude"] is True
        assert members[0]["keys"]["ChC"] is True
        assert members[0]["keys"]["Post"] is False

    def test_sorted_by_nachname(self, client, db_session):
        _seed(db_session)
        headers, _ = _login(db_session, client)

        hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        m_alpha = Member(
            email="a@vbw.at",
            auth_password=hashed,
            auth_locked=True,
            vorname="Alpha",
            nachname="Amann",
            org_id="vbw",
        )
        m_zeta = Member(
            email="z@vbw.at",
            auth_password=hashed,
            auth_locked=True,
            vorname="Zeta",
            nachname="Zeller",
            org_id="vbw",
        )
        db_session.add_all([m_alpha, m_zeta])
        db_session.commit()

        db_session.add(
            MemberKey(member_id=m_zeta.id_uuid, key_id=_key_id(db_session, "Bude"))
        )
        db_session.add(
            MemberKey(member_id=m_alpha.id_uuid, key_id=_key_id(db_session, "ChC"))
        )
        db_session.commit()

        resp = client.get("/api/standesdb/keys", headers=headers)
        members = resp.json()["members"]
        assert len(members) == 2
        assert members[0]["nachname"] == "Amann"
        assert members[1]["nachname"] == "Zeller"

    def test_member_without_keys_excluded(self, client, db_session):
        _seed(db_session)
        headers, user = _login(db_session, client)

        hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        no_keys = Member(
            email="nokeys@vbw.at",
            auth_password=hashed,
            auth_locked=True,
            vorname="Ohne",
            nachname="Schlüssel",
            org_id="vbw",
        )
        db_session.add(no_keys)
        db_session.commit()

        db_session.add(
            MemberKey(member_id=user.id_uuid, key_id=_key_id(db_session, "Bude"))
        )
        db_session.commit()

        resp = client.get("/api/standesdb/keys", headers=headers)
        members = resp.json()["members"]
        assert len(members) == 1
        assert members[0]["id"] == user.id

    def test_query_count_does_not_scale_with_member_count(
        self, client, db_session, count_queries
    ):
        """Regression test for the N+1 fix in _build_keys_data(): iterating
        m.member_keys per member must not issue one query per member."""
        _seed(db_session)
        headers, user = _login(db_session, client)
        db_session.add(
            MemberKey(member_id=user.id_uuid, key_id=_key_id(db_session, "Bude"))
        )
        db_session.commit()

        with count_queries() as small:
            resp_small = client.get("/api/standesdb/keys", headers=headers)

        for i in range(5):
            hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
            m = Member(
                email=f"member{i}@vbw.at",
                auth_password=hashed,
                auth_locked=False,
                vorname=f"Member{i}",
                nachname=f"Test{i}",
                org_id="vbw",
            )
            db_session.add(m)
            db_session.commit()
            db_session.add(
                MemberKey(member_id=m.id_uuid, key_id=_key_id(db_session, "Bude"))
            )
            db_session.commit()

        with count_queries() as large:
            resp_large = client.get("/api/standesdb/keys", headers=headers)

        assert resp_small.status_code == 200
        assert resp_large.status_code == 200
        assert len(resp_large.json()["members"]) == 6
        assert large.count == small.count


class TestKeysDownloadEndpoint:
    def test_requires_auth(self, client, db_session):
        resp = client.get("/api/standesdb/keys/download")
        assert resp.status_code == 401

    def test_requires_keylist_permission(self, client, db_session):
        _seed(db_session)
        headers = _login_no_permission(db_session, client)
        resp = client.get(
            "/api/standesdb/keys/download",
            headers=headers,
        )
        assert resp.status_code == 403

    def test_download_content(self, client, db_session):
        _seed(db_session)
        headers, user = _login(db_session, client)

        db_session.add(
            MemberKey(member_id=user.id_uuid, key_id=_key_id(db_session, "Bude"))
        )
        db_session.add(
            MemberKey(member_id=user.id_uuid, key_id=_key_id(db_session, "Post"))
        )
        db_session.commit()

        resp = client.get(
            "/api/standesdb/keys/download",
            headers=headers,
        )
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert "attachment" in resp.headers["content-disposition"]
        assert "schluessel_" in resp.headers["content-disposition"]
        content = resp.content.decode("utf-8")
        assert "User, Test: Bude, Post" in content

    def test_download_empty(self, client, db_session):
        _seed(db_session)
        headers, _ = _login(db_session, client)

        resp = client.get(
            "/api/standesdb/keys/download",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.content.decode("utf-8") == ""
