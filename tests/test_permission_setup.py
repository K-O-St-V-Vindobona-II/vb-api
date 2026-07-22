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
                group="funktion",
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


class TestDevSuperuserGuard:
    def test_dev_superuser_active_in_development(self, db_session):
        """DEV_SUPERUSER_ID grants all permissions in non-production environments."""
        import bcrypt

        from app.models.member import Member as MemberModel

        hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        member = MemberModel(
            id=999,
            email="dev@test.at",
            auth_password=hashed,
            auth_locked=False,
            vorname="Dev",
            nachname="Super",
            org_id=None,
            state_id=None,
        )
        db_session.add(member)
        db_session.commit()

        with (
            __import__("unittest.mock", fromlist=["patch"]).patch(
                "app.services.permission_service.DEV_SUPERUSER_ID", 999
            ),
            __import__("unittest.mock", fromlist=["patch"]).patch(
                "app.services.permission_service.APP_ENVIRONMENT", "development"
            ),
        ):
            from app.services.permission_service import (
                ALL_PERMISSIONS,
                calculate_permissions,
            )

            perms = calculate_permissions(member)

        assert sorted(perms) == sorted(ALL_PERMISSIONS)

    def test_dev_superuser_disabled_in_production(self, db_session):
        """DEV_SUPERUSER_ID is forced to 0 in production — regular rules apply."""
        import bcrypt

        from app.models.member import Member as MemberModel

        hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        member = MemberModel(
            id=888,
            email="prod@test.at",
            auth_password=hashed,
            auth_locked=False,
            vorname="Prod",
            nachname="User",
            org_id=None,
            state_id=None,
        )
        db_session.add(member)
        db_session.commit()

        with (
            __import__("unittest.mock", fromlist=["patch"]).patch(
                "app.services.permission_service.DEV_SUPERUSER_ID", 0
            ),
            __import__("unittest.mock", fromlist=["patch"]).patch(
                "app.services.permission_service.APP_ENVIRONMENT", "production"
            ),
        ):
            from app.services.permission_service import calculate_permissions

            perms = calculate_permissions(member)

        # No roles → no permissions
        assert perms == []
