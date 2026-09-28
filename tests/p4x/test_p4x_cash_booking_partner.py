"""A cash booking (no counter-account IBAN) cannot be assigned a partner:
the partner row is keyed by IBAN, and every cash booking shares the empty
one, so one assignment would silently apply to every other cash booking."""

from datetime import date

import pytest
from fastapi import HTTPException

from app.models.member import Member
from app.models.org import Org
from app.models.state import State
from app.services import p4x_partner_service
from tests.p4x.test_p4x_service_queries import _add_tx, _create_account


class TestCashBookings:
    def test_a_booking_without_counter_account_cannot_get_a_partner(self, db_session):
        account = _create_account(db_session)
        db_session.add_all(
            [Org(id="vbw", label="V", order=1), State(id="up", label="U", order=1)]
        )
        member = Member(vorname="Anna", nachname="Bar", org_id="vbw", state_id="up")
        db_session.add(member)
        db_session.commit()
        cash = _add_tx(
            db_session,
            account,
            date(2026, 3, 1),
            50.0,
            iban="",
            subject="Bar",
            hash_suffix="c",
        )

        with pytest.raises(HTTPException) as exc:
            p4x_partner_service.set_transaction_partner(
                db_session, cash, {"type": "member", "id": member.id}, False, None
            )

        assert exc.value.status_code == 422
