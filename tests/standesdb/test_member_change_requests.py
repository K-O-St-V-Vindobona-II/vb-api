"""Tests for the self-service "Meine Stammdaten" change-request workflow:
submit/resubmit, admin org-scoped list+review, atomic decide, IDOR/org
scoping, email-verification reset reuse, and the roles/badges/keys
untouched regression guard. See tests/standesdb/test_member_change_requests_
queries.py for the dedicated N+1 query-count test.
"""

from datetime import UTC, date, datetime

import bcrypt

from app.models.badge import Badge
from app.models.key import Key
from app.models.member import Member
from app.models.member_badge import MemberBadge
from app.models.member_change_request import MemberChangeRequest
from app.models.member_key import MemberKey
from app.models.member_role import MemberRole
from app.models.members_log import MembersLog
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session


def _seed_base(db) -> None:
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="up", label="UP", order=1),
            # standesfuehrer is what _create_admin() grants (needed for the
            # standesdbVbwAdmin/standesdbVbnAdmin permission) - it's a
            # per-org singleton office (validate_roles_history() rejects a
            # second, overlapping holder), so any test that ALSO puts a
            # role on a plain member (e.g. for the roles-untouched
            # regression test) must use a different role like "senior" to
            # avoid an unrelated, correctly-enforced conflict with the
            # admin fixture.
            Role(id="standesfuehrer", group="chc", label="Standesführer", order=1),
            Role(id="senior", group="chc", label="Senior", order=2),
        ]
    )
    db.commit()


def _create_member(db, *, org_id: str = "vbw", email: str, **overrides) -> Member:
    defaults: dict[str, object] = {
        "vorname": "Max",
        "nachname": "Mustermann",
        "org_id": org_id,
        "state_id": "up",
        "email": email,
        "auth_password": "x",
        "auth_locked": False,
    }
    defaults.update(overrides)
    member = Member(**defaults)
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def _create_admin(db, *, org_id: str = "vbw", email: str = "admin@test.at") -> Member:
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    admin = Member(
        vorname="Admin",
        nachname="User",
        org_id=org_id,
        email=email,
        auth_password=hashed,
        auth_locked=False,
    )
    db.add(admin)
    db.commit()
    db.add(
        MemberRole(
            member_id=admin.id,
            role_id="standesfuehrer",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    db.refresh(admin)
    return admin


def _login(db, member: Member) -> dict:
    token, _, _ = create_user_session(db, member)
    return {"Authorization": f"Bearer {token}"}


def _self_service_payload(member: Member, **overrides: object) -> dict[str, object]:
    """Base payload mirrors the given member's own CURRENT live values, so
    only explicitly overridden fields end up in the computed diff - without
    this, every submission would also "propose" every other field's own
    unchanged value as if it were None (e.g. email), producing a false
    extra diff entry."""
    base: dict[str, object] = {
        "vorname": member.vorname,
        "nachname": member.nachname,
        "email": member.email,
        "zustellungen": "deaktiviert",
    }
    base.update(overrides)
    return base


class TestSubmitOwnChangeRequest:
    def test_creates_request_with_correct_diff(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member1@test.at")
        headers = _login(db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(member, nachname="Mustermann-Neu"),
            headers=headers,
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "submitted"}
        request = (
            db_session.query(MemberChangeRequest)
            .filter(MemberChangeRequest.member_id == member.id)
            .first()
        )
        assert request is not None
        assert set(request.proposed_data.keys()) == {"nachname"}
        assert request.proposed_data["nachname"] == {
            "old": "Mustermann",
            "new": "Mustermann-Neu",
        }
        assert request.status.value == "pending"

    def test_no_actual_change_creates_no_request(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member2@test.at")
        headers = _login(db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(member),  # identical to live values
            headers=headers,
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "no_changes"}
        assert (
            db_session.query(MemberChangeRequest)
            .filter(MemberChangeRequest.member_id == member.id)
            .count()
            == 0
        )

    def test_no_actual_change_deletes_existing_pending_request(
        self, db_session, client
    ):
        """Reverting every proposed field back to the live value removes the
        now-empty pending request instead of leaving a stale row."""
        _seed_base(db_session)
        member = _create_member(db_session, email="member3@test.at")
        headers = _login(db_session, member)

        client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(member, nachname="Geaendert"),
            headers=headers,
        )
        assert (
            db_session.query(MemberChangeRequest)
            .filter(MemberChangeRequest.member_id == member.id)
            .count()
            == 1
        )

        resp = client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(member),  # back to the live value
            headers=headers,
        )

        assert resp.json() == {"status": "no_changes"}
        assert (
            db_session.query(MemberChangeRequest)
            .filter(MemberChangeRequest.member_id == member.id)
            .count()
            == 0
        )

    def test_resubmit_while_pending_overwrites_same_row(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member4@test.at")
        headers = _login(db_session, member)

        client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(member, nachname="ErsteAenderung"),
            headers=headers,
        )
        first = (
            db_session.query(MemberChangeRequest)
            .filter(MemberChangeRequest.member_id == member.id)
            .first()
        )
        assert first is not None
        first_id = first.id

        client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(member, nachname="ZweiteAenderung"),
            headers=headers,
        )

        rows = (
            db_session.query(MemberChangeRequest)
            .filter(MemberChangeRequest.member_id == member.id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].id == first_id
        assert rows[0].proposed_data["nachname"]["new"] == "ZweiteAenderung"

    def test_plain_member_without_any_permission_can_submit(self, db_session, client):
        """Proves this endpoint is NOT gated behind an admin permission -
        _create_member grants no role at all."""
        _seed_base(db_session)
        member = _create_member(db_session, email="member5@test.at")
        headers = _login(db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(member, nachname="Geaendert"),
            headers=headers,
        )

        assert resp.status_code != 403
        assert resp.status_code == 200

    def test_rejects_admin_only_field(self, db_session, client):
        """StrictInputModel(extra="forbid") - org_id (admin-only) must not
        even be accepted on this endpoint."""
        _seed_base(db_session)
        member = _create_member(db_session, email="member6@test.at")
        headers = _login(db_session, member)

        resp = client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(member, org_id="vbn"),
            headers=headers,
        )

        assert resp.status_code == 422

    def test_notifies_org_admins_with_correct_recipients(
        self, db_session, client, mock_arq_pool
    ):
        _seed_base(db_session)
        member = _create_member(db_session, email="member7@test.at")
        vbw_admin = _create_admin(db_session, org_id="vbw", email="vbwadmin@test.at")
        _create_admin(db_session, org_id="vbn", email="vbnadmin@test.at")
        headers = _login(db_session, member)

        client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(member, nachname="Geaendert"),
            headers=headers,
        )

        mock_arq_pool.enqueue_job.assert_called_once()
        task_name, recipients, member_cn, diff = mock_arq_pool.enqueue_job.call_args[0]
        assert task_name == "task_send_member_change_request_submitted_email"
        assert recipients == [vbw_admin.email]
        assert member_cn == member.cn
        assert diff["nachname"]["new"] == "Geaendert"


class TestGetOwnChangeRequest:
    def test_returns_404_when_none_pending(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member8@test.at")
        headers = _login(db_session, member)

        resp = client.get("/api/standesdb/members/me/change-request", headers=headers)

        assert resp.status_code == 404

    def test_returns_proposed_fields_when_pending(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member9@test.at")
        headers = _login(db_session, member)
        client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(member, nachname="Geaendert"),
            headers=headers,
        )

        resp = client.get("/api/standesdb/members/me/change-request", headers=headers)

        assert resp.status_code == 200
        assert resp.json()["proposed_fields"]["nachname"] == "Geaendert"


class TestGetOwnStammdaten:
    def test_returns_live_values_without_admin_only_fields(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member10@test.at", gruender=True)
        headers = _login(db_session, member)

        resp = client.get("/api/standesdb/members/me/stammdaten", headers=headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["nachname"] == "Mustermann"
        assert "gruender" not in body
        assert "org_id" not in body
        assert "auth_locked" not in body
        assert "roles_history" not in body
        assert "geburtsdatum" not in body
        assert "geburtsdatum_accuracy" not in body


class TestListMemberChangeRequests:
    def test_org_scoped_admin_only_sees_own_org(self, db_session, client):
        _seed_base(db_session)
        vbw_member = _create_member(db_session, org_id="vbw", email="vbwmember@test.at")
        vbn_member = _create_member(db_session, org_id="vbn", email="vbnmember@test.at")
        vbw_admin = _create_admin(db_session, org_id="vbw", email="vbwadmin2@test.at")

        client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(vbw_member, nachname="A"),
            headers=_login(db_session, vbw_member),
        )
        client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(vbn_member, nachname="B"),
            headers=_login(db_session, vbn_member),
        )

        resp = client.get(
            "/api/standesdb/member-change-requests",
            headers=_login(db_session, vbw_admin),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["member_id"] == vbw_member.id

    def test_non_admin_gets_403(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="plain@test.at")
        headers = _login(db_session, member)

        resp = client.get("/api/standesdb/member-change-requests", headers=headers)

        assert resp.status_code == 403


class TestGetMemberChangeRequest:
    def test_idor_cross_org_admin_forbidden(self, db_session, client):
        _seed_base(db_session)
        vbw_member = _create_member(db_session, org_id="vbw", email="vbwm@test.at")
        vbn_admin = _create_admin(db_session, org_id="vbn", email="vbna@test.at")
        client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(vbw_member, nachname="Geaendert"),
            headers=_login(db_session, vbw_member),
        )
        request = (
            db_session.query(MemberChangeRequest)
            .filter(MemberChangeRequest.member_id == vbw_member.id)
            .first()
        )

        resp = client.get(
            f"/api/standesdb/member-change-requests/{request.id}",
            headers=_login(db_session, vbn_admin),
        )

        assert resp.status_code == 403

    def test_returns_diff_and_field_count(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member11@test.at")
        admin = _create_admin(db_session, email="admin11@test.at")
        client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(member, nachname="Geaendert"),
            headers=_login(db_session, member),
        )
        request = (
            db_session.query(MemberChangeRequest)
            .filter(MemberChangeRequest.member_id == member.id)
            .first()
        )

        resp = client.get(
            f"/api/standesdb/member-change-requests/{request.id}",
            headers=_login(db_session, admin),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert body["diff"] == [
            {"field": "nachname", "old": "Mustermann", "new": "Geaendert"}
        ]


class TestDecideMemberChangeRequest:
    def _submit_and_get_request(
        self, db_session, client, member: Member, **payload_overrides: object
    ) -> MemberChangeRequest:
        client.post(
            "/api/standesdb/members/me/change-request",
            json=_self_service_payload(member, **payload_overrides),
            headers=_login(db_session, member),
        )
        request = (
            db_session.query(MemberChangeRequest)
            .filter(MemberChangeRequest.member_id == member.id)
            .first()
        )
        assert request is not None
        return request

    def test_incomplete_decisions_returns_422(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member12@test.at")
        admin = _create_admin(db_session, email="admin12@test.at")
        request = self._submit_and_get_request(
            db_session, client, member, nachname="A", vorname="B"
        )

        resp = client.post(
            f"/api/standesdb/member-change-requests/{request.id}/decide",
            json={"field_decisions": {"nachname": "approved"}},  # missing "vorname"
            headers=_login(db_session, admin),
        )

        assert resp.status_code == 422

    def test_idor_cross_org_admin_cannot_decide(self, db_session, client):
        _seed_base(db_session)
        vbw_member = _create_member(db_session, org_id="vbw", email="vbwm2@test.at")
        vbn_admin = _create_admin(db_session, org_id="vbn", email="vbna2@test.at")
        request = self._submit_and_get_request(
            db_session, client, vbw_member, nachname="Geaendert"
        )

        resp = client.post(
            f"/api/standesdb/member-change-requests/{request.id}/decide",
            json={"field_decisions": {"nachname": "approved"}},
            headers=_login(db_session, vbn_admin),
        )

        assert resp.status_code == 403

    def test_approve_applies_change_and_resolves_request(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member13@test.at")
        admin = _create_admin(db_session, email="admin13@test.at")
        request = self._submit_and_get_request(
            db_session, client, member, nachname="Geaendert"
        )

        resp = client.post(
            f"/api/standesdb/member-change-requests/{request.id}/decide",
            json={"field_decisions": {"nachname": "approved"}},
            headers=_login(db_session, admin),
        )

        assert resp.status_code == 200
        db_session.refresh(member)
        assert member.nachname == "Geaendert"
        db_session.refresh(request)
        assert request.status.value == "resolved"
        assert request.field_decisions == {"nachname": "approved"}
        assert request.resolved_by == admin.id
        assert request.resolved_at is not None

    def test_reject_does_not_apply_change(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member14@test.at")
        admin = _create_admin(db_session, email="admin14@test.at")
        request = self._submit_and_get_request(
            db_session, client, member, nachname="Geaendert"
        )

        resp = client.post(
            f"/api/standesdb/member-change-requests/{request.id}/decide",
            json={"field_decisions": {"nachname": "rejected"}},
            headers=_login(db_session, admin),
        )

        assert resp.status_code == 200
        db_session.refresh(member)
        assert member.nachname == "Mustermann"

    def test_partial_approval_applies_only_approved_fields(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member15@test.at")
        admin = _create_admin(db_session, email="admin15@test.at")
        request = self._submit_and_get_request(
            db_session,
            client,
            member,
            nachname="NeuerNachname",
            vorname="NeuerVorname",
        )

        client.post(
            f"/api/standesdb/member-change-requests/{request.id}/decide",
            json={
                "field_decisions": {
                    "nachname": "approved",
                    "vorname": "rejected",
                }
            },
            headers=_login(db_session, admin),
        )

        db_session.refresh(member)
        assert member.nachname == "NeuerNachname"
        assert member.vorname == "Max"

    def test_approving_email_change_resets_email_verified_at(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="old@test.at")
        member.email_verified_at = datetime.now(UTC)
        db_session.commit()
        admin = _create_admin(db_session, email="admin16@test.at")
        request = self._submit_and_get_request(
            db_session, client, member, email="new@test.at"
        )

        client.post(
            f"/api/standesdb/member-change-requests/{request.id}/decide",
            json={"field_decisions": {"email": "approved"}},
            headers=_login(db_session, admin),
        )

        db_session.refresh(member)
        assert member.email == "new@test.at"
        assert member.email_verified_at is None

    def test_roles_badges_keys_untouched_after_resolution(self, db_session, client):
        """Highest-priority regression test: resolving a self-service
        request (which never touches roles/badges/keys) must not wipe them -
        this is exactly what an incompletely-built current_full dict inside
        resolve_change_request() would do, since apply_member_input() fully
        replaces these from whatever lists it's given."""
        _seed_base(db_session)
        db_session.add_all(
            [
                Badge(id=1, name="Testabzeichen", group="ehrenzeichen", order=1),
                Key(id=1, name="Testschluessel"),
            ]
        )
        db_session.commit()
        member = _create_member(db_session, email="member17@test.at")
        db_session.add(
            MemberRole(
                member_id=member.id,
                role_id="senior",  # NOT standesfuehrer - see _seed_base()
                startdate=date(2010, 1, 1),
                enddate=None,
            )
        )
        db_session.add(MemberBadge(member_id=member.id, badge_id=1))
        db_session.add(MemberKey(member_id=member.id, key_id=1))
        db_session.commit()
        admin = _create_admin(db_session, email="admin17@test.at")
        request = self._submit_and_get_request(
            db_session, client, member, nachname="Geaendert"
        )

        resp = client.post(
            f"/api/standesdb/member-change-requests/{request.id}/decide",
            json={"field_decisions": {"nachname": "approved"}},
            headers=_login(db_session, admin),
        )
        assert resp.status_code == 200

        # Queried directly (not via member.member_roles/etc.) for an
        # unambiguous ground-truth check of what the API's own session
        # actually persisted.
        db_session.expire_all()
        roles = (
            db_session.query(MemberRole).filter(MemberRole.member_id == member.id).all()
        )
        badges = (
            db_session.query(MemberBadge)
            .filter(MemberBadge.member_id == member.id)
            .all()
        )
        keys = (
            db_session.query(MemberKey).filter(MemberKey.member_id == member.id).all()
        )
        assert [r.role_id for r in roles] == ["senior"]
        assert [b.badge_id for b in badges] == [1]
        assert [k.key_id for k in keys] == [1]

    def test_resolution_produces_members_log_entry(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member18@test.at")
        admin = _create_admin(db_session, email="admin18@test.at")
        request = self._submit_and_get_request(
            db_session, client, member, nachname="Geaendert"
        )

        client.post(
            f"/api/standesdb/member-change-requests/{request.id}/decide",
            json={"field_decisions": {"nachname": "approved"}},
            headers=_login(db_session, admin),
        )

        log = (
            db_session.query(MembersLog)
            .filter(MembersLog.member_id == member.id, MembersLog.key == "nachname")
            .first()
        )
        assert log is not None
        assert log.modified_by == admin.id
        assert log.old == "Mustermann"
        assert log.new == "Geaendert"

    def test_uniqueness_collision_at_resolution_returns_409(self, db_session, client):
        _seed_base(db_session)
        _create_member(
            db_session,
            email="taken@test.at",
            vorname="Erika",
            nachname="Kollision",
        )
        member = _create_member(db_session, email="member19@test.at")
        admin = _create_admin(db_session, email="admin19@test.at")
        request = self._submit_and_get_request(
            db_session,
            client,
            member,
            vorname="Erika",
            nachname="Kollision",
        )

        resp = client.post(
            f"/api/standesdb/member-change-requests/{request.id}/decide",
            json={"field_decisions": {"vorname": "approved", "nachname": "approved"}},
            headers=_login(db_session, admin),
        )

        assert resp.status_code == 409

    def test_already_resolved_request_returns_409(self, db_session, client):
        _seed_base(db_session)
        member = _create_member(db_session, email="member20@test.at")
        admin = _create_admin(db_session, email="admin20@test.at")
        request = self._submit_and_get_request(
            db_session, client, member, nachname="Geaendert"
        )
        headers = _login(db_session, admin)
        client.post(
            f"/api/standesdb/member-change-requests/{request.id}/decide",
            json={"field_decisions": {"nachname": "approved"}},
            headers=headers,
        )

        resp = client.post(
            f"/api/standesdb/member-change-requests/{request.id}/decide",
            json={"field_decisions": {"nachname": "approved"}},
            headers=headers,
        )

        assert resp.status_code == 409

    def test_notifies_member_of_resolution(self, db_session, client, mock_arq_pool):
        _seed_base(db_session)
        member = _create_member(db_session, email="member21@test.at")
        admin = _create_admin(db_session, email="admin21@test.at")
        request = self._submit_and_get_request(
            db_session, client, member, nachname="Geaendert"
        )
        # _submit_and_get_request() already triggered one enqueue_job()
        # call for the "submitted" notification — reset so this test only
        # observes the "resolved" notification from the decide call below.
        mock_arq_pool.enqueue_job.reset_mock()

        client.post(
            f"/api/standesdb/member-change-requests/{request.id}/decide",
            json={"field_decisions": {"nachname": "approved"}},
            headers=_login(db_session, admin),
        )

        mock_arq_pool.enqueue_job.assert_called_once()
        task_name, to_email, diff, decisions = mock_arq_pool.enqueue_job.call_args[0]
        assert task_name == "task_send_member_change_request_resolved_email"
        assert to_email == "member21@test.at"
        assert diff["nachname"]["new"] == "Geaendert"
        assert decisions == {"nachname": "approved"}
