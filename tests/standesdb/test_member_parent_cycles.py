"""Leibverhaeltnisse never close a cycle, and a directory entry loads its
tree in few queries."""

import signal

import pytest
from fastapi import HTTPException

from app.models.member import Member
from app.models.org import Org
from app.models.state import State
from app.services import standesdb_service


def _members(db, n, prefix):
    if not db.get(Org, "vbw"):
        db.add_all(
            [Org(id="vbw", label="V", order=1), State(id="up", label="U", order=1)]
        )
        db.commit()
    members = [
        Member(
            vorname=f"{prefix}{i}",
            nachname="X",
            org_id="vbw",
            state_id="up",
            entlassen=False,
            verstorben=False,
        )
        for i in range(n)
    ]
    db.add_all(members)
    db.commit()
    return members


@pytest.fixture
def chain(db_session):
    """a <- b <- c: b's Leibbursch is a, c's is b."""
    a, b, c = _members(db_session, 3, "Ch")
    b.parent_id = a.id
    c.parent_id = b.id
    db_session.commit()
    return a, b, c


class TestParentValidation:
    @pytest.mark.parametrize("parent_index", [0, 1, 2])
    def test_a_descendant_or_self_cannot_become_the_parent(
        self, db_session, chain, parent_index
    ):
        # a is the top of the chain: none of a, b (child) or c (grandchild)
        # may become its parent
        with pytest.raises(HTTPException) as exc:
            standesdb_service.validate_parent_id(
                db_session, chain[parent_index].id, "vbw", chain[0].id
            )

        assert exc.value.status_code == 400

    def test_unrelated_member_is_accepted(self, db_session, chain):
        other = _members(db_session, 1, "Other")[0]

        standesdb_service.validate_parent_id(db_session, other.id, "vbw", chain[0].id)

    def test_own_ancestor_is_accepted(self, db_session, chain):
        standesdb_service.validate_parent_id(
            db_session, chain[0].id, "vbw", chain[2].id
        )


class TestExistingCycle:
    def test_detail_of_a_member_in_a_cycle_returns(self, db_session, chain):
        a, _, c = chain
        a.parent_id = c.id  # a cycle that got into the data
        db_session.commit()
        db_session.expire_all()

        def timeout(*_):
            raise TimeoutError

        signal.signal(signal.SIGALRM, timeout)
        signal.alarm(5)
        try:
            detail = standesdb_service.get_member_detail(db_session, a.id)
        finally:
            signal.alarm(0)

        ancestry_ids = [entry["id"] for entry in detail.tree["ancestry"]]
        assert len(ancestry_ids) == 3
        assert set(ancestry_ids) == {chain[0].id, chain[1].id, chain[2].id}


class TestTreeQueries:
    def test_query_count_does_not_scale_with_children(self, db_session, count_queries):
        a = _members(db_session, 1, "Root")[0]
        small_children = _members(db_session, 3, "S")
        for child in small_children:
            child.parent_id = a.id
        db_session.commit()
        db_session.expire_all()

        with count_queries() as counter:
            standesdb_service.get_member_detail(db_session, a.id)
        small_count = counter.count

        large_children = _members(db_session, 12, "L")
        for child in large_children:
            child.parent_id = a.id
        db_session.commit()
        db_session.expire_all()

        with count_queries() as counter:
            standesdb_service.get_member_detail(db_session, a.id)
        large_count = counter.count

        assert small_count == large_count
