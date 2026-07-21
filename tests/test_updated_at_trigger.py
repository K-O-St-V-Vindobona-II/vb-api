"""Regression coverage for the updated_at DB trigger (Alembic revision
74d19e4af679): CLAUDE.md mandates that updated_at is maintained exclusively
by a Postgres trigger, never by Python. These tests prove the trigger
actually fires on UPDATE without any application code setting the column."""

from datetime import UTC, datetime

from app.models.archive_dir import ArchiveDir
from app.models.member import Member
from app.models.personal_access_token import PersonalAccessToken


class TestUpdatedAtTrigger:
    def test_archive_dir_updated_at_is_null_until_first_update(self, db_session):
        dir_ = ArchiveDir(name="trigger-test")
        db_session.add(dir_)
        db_session.commit()
        db_session.refresh(dir_)
        assert dir_.updated_at is None

    def test_archive_dir_update_sets_updated_at_via_trigger(self, db_session):
        dir_ = ArchiveDir(name="trigger-test")
        db_session.add(dir_)
        db_session.commit()

        before = datetime.now(UTC)
        dir_.name = "trigger-test-renamed"
        db_session.commit()
        db_session.refresh(dir_)

        assert dir_.updated_at is not None
        assert dir_.updated_at >= before

    def test_member_update_sets_updated_at_via_trigger(self, db_session):
        member = Member(vorname="Trigger", nachname="Test")
        db_session.add(member)
        db_session.commit()
        assert member.updated_at is None

        member.nachname = "Test2"
        db_session.commit()
        db_session.refresh(member)

        assert member.updated_at is not None

    def test_personal_access_token_update_sets_updated_at_via_trigger(self, db_session):
        """This is the one table that previously had a Python onupdate=
        instead of relying on manual assignment — the DB trigger now
        replaces that Python-side mechanism entirely."""
        token = PersonalAccessToken(
            tokenable_type="Member",
            tokenable_id=1,
            name="test-session",
            token="jti-trigger-test",
        )
        db_session.add(token)
        db_session.commit()
        assert token.updated_at is None

        token.name = "renamed-session"
        db_session.commit()
        db_session.refresh(token)

        assert token.updated_at is not None
