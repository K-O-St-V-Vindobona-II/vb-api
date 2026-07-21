"""Regression tests for scripts/migration_archive/sqlite2pg.py."""

from sqlalchemy import text

import scripts.migration_archive.sqlite2pg as sqlite2pg
from app.models.member import Member
from app.models.members_log import MembersLog
from app.models.org import Org
from tests.scripts._subprocess_helpers import (
    assert_module_imports_and_configures_mappers,
)


def test_standalone_import_configures_mappers_without_error() -> None:
    """Run as a fresh process (not sharing pytest's conftest-populated
    SQLAlchemy registry) — a plain in-process import can't detect a
    missing `import app.db.base`."""
    assert_module_imports_and_configures_mappers("scripts.migration_archive.sqlite2pg")


def test_fix_known_legacy_data_issues_nulls_parent_id_zero(db_session):
    """Legacy convention: parent_id=0 meant "no parent" — but 0 is never
    a valid member id here, so it must become NULL to satisfy the
    (nullable) self-referencing FK once constraints are enforced again
    (e.g. by pg_restore recreating them from a dump)."""
    # Explicit override, bypassing the ORM default (already fixed to
    # None), to simulate a row migrated from the legacy DB with the old
    # sentinel value. Constraints are disabled around the insert, exactly
    # like the real migration does (SET session_replication_role), since
    # parent_id=0 would otherwise be rejected by the FK before this
    # cleanup function ever gets a chance to run.
    member = Member(email="legacy@vindobona.at", parent_id=0)
    db_session.execute(text("SET LOCAL session_replication_role = 'replica'"))
    db_session.add(member)
    db_session.commit()
    db_session.execute(text("SET LOCAL session_replication_role = 'origin'"))
    member_id = member.id

    sqlite2pg._fix_known_legacy_data_issues(db_session)
    db_session.commit()

    assert db_session.get(Member, member_id).parent_id is None


def test_fix_known_legacy_data_issues_deletes_orphaned_logs(db_session):
    """members_logs rows referencing a member that no longer exists
    (hard-deleted without cascading cleanup) must be removed — the FK
    would otherwise reject them once constraints are enforced again."""
    orphan = MembersLog(member_id=99999, action="update", key="email")
    db_session.execute(text("SET LOCAL session_replication_role = 'replica'"))
    db_session.add(orphan)
    db_session.commit()
    db_session.execute(text("SET LOCAL session_replication_role = 'origin'"))
    orphan_id = orphan.id

    sqlite2pg._fix_known_legacy_data_issues(db_session)
    db_session.commit()

    assert db_session.get(MembersLog, orphan_id) is None


def test_fix_known_legacy_data_issues_keeps_valid_rows(db_session):
    """The cleanup must not touch rows that are already consistent."""
    db_session.add(Org(id="vbn", label="VBN", order=1))
    db_session.commit()

    member = Member(email="valid_parent@vindobona.at", org_id="vbn")
    db_session.add(member)
    db_session.commit()
    db_session.refresh(member)

    child = Member(email="valid_child@vindobona.at", org_id="vbn", parent_id=member.id)
    log = MembersLog(member_id=member.id, action="update", key="email")
    db_session.add_all([child, log])
    db_session.commit()
    child_id, log_id = child.id, log.id

    sqlite2pg._fix_known_legacy_data_issues(db_session)
    db_session.commit()

    assert db_session.get(Member, child_id).parent_id == member.id
    assert db_session.get(MembersLog, log_id) is not None
