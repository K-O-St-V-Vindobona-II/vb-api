"""Fields that every write path requires are required in the database as well:
comment text, badge name, and the description lengths the API enforces."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
from app.models.archive_file_comment import ArchiveFileComment
from app.models.archive_store_item import ArchiveStoreItem
from app.models.badge import Badge


def _archive_file(db, **columns) -> ArchiveFile:
    item = ArchiveStoreItem(
        name="f", extension="txt", mime_type="text/plain", size=1, sha256_hash="d" * 64
    )
    db.add(item)
    db.flush()
    archive_file = ArchiveFile(archive_store_item_id=item.id, **columns)
    db.add(archive_file)
    db.commit()
    return archive_file


def _refused(db, kind: str) -> None:
    with pytest.raises(IntegrityError, match=kind):
        db.commit()


class TestCommentContent:
    def test_a_comment_without_text_is_refused(self, db_session):
        archive_file = _archive_file(db_session)
        db_session.add(
            ArchiveFileComment(archive_file_id=archive_file.id, content=None)
        )

        _refused(db_session, "not-null constraint")

    @pytest.mark.parametrize("content", ["", "a", "abcd"])
    def test_a_comment_shorter_than_the_api_minimum_is_refused(
        self, db_session, content
    ):
        archive_file = _archive_file(db_session)
        db_session.add(
            ArchiveFileComment(archive_file_id=archive_file.id, content=content)
        )

        _refused(db_session, "check constraint")

    def test_a_comment_longer_than_the_api_maximum_is_refused(self, db_session):
        archive_file = _archive_file(db_session)
        db_session.add(
            ArchiveFileComment(archive_file_id=archive_file.id, content="ä" * 1001)
        )

        _refused(db_session, "check constraint")

    @pytest.mark.parametrize("length", [5, 1000])
    def test_comments_at_the_limits_are_accepted(self, db_session, length):
        archive_file = _archive_file(db_session)
        db_session.add(
            ArchiveFileComment(archive_file_id=archive_file.id, content="ä" * length)
        )

        db_session.commit()


class TestBadgeName:
    def test_a_badge_without_a_name_is_refused(self, db_session):
        db_session.add(Badge(name=None))

        _refused(db_session, "not-null constraint")

    def test_a_badge_with_a_name_is_accepted(self, db_session):
        db_session.add(Badge(name="Ehrenmitglied"))

        db_session.commit()


class TestDescriptionLength:
    def test_directory_description_at_the_limit_is_accepted(self, db_session):
        db_session.add(ArchiveDir(name="Dir1x", description="ä" * 128))

        db_session.commit()

    def test_directory_description_over_the_limit_is_refused(self, db_session):
        db_session.add(ArchiveDir(name="Dir1x", description="ä" * 129))

        _refused(db_session, "check constraint")

    def test_directory_without_description_is_accepted(self, db_session):
        db_session.add(ArchiveDir(name="Dir1x", description=None))

        db_session.commit()

    def test_file_description_at_the_limit_is_accepted(self, db_session):
        _archive_file(db_session, description="ä" * 128)

    def test_file_description_over_the_limit_is_refused(self, db_session):
        with pytest.raises(IntegrityError, match="check constraint"):
            _archive_file(db_session, description="ä" * 129)

    def test_file_without_description_is_accepted(self, db_session):
        _archive_file(db_session, description=None)


class TestModelsDeclareTheRules:
    """Alembic does not compare CHECK constraints, so the models are checked here."""

    @staticmethod
    def _checks(model) -> dict[str, str]:
        return {
            c.name: str(c.sqltext)
            for c in model.__table__.constraints
            if c.__class__.__name__ == "CheckConstraint"
        }

    def test_comment_text_is_required_and_bounded(self):
        assert ArchiveFileComment.__table__.c.content.nullable is False
        assert self._checks(ArchiveFileComment)[
            "archive_file_comments_content_check"
        ] == ("char_length(content) BETWEEN 5 AND 1000")

    def test_badge_name_is_required(self):
        assert Badge.__table__.c.name.nullable is False

    def test_description_limits(self):
        expected = "description IS NULL OR char_length(description) <= 128"

        assert self._checks(ArchiveDir)["archive_dirs_description_check"] == expected
        assert self._checks(ArchiveFile)["archive_files_description_check"] == expected
