"""N+1 regression test for GET /standesdb/member-change-requests - a list
endpoint whose query count must not scale with the number of pending
requests, per this project's list-endpoint testing convention. Kept in its
own file to mirror the tests/p4x/test_p4x_service_queries.py convention.
"""

from datetime import date

import bcrypt

from app.models.member import Member
from app.models.member_change_request import MemberChangeRequest
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session


def _seed_base(db) -> None:
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            State(id="up", label="UP", order=1),
            Role(id="standesfuehrer", group="chc", label="Standesführer", order=1),
        ]
    )
    db.commit()


def _create_admin(db) -> Member:
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    admin = Member(
        vorname="Admin",
        nachname="User",
        org_id="vbw",
        email="queries-admin@test.at",
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


def _create_pending_request(db, index: int) -> None:
    member = Member(
        vorname=f"Member{index}",
        nachname="Test",
        org_id="vbw",
        state_id="up",
        email=f"queries-member{index}@test.at",
        auth_password="x",
        auth_locked=False,
    )
    db.add(member)
    db.commit()
    db.add(
        MemberChangeRequest(
            member_id=member.id,
            proposed_data={"nachname": {"old": "Test", "new": f"Test{index}"}},
        )
    )
    db.commit()


def _login(db, member: Member) -> dict:
    token, _, _ = create_user_session(db, member)
    return {"Authorization": f"Bearer {token}"}


class TestMemberChangeRequestListQueryCount:
    def test_query_count_does_not_scale_with_request_count(
        self, client, db_session, count_queries
    ):
        _seed_base(db_session)
        admin = _create_admin(db_session)
        headers = _login(db_session, admin)
        _create_pending_request(db_session, 0)

        with count_queries() as small:
            resp_small = client.get(
                "/api/standesdb/member-change-requests", headers=headers
            )

        for i in range(1, 6):
            _create_pending_request(db_session, i)

        with count_queries() as large:
            resp_large = client.get(
                "/api/standesdb/member-change-requests", headers=headers
            )

        assert resp_small.status_code == 200
        assert resp_large.status_code == 200
        assert resp_large.json()["total"] == 6
        assert large.count == small.count
