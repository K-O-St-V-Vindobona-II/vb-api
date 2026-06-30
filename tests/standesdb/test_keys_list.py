"""Tests für GET /api/standesdb/keys — Schlüsselliste."""

from datetime import date

import bcrypt

from app.models.key import Key
from app.models.member import Member
from app.models.member_key import MemberKey
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session


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
            Key(id=1, name="Bude"),
            Key(id=2, name="ChC"),
            Key(id=3, name="Post"),
        ]
    )
    db.commit()


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
            member_id=m.id,
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

        db_session.add(MemberKey(member_id=user.id, key_id=1))
        db_session.add(MemberKey(member_id=user.id, key_id=2))
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

        db_session.add(MemberKey(member_id=m_zeta.id, key_id=1))
        db_session.add(MemberKey(member_id=m_alpha.id, key_id=2))
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

        db_session.add(MemberKey(member_id=user.id, key_id=1))
        db_session.commit()

        resp = client.get("/api/standesdb/keys", headers=headers)
        members = resp.json()["members"]
        assert len(members) == 1
        assert members[0]["id"] == user.id


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

        db_session.add(MemberKey(member_id=user.id, key_id=1))
        db_session.add(MemberKey(member_id=user.id, key_id=3))
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
