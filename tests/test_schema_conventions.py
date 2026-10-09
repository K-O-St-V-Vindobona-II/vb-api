"""The models describe the migrated schema; the schema follows the table rules."""

import warnings
from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import app.db.base  # noqa: F401  (registers every model)
from app.db.database import Base
from app.db.migration_filters import include_object
from app.models.archive_dir import ArchiveDir
from app.models.archive_permission import ArchivePermission
from app.models.contact import Contact
from app.models.enums import ContactType
from app.models.member import Member
from app.models.org import Org
from app.models.state import State
from tests.conftest import engine


def test_models_match_the_migrated_schema():
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_object": include_object,
            },
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # computed columns cannot be compared
            differences = compare_metadata(context, Base.metadata)

    assert differences == []


def test_every_created_at_is_not_null_and_defaults_to_now():
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_name, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'created_at'"
            )
        ).all()

    offenders = [
        name
        for name, nullable, default in rows
        if nullable == "YES" or default is None or "now()" not in default
    ]
    assert rows
    assert offenders == []


def test_row_without_created_at_gets_the_database_time(db_session):
    contact = Contact(name="Ohne Zeitstempel", kontakttyp=ContactType.PERSON)
    db_session.add(contact)
    db_session.commit()
    db_session.refresh(contact)

    assert contact.created_at is not None
    assert abs(datetime.now(UTC) - contact.created_at).total_seconds() < 60


@pytest.mark.parametrize("table", ["members_logs", "contacts_logs"])
def test_a_change_log_entry_without_a_time_gets_the_database_time(db_session, table):
    db_session.execute(
        text(f"INSERT INTO {table} (action, key) VALUES ('update', 'name')")  # noqa: S608
    )

    stored = db_session.execute(text(f"SELECT modified_at FROM {table}")).scalar()  # noqa: S608

    assert stored is not None


class TestContactNames:
    def test_name_of_a_deleted_contact_can_be_used_again(self, db_session):
        old = Contact(name="Muster GmbH", kontakttyp=ContactType.ORGANISATION)
        db_session.add(old)
        db_session.commit()
        old.deleted_at = datetime.now(UTC)
        db_session.commit()

        db_session.add(Contact(name="Muster GmbH", kontakttyp=ContactType.ORGANISATION))
        db_session.commit()

        assert db_session.query(Contact).filter_by(name="Muster GmbH").count() == 2

    def test_active_names_are_unique_regardless_of_case(self, db_session):
        db_session.add(Contact(name="Muster GmbH", kontakttyp=ContactType.ORGANISATION))
        db_session.commit()

        db_session.add(Contact(name="MUSTER gmbh", kontakttyp=ContactType.ORGANISATION))
        with pytest.raises(IntegrityError):
            db_session.commit()


def test_the_model_declares_the_partial_name_index_of_the_database():
    index = next(
        i for i in Contact.__table__.indexes if i.name == "contacts_name_active_key"
    )

    assert index.unique
    assert str(index.dialect_options["postgresql"]["where"]) == "deleted_at IS NULL"


def test_the_model_declares_the_member_rules_of_the_database():
    indexes = {i.name: i for i in Member.__table__.indexes}
    constraints = {c.name for c in Member.__table__.constraints}

    assert indexes["members_email_lower_key"].unique
    assert "members_parent_not_self_check" in constraints


def test_a_directory_permission_pair_is_unique(db_session):
    db_session.add_all(
        [Org(id="vbw", label="V", order=1), State(id="up", label="U", order=1)]
    )
    directory = ArchiveDir(name="Dir1x")
    db_session.add(directory)
    db_session.commit()
    db_session.add(
        ArchivePermission(archive_dir_id=directory.id, org_id="vbw", state_id="up")
    )
    db_session.commit()

    db_session.add(
        ArchivePermission(archive_dir_id=directory.id, org_id="vbw", state_id="up")
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
