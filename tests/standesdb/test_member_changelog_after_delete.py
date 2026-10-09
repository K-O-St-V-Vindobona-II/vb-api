"""Change log entries of a removed member keep no reference to a person: no
endpoint delivers them, neither under the removed id nor under another one."""

from datetime import UTC, datetime

from app.models.member import Member
from app.models.members_log import MembersLog
from app.services import standesdb_service
from tests.standesdb.test_standesdb_coverage import _admin, _headers, _seed


def _member(db, email: str) -> Member:
    member = Member(
        email=email, vorname="Max", nachname="Muster", org_id="vbw", state_id="fu"
    )
    db.add(member)
    db.commit()
    return member


def _log(db, member_id, new: str) -> None:
    db.add(
        MembersLog(
            member_id=member_id,
            modified_at=datetime.now(UTC),
            action="update",
            key="telefon",
            old="alt",
            new=new,
        )
    )
    db.commit()


class TestChangelogOfARemovedMember:
    def _removed_and_kept(self, db):
        removed = _member(db, "removed@vbw.at")
        kept = _member(db, "kept@vbw.at")
        _log(db, removed.id, "+43 1 111")
        _log(db, kept.id, "+43 1 222")
        removed_id = removed.id
        db.delete(removed)
        db.commit()
        return removed_id, kept

    def test_the_entries_stay_without_a_member(self, db_session):
        _seed(db_session)
        self._removed_and_kept(db_session)

        orphans = db_session.query(MembersLog).filter(MembersLog.member_id.is_(None))

        assert [e.new for e in orphans] == ["+43 1 111"]

    def test_the_removed_member_has_no_changelog_endpoint(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        removed_id, _kept = self._removed_and_kept(db_session)

        response = client.get(
            f"/api/standesdb/members/{removed_id}/changelog", headers=headers
        )

        assert response.status_code == 404

    def test_the_service_returns_nothing_for_the_removed_id(self, db_session):
        _seed(db_session)
        removed_id, _kept = self._removed_and_kept(db_session)

        result = standesdb_service.get_member_changelog(db_session, removed_id, 1, 25)

        assert result["total"] == 0
        assert result["items"] == []

    def test_another_member_never_receives_the_orphaned_entries(
        self, client, db_session
    ):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        _removed_id, kept = self._removed_and_kept(db_session)

        response = client.get(
            f"/api/standesdb/members/{kept.id}/changelog", headers=headers
        )

        assert response.status_code == 200
        assert [item["new"] for item in response.json()["items"]] == ["+43 1 222"]
