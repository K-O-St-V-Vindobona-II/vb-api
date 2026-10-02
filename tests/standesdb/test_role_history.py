"""Every grant, revocation and period change of a member's role leaves one
append-only history entry with the member who made it."""

from datetime import date

import bcrypt
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.enums import RoleAssignmentAction
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.role import Role
from app.models.role_assignment_event import RoleAssignmentEvent
from app.services.role_history_service import RoleEntry, diff_role_entries
from tests.standesdb.test_api import (
    _auth_headers,
    _create_admin,
    _setup_reference_data,
)

GRANTED = RoleAssignmentAction.GRANTED
REVOKED = RoleAssignmentAction.REVOKED
PERIOD_CHANGED = RoleAssignmentAction.PERIOD_CHANGED

STANDESFUEHRER = {"id": "standesfuehrer", "startdate": "2000-01-01"}
INTERNETREFERENT = {"id": "internetreferent", "startdate": "2020-01-01"}


def _entry(role_id: str, start: str, end: str | None = None) -> RoleEntry:
    return RoleEntry(
        role_id, date.fromisoformat(start), date.fromisoformat(end) if end else None
    )


def _member(db, email: str, first: str, last: str) -> Member:
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    member = Member(
        email=email,
        auth_password=hashed,
        vorname=first,
        nachname=last,
        org_id="vbw",
        auth_locked=False,
    )
    db.add(member)
    db.commit()
    return member


def _put(client, headers, member: Member, roles: list[dict[str, str]]):
    return client.put(
        f"/api/standesdb/members/{member.id}",
        headers=headers,
        json={
            "org_id": "vbw",
            "vorname": member.vorname,
            "nachname": member.nachname,
            "email": member.email,
            "auth_locked": False,
            "roles_history": roles,
        },
    )


@pytest.fixture
def editor(db_session):
    _setup_reference_data(db_session)
    return _create_admin(db_session)


def _events(db) -> list[RoleAssignmentEvent]:
    db.expire_all()
    return (
        db.query(RoleAssignmentEvent)
        .order_by(RoleAssignmentEvent.created_at, RoleAssignmentEvent.id)
        .all()
    )


class TestDiffRoleEntries:
    def test_new_entry_is_a_grant(self):
        changes = diff_role_entries([], [_entry("a", "2024-01-01")])

        assert [(c.action, c.entry.role_id) for c in changes] == [(GRANTED, "a")]

    def test_missing_entry_is_a_revocation(self):
        changes = diff_role_entries([_entry("a", "2024-01-01")], [])

        assert [(c.action, c.entry.role_id) for c in changes] == [(REVOKED, "a")]

    def test_same_entry_with_another_end_date_is_one_period_change(self):
        changes = diff_role_entries(
            [_entry("a", "2024-01-01")], [_entry("a", "2024-01-01", "2025-01-01")]
        )

        assert len(changes) == 1
        assert changes[0].action == PERIOD_CHANGED
        assert changes[0].entry.enddate == date(2025, 1, 1)
        assert changes[0].previous_enddate is None

    def test_period_change_keeps_the_previous_end_date(self):
        changes = diff_role_entries(
            [_entry("a", "2024-01-01", "2025-01-01")], [_entry("a", "2024-01-01")]
        )

        assert changes[0].previous_enddate == date(2025, 1, 1)
        assert changes[0].entry.enddate is None

    def test_identical_lists_have_no_changes(self):
        entries = [_entry("a", "2024-01-01"), _entry("b", "2020-05-05", "2021-05-05")]

        assert diff_role_entries(entries, list(entries)) == []

    def test_same_role_with_another_start_date_is_revoke_plus_grant(self):
        changes = diff_role_entries(
            [_entry("a", "2020-01-01")], [_entry("a", "2021-01-01")]
        )

        assert sorted(c.action.value for c in changes) == ["granted", "revoked"]

    def test_order_is_deterministic(self):
        changes = diff_role_entries(
            [], [_entry("b", "2024-01-01"), _entry("a", "2024-01-01")]
        )

        assert [c.entry.role_id for c in changes] == ["a", "b"]


class TestSavingRolesWritesHistory:
    def test_granting_a_role_records_who_gave_what_to_whom(
        self, client, db_session, editor
    ):
        target = _member(db_session, "target@vbw.at", "Target", "Member")
        headers = _auth_headers(client, db_session, editor)

        assert _put(client, headers, target, [INTERNETREFERENT]).status_code == 200

        (event,) = _events(db_session)
        assert event.action == GRANTED
        assert event.member_id == target.id
        assert event.actor_id == editor.id
        assert event.role_id == "internetreferent"
        assert event.startdate == date(2020, 1, 1)
        assert event.enddate is None
        assert event.previous_enddate is None

    def test_a_self_grant_is_visible_as_such(self, client, db_session, editor):
        headers = _auth_headers(client, db_session, editor)

        _put(client, headers, editor, [STANDESFUEHRER, INTERNETREFERENT])

        (event,) = _events(db_session)
        assert event.member_id == event.actor_id == editor.id

    def test_revoking_a_role_is_recorded(self, client, db_session, editor):
        target = _member(db_session, "target@vbw.at", "Target", "Member")
        headers = _auth_headers(client, db_session, editor)
        _put(client, headers, target, [INTERNETREFERENT])

        _put(client, headers, target, [])

        assert [e.action for e in _events(db_session)] == [GRANTED, REVOKED]

    def test_ending_a_role_is_a_period_change(self, client, db_session, editor):
        target = _member(db_session, "target@vbw.at", "Target", "Member")
        headers = _auth_headers(client, db_session, editor)
        _put(client, headers, target, [INTERNETREFERENT])

        _put(client, headers, target, [{**INTERNETREFERENT, "enddate": "2030-01-01"}])

        changed = _events(db_session)[-1]
        assert changed.action == PERIOD_CHANGED
        assert changed.enddate == date(2030, 1, 1)
        assert changed.previous_enddate is None

    def test_saving_unchanged_roles_records_nothing(self, client, db_session, editor):
        target = _member(db_session, "target@vbw.at", "Target", "Member")
        headers = _auth_headers(client, db_session, editor)
        _put(client, headers, target, [INTERNETREFERENT])

        _put(client, headers, target, [INTERNETREFERENT])

        assert len(_events(db_session)) == 1

    def test_a_save_that_touches_no_role_records_nothing(
        self, client, db_session, editor
    ):
        target = _member(db_session, "target@vbw.at", "Target", "Member")
        headers = _auth_headers(client, db_session, editor)

        _put(client, headers, target, [])

        assert _events(db_session) == []

    def test_several_changes_of_one_save_are_recorded_separately(
        self, client, db_session, editor
    ):
        db_session.add(Role(id="kneipwart", group="funktion", label="Kneipwart"))
        db_session.commit()
        target = _member(db_session, "target@vbw.at", "Target", "Member")
        headers = _auth_headers(client, db_session, editor)
        kneipwart = {"id": "kneipwart", "startdate": "2024-01-01"}

        _put(client, headers, target, [INTERNETREFERENT, kneipwart])

        assert sorted(e.role_id for e in _events(db_session)) == [
            "internetreferent",
            "kneipwart",
        ]

    def test_a_member_created_with_roles_gets_grants(self, client, db_session, editor):
        headers = _auth_headers(client, db_session, editor)

        resp = client.post(
            "/api/standesdb/members",
            headers=headers,
            json={
                "org_id": "vbw",
                "vorname": "New",
                "nachname": "Member",
                "email": "new@vbw.at",
                "auth_locked": False,
                "roles_history": [INTERNETREFERENT],
            },
        )

        assert resp.status_code == 201
        (event,) = _events(db_session)
        assert event.action == GRANTED
        assert str(event.member_id) == resp.json()["id"]

    def test_a_rejected_save_leaves_no_entry(self, client, db_session, editor):
        # The Standesführer role is already held by the editor: overlapping
        # holders are refused (422) before anything is written.
        target = _member(db_session, "target@vbw.at", "Target", "Member")
        headers = _auth_headers(client, db_session, editor)

        resp = _put(client, headers, target, [STANDESFUEHRER])

        assert resp.status_code == 422
        assert _events(db_session) == []


class TestRoleHistoryEndpoint:
    def _grant_many(self, client, db_session, editor, count: int) -> None:
        headers = _auth_headers(client, db_session, editor)
        db_session.add_all(
            Role(id=f"office-{index}", group="funktion", label=f"Office {index}")
            for index in range(count)
        )
        db_session.commit()
        target = _member(db_session, "many@vbw.at", "Many", "Roles")
        roles = [
            {"id": f"office-{index}", "startdate": "2024-01-01"}
            for index in range(count)
        ]
        assert _put(client, headers, target, roles).status_code == 200

    def test_lists_newest_first_with_names_and_conferred_permissions(
        self, client, db_session, editor
    ):
        target = _member(db_session, "target@vbw.at", "Target", "Member")
        headers = _auth_headers(client, db_session, editor)
        _put(client, headers, target, [INTERNETREFERENT])
        _put(client, headers, target, [])

        resp = client.get("/api/system/role-history", headers=headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        newest, oldest = body["items"]
        assert (newest["action"], oldest["action"]) == ("revoked", "granted")
        assert newest["member_cn"] == "Target Member"
        assert newest["actor_cn"] == "Admin Test"
        assert newest["role_label"] == "Internetreferent"
        assert newest["permissions"] == [
            "archiveAdmin",
            "publicContentEditor",
            "systemAdmin",
        ]

    def test_any_authenticated_member_may_read_it(self, client, db_session, editor):
        plain = _member(db_session, "plain@vbw.at", "Plain", "Member")
        headers = _auth_headers(client, db_session, plain)

        assert (
            client.get("/api/system/role-history", headers=headers).status_code == 200
        )

    def test_requires_authentication(self, client):
        assert client.get("/api/system/role-history").status_code == 401

    def test_is_paginated(self, client, db_session, editor):
        self._grant_many(client, db_session, editor, 5)
        headers = _auth_headers(client, db_session, editor)

        page = client.get(
            "/api/system/role-history?page=2&page_size=2", headers=headers
        ).json()

        assert page["total"] == 5
        assert (page["page"], page["page_size"], len(page["items"])) == (2, 2, 2)

    def test_page_size_is_bounded(self, client, db_session, editor):
        headers = _auth_headers(client, db_session, editor)

        resp = client.get("/api/system/role-history?page_size=101", headers=headers)

        assert resp.status_code == 422

    def test_query_count_does_not_scale_with_the_page_size(
        self, client, db_session, editor, count_queries
    ):
        self._grant_many(client, db_session, editor, 20)
        headers = _auth_headers(client, db_session, editor)
        client.get("/api/system/role-history?page_size=1", headers=headers)

        with count_queries() as small:
            client.get("/api/system/role-history?page_size=2", headers=headers)
        with count_queries() as large:
            client.get("/api/system/role-history?page_size=20", headers=headers)

        assert large.count == small.count

    def test_query_count_stays_flat_for_distinct_subjects_roles_and_actors(
        self, client, db_session, editor, count_queries
    ):
        # One subject, role and actor per entry: a lazy load per row would show
        # up here, while a repeated subject is served from the session.
        for index in range(20):
            role = Role(id=f"distinct-{index}", group="funktion", label=f"R{index}")
            subject = _member(db_session, f"subject{index}@vbw.at", "Sub", str(index))
            actor = _member(db_session, f"actor{index}@vbw.at", "Act", str(index))
            db_session.add(role)
            db_session.flush()
            db_session.add(
                RoleAssignmentEvent(
                    member_id=subject.id,
                    role_id=role.id,
                    action=GRANTED,
                    startdate=date(2024, 1, 1),
                    actor_id=actor.id,
                )
            )
        db_session.commit()
        headers = _auth_headers(client, db_session, editor)
        db_session.expunge_all()
        client.get("/api/system/role-history?page_size=1", headers=headers)

        with count_queries() as small:
            client.get("/api/system/role-history?page_size=2", headers=headers)
        db_session.expunge_all()
        with count_queries() as large:
            client.get("/api/system/role-history?page_size=20", headers=headers)

        assert large.count == small.count


class TestAppendOnly:
    def _event(self, db_session, editor) -> RoleAssignmentEvent:
        event = RoleAssignmentEvent(
            member_id=editor.id,
            role_id="standesfuehrer",
            action=GRANTED,
            startdate=date(2000, 1, 1),
            actor_id=editor.id,
        )
        db_session.add(event)
        db_session.commit()
        return event

    def test_an_entry_cannot_be_updated(self, db_session, editor):
        event = self._event(db_session, editor)

        event.enddate = date(2030, 1, 1)
        with pytest.raises(DBAPIError, match="append-only"):
            db_session.commit()
        db_session.rollback()

    def test_an_entry_cannot_be_deleted(self, db_session, editor):
        self._event(db_session, editor)

        with pytest.raises(DBAPIError, match="append-only"):
            db_session.execute(text("DELETE FROM role_assignment_events"))
        db_session.rollback()

    def test_a_member_with_history_cannot_be_deleted(self, db_session, editor):
        self._event(db_session, editor)

        with pytest.raises(IntegrityError):
            db_session.execute(
                text("DELETE FROM members WHERE id = :id"), {"id": editor.id}
            )
        db_session.rollback()

    def test_a_role_with_history_cannot_be_deleted(self, db_session, editor):
        self._event(db_session, editor)
        db_session.query(MemberRole).delete()
        db_session.commit()

        with pytest.raises(IntegrityError):
            db_session.execute(text("DELETE FROM roles WHERE id = 'standesfuehrer'"))
        db_session.rollback()

    def test_a_reversed_period_is_rejected(self, db_session, editor):
        db_session.add(
            RoleAssignmentEvent(
                member_id=editor.id,
                role_id="standesfuehrer",
                action=GRANTED,
                startdate=date(2024, 2, 1),
                enddate=date(2024, 1, 1),
            )
        )

        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_timestamps_are_filled_by_the_database(self, db_session, editor):
        event = self._event(db_session, editor)

        assert event.created_at is not None
        assert event.updated_at is not None
