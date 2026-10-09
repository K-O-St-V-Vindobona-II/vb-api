"""Leibverhaeltnisse never close a cycle, and a directory entry loads its
tree in few queries."""

import signal
from itertools import pairwise

import pytest
from fastapi import HTTPException
from sqlalchemy import text

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
        # A cycle that got into the data before the database refused them: the
        # trigger has to be off to store one.
        db_session.execute(
            text("ALTER TABLE members DISABLE TRIGGER members_parent_cycle")
        )
        a.parent_id = c.id
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

    def test_statement_count_does_not_grow_with_several_generations(
        self, db_session, count_queries
    ):
        small_root, _, _ = _family(db_session, "Qs", 3, 2)
        with count_queries() as counter:
            standesdb_service.get_member_detail(db_session, small_root.id)
        small_count = counter.count

        large_root, _, _ = _family(db_session, "Ql", 12, 2)
        with count_queries() as counter:
            standesdb_service.get_member_detail(db_session, large_root.id)
        large_count = counter.count

        assert small_count == large_count


def _family(db_session, prefix, children, grandchildren):
    """A root with `children` children, each with `grandchildren` children."""
    root = _members(db_session, 1, f"{prefix}R")[0]
    kids = _members(db_session, children, f"{prefix}K")
    for kid in kids:
        kid.parent_id = root.id
    db_session.commit()
    grand = {}
    for index, kid in enumerate(kids):
        grand[kid.id] = _members(db_session, grandchildren, f"{prefix}G{index}x")
        for member in grand[kid.id]:
            member.parent_id = kid.id
    db_session.commit()
    db_session.expire_all()
    return root, kids, grand


class TestTreeShape:
    def test_every_generation_hangs_under_its_own_parent(self, db_session):
        root, kids, grand = _family(db_session, "Sh", 3, 2)

        tree = standesdb_service.get_member_detail(db_session, root.id).tree

        assert {child["id"] for child in tree["children"]} == {k.id for k in kids}
        for child in tree["children"]:
            assert {g["id"] for g in child["children"]} == {
                g.id for g in grand[child["id"]]
            }
            assert all(g["children"] == [] for g in child["children"])

    def test_siblings_are_ordered_by_id(self, db_session):
        root, kids, grand = _family(db_session, "Or", 4, 3)
        # An update moves a row to the end of the table: without an explicit
        # order the oldest member would now come last.
        kids[0].couleurname = "Updated"
        grand[kids[1].id][0].couleurname = "Updated"
        db_session.commit()
        db_session.expire_all()

        tree = standesdb_service.get_member_detail(db_session, root.id).tree

        child_ids = [child["id"] for child in tree["children"]]
        assert child_ids == sorted(child_ids)
        for child in tree["children"]:
            grandchild_ids = [g["id"] for g in child["children"]]
            assert grandchild_ids == sorted(grandchild_ids)

    def test_a_member_without_children_has_an_empty_tree(self, db_session):
        lone = _members(db_session, 1, "Lone")[0]

        tree = standesdb_service.get_member_detail(db_session, lone.id).tree

        assert tree["children"] == []
        assert [entry["id"] for entry in tree["ancestry"]] == [lone.id]

    def test_the_ancestry_runs_from_the_top_down(self, db_session, chain):
        a, b, c = chain

        tree = standesdb_service.get_member_detail(db_session, c.id).tree

        assert [entry["id"] for entry in tree["ancestry"]] == [a.id, b.id, c.id]


class TestTreeDepth:
    DEPTH = standesdb_service._MAX_TREE_DEPTH

    def _long_chain(self, db_session):
        members = _members(db_session, self.DEPTH + 5, "Deep")
        for parent, child in pairwise(members):
            child.parent_id = parent.id
        db_session.commit()
        db_session.expire_all()
        return members

    def test_descendants_are_followed_up_to_the_depth_limit(self, db_session):
        members = self._long_chain(db_session)

        tree = standesdb_service.get_member_detail(db_session, members[0].id).tree

        levels = 0
        node = tree
        while node["children"]:
            node = node["children"][0]
            levels += 1
        assert levels == self.DEPTH

    def test_ancestors_are_followed_up_to_the_depth_limit(self, db_session):
        members = self._long_chain(db_session)

        tree = standesdb_service.get_member_detail(db_session, members[-1].id).tree

        assert len(tree["ancestry"]) == self.DEPTH
        assert tree["ancestry"][-1]["id"] == members[-1].id
