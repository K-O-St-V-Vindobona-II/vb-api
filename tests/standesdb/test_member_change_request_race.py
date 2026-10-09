"""Two admins deciding the same change request are serialised by a row lock.

The race needs two real database connections with committed data, so these
tests create their rows outside the per-test transaction and remove them
afterwards."""

import threading
from datetime import date
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.database import SessionLocal, engine
from app.models.member import Member
from app.models.member_change_request import MemberChangeRequest
from app.models.member_role import MemberRole
from app.models.members_log import MembersLog
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.schemas.standesdb import MemberChangeRequestDecisionRequest
from app.services import member_change_request_service, standesdb_entry_service
from tests.auth.test_refresh_serialised import (
    WAIT_SECONDS,
    _wait_until_a_request_waits_for_a_lock,
)

if TYPE_CHECKING:
    import uuid


def _decision(request: MemberChangeRequest) -> MemberChangeRequestDecisionRequest:
    return MemberChangeRequestDecisionRequest(
        field_decisions={"nachname": "approved"},
        expected_updated_at=request.updated_at,
    )


def _committed_request() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(admin id, member id, request id) of a pending request committed for real."""
    setup = Session(bind=engine)
    setup.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            State(id="up", label="UP", order=1),
            Role(id="standesfuehrer", group="chc", label="Standesführer", order=1),
        ]
    )
    setup.commit()
    admin = Member(
        vorname="Admin",
        nachname="Race",
        org_id="vbw",
        email="race-admin@example.at",
        auth_password="x",
        auth_locked=False,
    )
    member = Member(
        vorname="Max",
        nachname="Mustermann",
        org_id="vbw",
        state_id="up",
        email="race-member@example.at",
        auth_password="x",
        auth_locked=False,
    )
    setup.add_all([admin, member])
    setup.commit()
    setup.add(
        MemberRole(
            member_id=admin.id,
            role_id="standesfuehrer",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    request = MemberChangeRequest(
        member_id=member.id,
        proposed_data={"nachname": {"old": "Mustermann", "new": "Neumann"}},
    )
    setup.add(request)
    setup.commit()
    ids = (admin.id, member.id, request.id)
    setup.close()
    return ids


def _remove_committed_rows() -> None:
    cleanup = Session(bind=engine)
    for model in (
        MembersLog,
        MemberChangeRequest,
        MemberRole,
        Member,
        Role,
        State,
        Org,
    ):
        cleanup.query(model).delete()
    cleanup.commit()
    cleanup.close()


class TestConcurrentDecision:
    def test_the_second_decision_waits_and_is_then_refused(self):
        admin_id, member_id, request_id = _committed_request()
        first = SessionLocal()
        outcome: dict[str, object] = {}
        try:
            first_admin = first.get(Member, admin_id)
            request = member_change_request_service.get_change_request_or_404(
                first, request_id, for_update=True
            )
            data = _decision(request)

            def second_decision() -> None:
                second = SessionLocal()
                try:
                    outcome["result"] = standesdb_entry_service.decide_change_request(
                        second, request_id, data, second.get(Member, admin_id)
                    )
                except HTTPException as error:
                    outcome["status"] = error.status_code
                    outcome["detail"] = error.detail
                finally:
                    second.close()

            thread = threading.Thread(target=second_decision)
            thread.start()
            try:
                _wait_until_a_request_waits_for_a_lock()
                # The first admin decides now and commits; only then may the
                # second one continue.
                member_change_request_service.resolve_change_request(
                    first,
                    request,
                    data.field_decisions,
                    data.expected_updated_at,
                    first_admin,
                )
            finally:
                thread.join(timeout=WAIT_SECONDS)

            check = Session(bind=engine)
            logged = check.query(MembersLog).filter_by(member_id=member_id).count()
            check.close()
        finally:
            first.close()
            _remove_committed_rows()

        assert not thread.is_alive()
        assert outcome == {
            "status": 409,
            "detail": "Antrag wurde bereits entschieden.",
        }
        assert logged == 1
