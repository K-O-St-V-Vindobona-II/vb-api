"""Alembic revision 5c1d7e9a3b42 (creation times with database defaults, unique
archive permissions, contact names unique among active contacts): it fills
what is missing, refuses data that would break the new name rule, changes
nothing in that case, and can be undone.

The revision functions run inside the per-test transaction, so the schema
changes are rolled back with it. `downgrade()` first puts the schema into the
state before the revision."""

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.models.archive_dir import ArchiveDir
from app.models.org import Org
from app.models.state import State

REVISION_FILE = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/5c1d7e9a3b42_audit_columns_and_uniqueness.py"
)
PERMISSION_CONSTRAINT = "archive_permissions_dir_org_state_key"


def _load_revision():
    spec = importlib.util.spec_from_file_location(
        "audit_columns_revision", REVISION_FILE
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Revision:
    def __init__(self, db_session):
        self._db = db_session
        self._module = _load_revision()

    def _run(self, step):
        self._db.flush()
        connection = self._db.connection()
        with Operations.context(MigrationContext.configure(connection)):
            step()

    def upgrade(self):
        self._run(self._module.upgrade)

    def downgrade(self):
        self._run(self._module.downgrade)

    def audited_tables(self):
        return self._module._AUDITED_TABLES

    def column(self, table, name):
        return self._db.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table "
                "AND column_name = :name"
            ),
            {"table": table, "name": name},
        ).one()

    def has_constraint(self, name):
        return bool(
            self._db.execute(
                text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
                {"name": name},
            ).scalar()
        )

    def has_index(self, name):
        return bool(
            self._db.execute(
                text("SELECT 1 FROM pg_indexes WHERE indexname = :name"), {"name": name}
            ).scalar()
        )


@pytest.fixture
def revision(db_session):
    return _Revision(db_session)


@pytest.fixture
def before(revision):
    """The schema as it was before the revision."""
    revision.downgrade()
    return revision


def _insert_contact(db_session, name, *, created_at=None, deleted=False):
    db_session.execute(
        text(
            "INSERT INTO contacts (kontakttyp, name, created_at, deleted_at) "
            "VALUES ('person', :name, :created_at, CASE WHEN :deleted THEN now() END)"
        ),
        {"name": name, "created_at": created_at, "deleted": deleted},
    )


def _permission_directory(db_session):
    db_session.add_all(
        [Org(id="vbw", label="V", order=1), State(id="up", label="U", order=1)]
    )
    directory = ArchiveDir(name="Dir1x")
    db_session.add(directory)
    db_session.flush()
    return directory.id


def _insert_permission(db_session, directory_id, permission_id, state="up"):
    db_session.execute(
        text(
            "INSERT INTO archive_permissions (id, archive_dir_id, org_id, state_id) "
            "VALUES (:id, :dir, 'vbw', :state)"
        ),
        {"id": permission_id, "dir": directory_id, "state": state},
    )


class TestCreationTimes:
    def test_a_missing_time_is_filled_and_an_existing_one_kept(
        self, db_session, before
    ):
        _insert_contact(db_session, "Ohne Zeit")
        _insert_contact(
            db_session, "Mit Zeit", created_at=datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)
        )

        before.upgrade()

        times = dict(
            db_session.execute(text("SELECT name, created_at FROM contacts")).all()
        )
        assert abs(datetime.now(UTC) - times["Ohne Zeit"]).total_seconds() < 60
        assert times["Mit Zeit"] == datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC)

    def test_every_audited_table_is_not_null_with_a_default_afterwards(self, before):
        before.upgrade()

        for table in before.audited_tables():
            nullable, default = before.column(table, "created_at")
            assert (nullable, default) == ("NO", "now()"), table

    def test_the_change_logs_get_only_a_default(self, before):
        before.upgrade()

        for table in ("contacts_logs", "members_logs"):
            nullable, default = before.column(table, "modified_at")
            assert (nullable, default) == ("YES", "now()"), table

    def test_the_schema_before_had_neither(self, before):
        for table in before.audited_tables():
            assert before.column(table, "created_at") == ("YES", None), table
        assert before.column("members_logs", "modified_at") == ("YES", None)


class TestArchivePermissions:
    def test_duplicates_are_removed_and_the_smallest_id_stays(self, db_session, before):
        directory_id = _permission_directory(db_session)
        ids = sorted(uuid.uuid4() for _ in range(3))
        for permission_id in reversed(ids):
            _insert_permission(db_session, directory_id, permission_id)

        before.upgrade()

        stored = db_session.execute(
            text("SELECT id FROM archive_permissions")
        ).scalars()
        assert list(stored) == [ids[0]]

    def test_different_pairs_are_kept(self, db_session, before):
        directory_id = _permission_directory(db_session)
        db_session.add(State(id="ex", label="E", order=2))
        db_session.flush()
        _insert_permission(db_session, directory_id, uuid.uuid4(), state="up")
        _insert_permission(db_session, directory_id, uuid.uuid4(), state="ex")

        before.upgrade()

        count = db_session.execute(text("SELECT count(*) FROM archive_permissions"))
        assert count.scalar() == 2

    def test_the_pair_is_unique_only_afterwards(self, before):
        assert not before.has_constraint(PERMISSION_CONSTRAINT)

        before.upgrade()

        assert before.has_constraint(PERMISSION_CONSTRAINT)


class TestContactNames:
    def test_two_active_names_that_differ_only_in_case_are_refused(
        self, db_session, before
    ):
        _insert_contact(db_session, "Muster GmbH")
        _insert_contact(db_session, "MUSTER gmbh")

        with pytest.raises(RuntimeError, match="differ only in case") as exc:
            before.upgrade()

        assert "muster gmbh" in str(exc.value)

    def test_nothing_is_changed_when_the_names_are_refused(self, db_session, before):
        _insert_contact(db_session, "Muster GmbH")
        _insert_contact(db_session, "MUSTER gmbh")

        with pytest.raises(RuntimeError):
            before.upgrade()

        assert before.has_constraint("contacts_name_key")
        assert not before.has_index("contacts_name_active_key")
        assert not before.has_constraint(PERMISSION_CONSTRAINT)
        assert before.column("contacts", "created_at") == ("YES", None)

    def test_the_upgrade_runs_once_the_names_are_corrected(self, db_session, before):
        _insert_contact(db_session, "Muster GmbH")
        _insert_contact(db_session, "MUSTER gmbh")
        with pytest.raises(RuntimeError):
            before.upgrade()
        db_session.execute(
            text("UPDATE contacts SET name = 'Anders' WHERE name = 'MUSTER gmbh'")
        )

        before.upgrade()

        assert before.has_index("contacts_name_active_key")

    def test_a_deleted_contact_with_the_same_name_does_not_block_it(
        self, db_session, before
    ):
        _insert_contact(db_session, "Muster GmbH", deleted=True)
        _insert_contact(db_session, "MUSTER gmbh")

        before.upgrade()

        assert before.has_index("contacts_name_active_key")
        assert not before.has_constraint("contacts_name_key")


class TestLocks:
    def test_the_upgrade_waits_at_most_a_few_seconds_for_a_lock(
        self, db_session, before
    ):
        db_session.execute(text("SET LOCAL lock_timeout = 0"))  # the downgrade set it

        before.upgrade()

        timeout = db_session.execute(text("SHOW lock_timeout")).scalar()

        assert timeout == "5s"


class TestDowngrade:
    def test_the_old_rules_come_back(self, before):
        before.upgrade()

        before.downgrade()

        assert before.has_constraint("contacts_name_key")
        assert not before.has_index("contacts_name_active_key")
        assert not before.has_constraint(PERMISSION_CONSTRAINT)
        assert before.column("contacts", "created_at") == ("YES", None)
        assert before.column("contacts_logs", "modified_at") == ("YES", None)
