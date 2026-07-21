#!/usr/bin/env python3
"""Idempotent SQLite -> PostgreSQL migration.

Usage (inside the backend container):
    python scripts/migration_archive/sqlite2pg.py

Or from the host:
    podman exec vb-api python scripts/migration_archive/sqlite2pg.py
"""

import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).parent / ".."))

from sqlalchemy import CursorResult, Table, create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.base  # noqa: F401 — registers all models  # pyright: ignore[reportUnusedImport]
from app.db.database import Base

SQLITE_PATH = "/database/legacy_db.sqlite3"
SQLITE_URL = f"sqlite:///{SQLITE_PATH}"
BATCH_SIZE = 1000


def get_pg_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set.")
        sys.exit(1)
    if "postgresql" not in url:
        print(f"ERROR: DATABASE_URL does not look like PostgreSQL: {url}")
        sys.exit(1)
    return url


def _create_sqlite_engine(path: str) -> Engine:
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_text_factory(  # pyright: ignore[reportUnusedFunction]
        dbapi_conn: sqlite3.Connection, _rec: object
    ) -> None:
        dbapi_conn.text_factory = lambda b: b.decode("utf-8", errors="replace")  # pyright: ignore[reportUnknownLambdaType]

    return engine


def _truncate_tables(pg_session: Session, tables: list[Table]) -> None:
    for table in reversed(tables):
        pg_session.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
    pg_session.commit()
    print("All tables truncated.\n")


def _copy_table_data(
    sqlite_session: Session,
    pg_session: Session,
    tables: list[Table],
) -> int:
    total_rows = 0
    for table in tables:
        rows = sqlite_session.execute(table.select()).fetchall()
        count = len(rows)

        if count == 0:
            print(f"  {table.name}: 0 rows (skip)")
            continue

        columns = [c.name for c in table.columns]
        for i in range(0, count, BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            pg_session.execute(
                table.insert(),
                [dict(zip(columns, row, strict=True)) for row in batch],
            )
        pg_session.commit()

        total_rows += count
        print(f"  {table.name}: {count} rows")
    return total_rows


def _reset_sequences(
    pg_session: Session,
    pg_engine: Engine,
    tables: list[Table],
) -> None:
    print("\nResetting sequences...")
    inspector: Inspector = inspect(pg_engine)
    for table in tables:
        _reset_table_sequence(pg_session, inspector, table)


def _reset_table_sequence(
    pg_session: Session,
    inspector: Inspector,
    table: Table,
) -> None:
    pk = inspector.get_pk_constraint(table.name)
    pk_cols = pk.get("constrained_columns", [])
    if len(pk_cols) != 1:
        return

    pk_col = pk_cols[0]
    col_obj = table.columns.get(pk_col)
    if col_obj is None or not col_obj.autoincrement:
        return
    if not str(col_obj.type).startswith(("INT", "BIG")):
        return

    seq_result = pg_session.execute(
        text(f"SELECT pg_get_serial_sequence('{table.name}', '{pk_col}')")
    ).scalar()
    if not seq_result:
        return

    max_val = pg_session.execute(
        text(f'SELECT COALESCE(MAX("{pk_col}"), 0) FROM "{table.name}"')  # noqa: S608
    ).scalar()
    next_val = (int(max_val) if max_val else 0) + 1
    pg_session.execute(text(f"SELECT setval('{seq_result}', {next_val}, false)"))


def _fix_known_legacy_data_issues(pg_session: Session) -> None:
    """Clean up referential-integrity quirks inherited from the legacy
    schema, which the FK constraints created by Base.metadata.create_all()
    would otherwise reject.

    This migration disables constraint checking during the bulk copy
    (session_replication_role='replica'), so these rows load without
    error here — but the same data later fails when pg_dump/pg_restore
    recreates the constraints from scratch (ALTER TABLE ADD CONSTRAINT
    validates existing rows). Fixing it here, not just once via a manual
    UPDATE/DELETE, matters because this script TRUNCATEs and reloads
    everything from the legacy SQLite on every run — a one-off manual fix
    would be silently wiped out by the next migration run.
    """
    # Legacy convention: parent_id=0 meant "no parent" (a non-nullable FK
    # default in the old schema). Member.parent_id is nullable here, and
    # 0 is never a valid member id, so it must become NULL instead.
    result = cast(
        "CursorResult[Any]",
        pg_session.execute(
            text("UPDATE members SET parent_id = NULL WHERE parent_id = 0")
        ),
    )
    if result.rowcount:
        print(f"  Fixed {result.rowcount} members.parent_id=0 -> NULL")

    # Audit-log rows for members that were hard-deleted from the legacy
    # DB without cascading the cleanup to their log entries. members_logs
    # is a pure leaf table (nothing references it), safe to drop.
    result = cast(
        "CursorResult[Any]",
        pg_session.execute(
            text(
                "DELETE FROM members_logs "
                "WHERE member_id NOT IN (SELECT id FROM members)"
            )
        ),
    )
    if result.rowcount:
        print(f"  Deleted {result.rowcount} orphaned members_logs row(s)")


def migrate() -> None:
    pg_url = get_pg_url()
    print(f"Source:  {SQLITE_URL}")
    print(f"Target:  {pg_url.split('@')[0].rsplit(':', 1)[0]}:***@...")
    print()

    if not Path(SQLITE_PATH).exists():
        print(f"ERROR: SQLite file not found at {SQLITE_PATH}")
        sys.exit(1)

    sqlite_engine = _create_sqlite_engine(SQLITE_PATH)
    pg_engine = create_engine(pg_url)

    # Historical schema source only. Since alembic revision "schema baseline"
    # (REV3), Alembic migrations are the authoritative schema source instead.
    print("Creating tables in PostgreSQL (if not exist)...")
    Base.metadata.create_all(bind=pg_engine)

    tables = list(Base.metadata.sorted_tables)
    print(f"Found {len(tables)} tables to migrate.\n")

    sqlite_session = sessionmaker(bind=sqlite_engine)()
    pg_session = sessionmaker(bind=pg_engine)()

    start = time.time()

    try:
        pg_session.execute(text("SET session_replication_role = 'replica'"))
        _truncate_tables(pg_session, tables)
        total_rows = _copy_table_data(sqlite_session, pg_session, tables)
        _reset_sequences(pg_session, pg_engine, tables)
        pg_session.execute(text("SET session_replication_role = 'origin'"))
        _fix_known_legacy_data_issues(pg_session)
        pg_session.commit()
    except Exception:
        pg_session.rollback()
        raise
    finally:
        sqlite_session.close()
        pg_session.close()

    elapsed = time.time() - start
    print(f"\nDone. {total_rows} rows migrated in {elapsed:.1f}s.")


if __name__ == "__main__":
    migrate()
