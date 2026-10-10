"""Direct tests of the lookups, permission checks and notification hand-overs
that the standesdb routes delegate to standesdb_entry_service."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.models.contact import Contact
from app.models.member import Member
from app.models.member_change_request import MemberChangeRequest
from app.schemas.standesdb import (
    MemberChangeRequestDecisionRequest,
    MemberSelfServiceSaveRequest,
)
from app.services import member_change_request_service, standesdb_entry_service
from tests.standesdb.test_member_change_requests import (
    _create_admin,
    _create_member,
    _seed_base,
)


def _refusal(call) -> HTTPException:
    with pytest.raises(HTTPException) as info:
        call()
    return info.value


class TestRequireOrgAdmin:
    def test_a_missing_org_is_refused_before_the_permissions_are_read(self, db_session):
        _seed_base(db_session)
        admin = _create_admin(db_session)

        error = _refusal(lambda: standesdb_entry_service.require_org_admin(admin, None))

        assert error.status_code == 422
        assert error.detail == "Keine Verbindung zugewiesen."

    def test_an_admin_of_another_org_is_refused(self, db_session):
        _seed_base(db_session)
        admin = _create_admin(db_session, org_id="vbw")

        error = _refusal(
            lambda: standesdb_entry_service.require_org_admin(admin, "vbn")
        )

        assert error.status_code == 403
        assert error.detail == "Fehlende Berechtigung: standesdbVbnAdmin"

    def test_the_admin_of_the_org_passes(self, db_session):
        _seed_base(db_session)
        admin = _create_admin(db_session, org_id="vbw")

        standesdb_entry_service.require_org_admin(admin, "vbw")


class TestRequireAnyOrgAdmin:
    def test_a_plain_member_is_refused(self, db_session):
        _seed_base(db_session)
        member = _create_member(db_session, email="plain@test.at")

        error = _refusal(lambda: standesdb_entry_service.require_any_org_admin(member))

        assert error.status_code == 403

    def test_an_admin_of_either_org_passes(self, db_session):
        _seed_base(db_session)
        admin = _create_admin(db_session, org_id="vbn")

        standesdb_entry_service.require_any_org_admin(admin)


class TestMemberLookups:
    def test_an_unknown_member_is_a_404(self, db_session):
        error = _refusal(
            lambda: standesdb_entry_service.get_member_or_404(db_session, uuid.uuid4())
        )

        assert error.status_code == 404

    def test_a_member_without_an_org_is_refused_with_the_specific_message(
        self, db_session
    ):
        _seed_base(db_session)
        admin = _create_admin(db_session)
        orphan = _create_member(db_session, email="orphan@test.at", org_id=None)

        error = _refusal(
            lambda: standesdb_entry_service.get_assigned_member_for_org_admin(
                db_session, orphan.id, admin
            )
        )

        assert error.status_code == 422
        assert error.detail == "Mitglied hat keine Verbindung zugewiesen."

    def test_the_generic_lookup_refuses_an_orphan_with_the_org_message(
        self, db_session
    ):
        _seed_base(db_session)
        admin = _create_admin(db_session)
        orphan = _create_member(db_session, email="orphan2@test.at", org_id=None)

        error = _refusal(
            lambda: standesdb_entry_service.get_member_for_org_admin(
                db_session, orphan.id, admin
            )
        )

        assert error.detail == "Keine Verbindung zugewiesen."

    def test_a_deleted_contact_is_a_404(self, db_session):
        contact = Contact(
            kontakttyp="person", name="Gone", deleted_at=datetime.now(UTC)
        )
        db_session.add(contact)
        db_session.commit()

        error = _refusal(
            lambda: standesdb_entry_service.get_contact_or_404(db_session, contact.id)
        )

        assert error.status_code == 404
        assert error.detail == "Kontakt nicht gefunden."


class TestSubmitOwnChangeRequest:
    def test_a_member_without_an_org_cannot_submit(self, db_session):
        _seed_base(db_session)
        orphan = _create_member(db_session, email="orphan3@test.at", org_id=None)
        data = MemberSelfServiceSaveRequest(
            vorname="Neu", nachname="Name", zustellungen="deaktiviert"
        )

        error = _refusal(
            lambda: standesdb_entry_service.submit_own_change_request(
                db_session, data, orphan
            )
        )

        assert error.status_code == 422
        assert "keine Verbindung" in error.detail


class TestDecideChangeRequest:
    def test_a_member_without_an_address_gets_no_resolved_mail(self, db_session):
        _seed_base(db_session)
        admin = _create_admin(db_session)
        member = _create_member(db_session, email="soon-gone@test.at")
        member_change_request_service.submit_change_request(
            db_session,
            member,
            MemberSelfServiceSaveRequest(
                vorname="Max",
                nachname="Neu",
                email=member.email,
                zustellungen="deaktiviert",
            ),
        )
        member.email = None
        db_session.commit()
        request = db_session.query(MemberChangeRequest).one()
        data = MemberChangeRequestDecisionRequest(
            field_decisions={"nachname": "approved"},
            expected_updated_at=request.updated_at,
        )

        notification = standesdb_entry_service.decide_change_request(
            db_session, request.id, data, admin
        )

        assert notification is None


class TestOwnImageChangedRecipients:
    def test_a_member_without_an_org_has_nobody_to_notify(self, db_session):
        _seed_base(db_session)
        orphan = Member(email="orphan4@test.at", org_id=None)

        assert (
            standesdb_entry_service.get_own_image_changed_recipients(db_session, orphan)
            is None
        )

    def test_the_admins_of_the_members_org_are_the_recipients(self, db_session):
        _seed_base(db_session)
        admin = _create_admin(db_session, org_id="vbw")
        member = _create_member(db_session, email="owner@test.at", org_id="vbw")

        recipients = standesdb_entry_service.get_own_image_changed_recipients(
            db_session, member
        )

        assert recipients == [admin.email]
