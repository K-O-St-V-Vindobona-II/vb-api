"""Tests for the permission-rules endpoint and rule consistency."""

from datetime import date

import bcrypt

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import (
    create_user_session,
)
from app.services.permission_service import (
    ALL_PERMISSIONS,
    PERMISSION_RULES,
    get_permission_rules_display,
)


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            State(id="fu", label="Fux", order=1),
            Role(
                id="internetreferent",
                group="it",
                label="Internetreferent",
                order=1,
            ),
        ]
    )
    db.commit()


def _admin_user(db, _client):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="admin@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Admin",
        nachname="User",
        org_id="vbw",
        state_id="fu",
    )
    db.add(m)
    db.commit()
    db.add(
        MemberRole(
            member_id=m.id,
            role_id="internetreferent",
            startdate=date(2020, 1, 1),
        )
    )
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}


def _regular_user(db, _client):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="user@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Regular",
        nachname="User",
        org_id="vbw",
        state_id="fu",
    )
    db.add(m)
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}


class TestPermissionRulesConsistency:
    def test_all_permissions_covered(self):
        rule_perms = {rule.permission for rule in PERMISSION_RULES}
        assert rule_perms == set(ALL_PERMISSIONS)

    def test_display_returns_all_rules(self):
        display = get_permission_rules_display()
        assert len(display) == len(PERMISSION_RULES)
        for entry in display:
            assert "permission" in entry
            assert "description" in entry
            assert isinstance(entry["permission"], str)
            assert isinstance(entry["description"], str)
            assert len(entry["description"]) > 0


class TestPermissionRulesEndpoint:
    def test_returns_rules_for_admin(
        self,
        db_session,
        client,
    ):
        _seed(db_session)
        headers = _admin_user(
            db_session,
            client,
        )
        resp = client.get(
            "/api/system/permission-rules",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == len(PERMISSION_RULES)
        perm_names = {rule["permission"] for rule in data}
        assert "systemAdmin" in perm_names
        assert "archiveAdmin" in perm_names

    def test_forbidden_without_admin(
        self,
        db_session,
        client,
    ):
        _seed(db_session)
        headers = _regular_user(
            db_session,
            client,
        )
        resp = client.get(
            "/api/system/permission-rules",
            headers=headers,
        )
        assert resp.status_code == 403
