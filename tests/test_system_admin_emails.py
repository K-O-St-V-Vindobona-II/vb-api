"""systemAdmin through a break-glass address: the address comes from the
server configuration, not from the code, and only counts once its owner has
proved control of the mailbox."""

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services import permission_service
from app.services.auth_service import create_user_session
from app.services.permission_service import (
    PERMISSION_RULES,
    calculate_permissions,
    get_emails_with_permission,
    get_permission_rules_display,
)
from tests.standesdb.test_endpoints import _member_payload

BREAK_GLASS = "break.glass@example.org"
SYSTEM_ADMIN = "systemAdmin"


def _member(db, email: str | None, *, verified: bool = True, **fields) -> Member:
    member = Member(
        email=email,
        email_verified_at=datetime.now(UTC) if verified else None,
        auth_locked=False,
        **fields,
    )
    db.add(member)
    db.commit()
    return member


def _seed_roles(db):
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
            Role(id="standesfuehrer", group="chc", label="Standesführer", order=2),
        ]
    )
    db.commit()


def _give_role(db, member: Member, role_id: str):
    db.add(
        MemberRole(
            member_id=member.id,
            role_id=role_id,
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()


class TestSettingValue:
    def test_it_is_empty_by_default(self, monkeypatch):
        monkeypatch.delenv("SYSTEM_ADMIN_EMAILS", raising=False)

        assert get_settings().system_admin_email_set == frozenset()

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("a@example.org", {"a@example.org"}),
            (" a@example.org , b@example.org ", {"a@example.org", "b@example.org"}),
            ("a@example.org,,  ,b@example.org,", {"a@example.org", "b@example.org"}),
            ("", set()),
            (" , ", set()),
            ("A@Example.org", {"A@Example.org"}),
        ],
    )
    def test_it_is_a_comma_separated_list(self, monkeypatch, raw, expected):
        monkeypatch.setenv("SYSTEM_ADMIN_EMAILS", raw)

        assert get_settings().system_admin_email_set == frozenset(expected)


class TestBreakGlassAddress:
    def test_a_verified_configured_address_grants_system_admin(
        self, db_session, monkeypatch
    ):
        monkeypatch.setenv("SYSTEM_ADMIN_EMAILS", BREAK_GLASS)
        member = _member(db_session, BREAK_GLASS)

        assert SYSTEM_ADMIN in calculate_permissions(member)

    def test_an_unverified_address_grants_nothing(self, db_session, monkeypatch):
        monkeypatch.setenv("SYSTEM_ADMIN_EMAILS", BREAK_GLASS)
        member = _member(db_session, BREAK_GLASS, verified=False)

        assert SYSTEM_ADMIN not in calculate_permissions(member)

    def test_another_address_grants_nothing(self, db_session, monkeypatch):
        monkeypatch.setenv("SYSTEM_ADMIN_EMAILS", BREAK_GLASS)
        member = _member(db_session, "someone.else@example.org")

        assert SYSTEM_ADMIN not in calculate_permissions(member)

    def test_the_address_is_matched_exactly_including_case(
        self, db_session, monkeypatch
    ):
        monkeypatch.setenv("SYSTEM_ADMIN_EMAILS", BREAK_GLASS)
        member = _member(db_session, BREAK_GLASS.upper())

        assert SYSTEM_ADMIN not in calculate_permissions(member)

    def test_without_the_setting_no_address_grants_anything(
        self, db_session, monkeypatch
    ):
        monkeypatch.delenv("SYSTEM_ADMIN_EMAILS", raising=False)
        member = _member(db_session, BREAK_GLASS)

        assert SYSTEM_ADMIN not in calculate_permissions(member)

    def test_a_member_without_an_address_is_not_matched(self, db_session, monkeypatch):
        monkeypatch.setenv("SYSTEM_ADMIN_EMAILS", BREAK_GLASS)
        member = _member(db_session, None)

        assert SYSTEM_ADMIN not in calculate_permissions(member)

    def test_the_role_still_grants_system_admin(self, db_session, monkeypatch):
        monkeypatch.delenv("SYSTEM_ADMIN_EMAILS", raising=False)
        _seed_roles(db_session)
        member = _member(db_session, "ref@example.org", org_id="vbw")
        _give_role(db_session, member, "internetreferent")

        assert SYSTEM_ADMIN in calculate_permissions(member)

    def test_the_role_needs_no_verified_address(self, db_session, monkeypatch):
        monkeypatch.delenv("SYSTEM_ADMIN_EMAILS", raising=False)
        _seed_roles(db_session)
        member = _member(db_session, "ref@example.org", verified=False, org_id="vbw")
        _give_role(db_session, member, "internetreferent")

        assert SYSTEM_ADMIN in calculate_permissions(member)

    def test_the_list_of_holders_follows_the_same_rule(self, db_session, monkeypatch):
        monkeypatch.setenv(
            "SYSTEM_ADMIN_EMAILS", f"{BREAK_GLASS},unverified@example.org"
        )
        _member(db_session, BREAK_GLASS)
        _member(db_session, "unverified@example.org", verified=False)

        assert get_emails_with_permission(db_session, SYSTEM_ADMIN) == [BREAK_GLASS]


class TestRuleOverview:
    def test_only_a_verified_address_is_listed_as_holder(self, db_session, monkeypatch):
        monkeypatch.setenv(
            "SYSTEM_ADMIN_EMAILS", f"{BREAK_GLASS},unverified@example.org"
        )
        _member(db_session, BREAK_GLASS, vorname="Verified", nachname="Holder")
        _member(
            db_session,
            "unverified@example.org",
            verified=False,
            vorname="Unverified",
            nachname="Holder",
        )

        rules = {r["permission"]: r for r in get_permission_rules_display(db_session)}

        assert rules[SYSTEM_ADMIN]["cns"] == ["Verified Holder"]

    def test_the_overview_does_not_name_an_address(self, db_session, monkeypatch):
        monkeypatch.setenv("SYSTEM_ADMIN_EMAILS", BREAK_GLASS)

        rules = get_permission_rules_display(db_session)

        assert [r for r in rules if "@" in r["description"]] == []


class TestCodeNamesNobody:
    def test_no_rule_description_names_an_address(self):
        described = [r for r in PERMISSION_RULES if "@" in r.description]

        assert described == []

    def test_the_rule_source_holds_no_address_literal(self):
        source = Path(permission_service.__file__).read_text()

        assert re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", source) == []

    def test_the_description_points_to_the_server_configuration(self):
        rule = next(r for r in PERMISSION_RULES if r.permission == SYSTEM_ADMIN)

        assert "Serverkonfiguration" in rule.description


class TestAddressIsNotAShortcut:
    """An administrator who can edit member records must not be able to take
    over the break-glass address by typing it into their own record."""

    def test_moving_the_address_to_another_record_does_not_move_the_power(
        self, client, db_session, monkeypatch
    ):
        monkeypatch.setenv("SYSTEM_ADMIN_EMAILS", BREAK_GLASS)
        _seed_roles(db_session)
        holder = _member(
            db_session, BREAK_GLASS, org_id="vbw", vorname="H", nachname="A"
        )
        admin = _member(
            db_session, "admin@vbw.at", org_id="vbw", vorname="S", nachname="F"
        )
        _give_role(db_session, admin, "standesfuehrer")
        token, _, _ = create_user_session(db_session, admin)
        headers = {"Authorization": f"Bearer {token}"}

        # 1. the holder's address is moved away, 2. the admin takes it over
        moved = client.put(
            f"/api/standesdb/members/{holder.id}",
            json=_member_payload(
                email="moved.away@example.org", vorname="H", nachname="A"
            ),
            headers=headers,
        )
        taken = client.put(
            f"/api/standesdb/members/{admin.id}",
            json=_member_payload(email=BREAK_GLASS, vorname="S", nachname="F"),
            headers=headers,
        )
        db_session.expire_all()

        assert (moved.status_code, taken.status_code) == (200, 200)
        assert admin.email == BREAK_GLASS
        assert admin.email_verified_at is None
        assert SYSTEM_ADMIN not in calculate_permissions(admin)
