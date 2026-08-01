"""Tests for the permission-rules endpoint and rule consistency."""

import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

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
    calculate_permissions,
    get_dev_superuser_cn,
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

    def test_display_returns_all_rules(self, db_session):
        display = get_permission_rules_display(db_session)
        assert len(display) == len(PERMISSION_RULES)
        for entry in display:
            assert "permission" in entry
            assert "description" in entry
            assert "cns" in entry
            assert isinstance(entry["permission"], str)
            assert isinstance(entry["description"], str)
            assert len(entry["description"]) > 0
            assert isinstance(entry["cns"], list)

    def test_display_excludes_dev_superuser_bypass(self, db_session):
        """DEV_SUPERUSER_ID grants every permission via calculate_permissions()
        (see TestDevSuperuserGuard), but the permission-setup display must
        show who satisfies each rule's actual condition, not who has
        blanket dev access — regression test for that mixup. The dev
        superuser's CN is surfaced separately via get_dev_superuser_cn(),
        not mixed into any rule's cns (see TestDevSuperuserCn)."""
        hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        member = Member(
            id=997,
            email="dev-superuser@test.at",
            auth_password=hashed,
            auth_locked=False,
            vorname="Dev",
            nachname="Superuser",
            org_id=None,
            state_id=None,
        )
        db_session.add(member)
        db_session.commit()

        with patch("app.services.permission_service.DEV_SUPERUSER_ID", 997):
            assert sorted(calculate_permissions(member)) == sorted(ALL_PERMISSIONS)
            display = get_permission_rules_display(db_session)

        for entry in display:
            assert "Dev Superuser" not in entry["cns"]


class TestDevSuperuserCn:
    def test_returns_none_when_unset(self, db_session):
        """DEV_SUPERUSER_ID is 0 in the test environment by default (see
        conftest.py)."""
        assert get_dev_superuser_cn(db_session) is None

    def test_returns_cn_when_active(self, db_session):
        hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        member = Member(
            id=996,
            email="dev-superuser2@test.at",
            auth_password=hashed,
            auth_locked=False,
            vorname="Dev",
            nachname="Superuser",
            org_id=None,
            state_id=None,
        )
        db_session.add(member)
        db_session.commit()

        with patch("app.services.permission_service.DEV_SUPERUSER_ID", 996):
            assert get_dev_superuser_cn(db_session) == "Dev Superuser"

    def test_returns_none_when_member_missing(self, db_session):
        """DEV_SUPERUSER_ID pointing at a non-existent member (e.g. stale
        config) must not blow up — just omit the notice."""
        with patch("app.services.permission_service.DEV_SUPERUSER_ID", 424242):
            assert get_dev_superuser_cn(db_session) is None


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
        rules = data["rules"]
        assert len(rules) == len(PERMISSION_RULES)
        perm_names = {rule["permission"] for rule in rules}
        assert "systemAdmin" in perm_names
        assert "archiveAdmin" in perm_names

        by_permission = {rule["permission"]: rule for rule in rules}
        assert by_permission["archiveAdmin"]["cns"] == ["Admin User"]
        assert by_permission["standesdbContactAdmin"]["cns"] == []
        assert data["dev_superuser_cn"] is None

    def test_allowed_for_regular_member(
        self,
        db_session,
        client,
    ):
        """Transparency page, not an admin tool — any authenticated member
        may read it, not just systemAdmin (see docstring on the endpoint)."""
        _seed(db_session)
        headers = _regular_user(
            db_session,
            client,
        )
        resp = client.get(
            "/api/system/permission-rules",
            headers=headers,
        )
        assert resp.status_code == 200

    def test_requires_authentication(self, client):
        resp = client.get("/api/system/permission-rules")
        assert resp.status_code == 401

    def test_returns_dev_superuser_cn_when_active(
        self,
        db_session,
        client,
    ):
        _seed(db_session)
        headers = _admin_user(db_session, client)
        admin = db_session.query(Member).filter_by(email="admin@vbw.at").one()

        with patch("app.services.permission_service.DEV_SUPERUSER_ID", admin.id):
            resp = client.get("/api/system/permission-rules", headers=headers)

        assert resp.status_code == 200
        assert resp.json()["dev_superuser_cn"] == "Admin User"

    def test_query_count_does_not_scale_with_member_count(
        self,
        db_session,
        client,
        count_queries,
    ):
        """Regression test for _group_members_by_rule_condition(): grouping
        every member's rule matches must run as a single member query, not
        one query per member or per rule."""
        _seed(db_session)
        headers = _admin_user(db_session, client)

        with count_queries() as small:
            resp_small = client.get("/api/system/permission-rules", headers=headers)

        for i in range(10):
            hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
            db_session.add(
                Member(
                    email=f"extra{i}@vbw.at",
                    auth_password=hashed,
                    auth_locked=False,
                    vorname=f"Extra{i}",
                    nachname="Member",
                    org_id="vbw",
                    state_id="fu",
                )
            )
        db_session.commit()

        with count_queries() as large:
            resp_large = client.get("/api/system/permission-rules", headers=headers)

        assert resp_small.status_code == 200
        assert resp_large.status_code == 200
        assert large.count == small.count


class TestDevSuperuserGuard:
    def test_dev_superuser_active_in_development(self, db_session):
        """DEV_SUPERUSER_ID grants all permissions when active (development only —
        see TestDevSuperuserEnvironmentGating for the env-based derivation)."""
        hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        member = Member(
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

        with patch("app.services.permission_service.DEV_SUPERUSER_ID", 999):
            perms = calculate_permissions(member)

        assert sorted(perms) == sorted(ALL_PERMISSIONS)

    def test_dev_superuser_disabled_when_inactive(self, db_session):
        """DEV_SUPERUSER_ID forced to 0 (any non-development stage) — regular
        rules apply."""
        hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
        member = Member(
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

        with patch("app.services.permission_service.DEV_SUPERUSER_ID", 0):
            perms = calculate_permissions(member)

        # No roles → no permissions
        assert perms == []


class TestDevSuperuserEnvironmentGating:
    """DEV_SUPERUSER_ID is derived once at import time from APP_ENVIRONMENT
    (see app/services/permission_service.py) - active in development only,
    forced to 0 everywhere else. A fresh subprocess per case is required
    since the derivation only runs once at module import (mirrors
    tests/core/test_config.py's approach for the same reason)."""

    @staticmethod
    def _derived_value(app_environment: str, dev_superuser_id: str) -> str:
        env = {
            **os.environ,
            "APP_ENVIRONMENT": app_environment,
            "DEV_SUPERUSER_ID": dev_superuser_id,
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.services.permission_service import DEV_SUPERUSER_ID; "
                "print(DEV_SUPERUSER_ID)",
            ],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        return result.stdout.strip()

    def test_active_in_development(self) -> None:
        assert self._derived_value("development", "42") == "42"

    def test_disabled_in_test(self) -> None:
        assert self._derived_value("test", "42") == "0"

    def test_disabled_in_qa(self) -> None:
        assert self._derived_value("qa", "42") == "0"

    def test_disabled_in_production(self) -> None:
        assert self._derived_value("production", "42") == "0"
