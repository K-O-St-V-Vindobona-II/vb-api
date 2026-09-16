"""Regression coverage proving id generation is fully server-side: every
table whose id column declares server_default=text("uuidv7()") must still
produce a valid UUIDv7 even for an INSERT that goes through raw SQL text(),
never through the ORM's mapped class or its Table/Column metadata. This is
stricter than the per-model guard tests (e.g. TestMemberIdUuidDefault):
those go through the ORM, so they would stay green even if a Python-side
default= were ever reintroduced on a mapped_column, silently masking that
regression. A raw text() INSERT cannot see Column.default at all.
"""

import itertools
import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import TextClause, text

import app.db.base  # noqa: F401 - registers every model on Base.metadata
from app.db.database import Base
from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
from app.models.archive_store_item import ArchiveStoreItem
from app.models.member import Member
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_transaction import P4xTransaction
from app.models.state import State

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session


def _make_member(session: Session) -> uuid.UUID:
    member = Member()
    session.add(member)
    session.flush()
    return member.id


def _make_org(session: Session) -> str:
    org = Org(id="uuidv7-test-org")
    session.add(org)
    session.flush()
    return org.id


def _make_state(session: Session) -> str:
    state = State(id="uuidv7-test-state")
    session.add(state)
    session.flush()
    return state.id


def _make_archive_dir(session: Session) -> uuid.UUID:
    archive_dir = ArchiveDir(name="uuidv7-test-dir")
    session.add(archive_dir)
    session.flush()
    return archive_dir.id


def _make_archive_store_item(session: Session) -> uuid.UUID:
    item = ArchiveStoreItem(
        name="uuidv7-test",
        extension="bin",
        mime_type="application/octet-stream",
        size=1,
        sha256_hash="uuidv7-store-item-test",
    )
    session.add(item)
    session.flush()
    return item.id


def _make_archive_file(session: Session) -> uuid.UUID:
    archive_file = ArchiveFile(archive_store_item_id=_make_archive_store_item(session))
    session.add(archive_file)
    session.flush()
    return archive_file.id


# p4x_accounts.iban is unique, and a single test (e.g. p4x_category_filter_hits)
# may need more than one throwaway account, so each call gets its own value.
_iban_sequence = itertools.count()


def _make_p4x_account(session: Session) -> uuid.UUID:
    account = P4xAccount(iban=f"AT{next(_iban_sequence):018d}")
    session.add(account)
    session.flush()
    return account.id


def _make_p4x_category(session: Session) -> uuid.UUID:
    category = P4xCategory(
        name="uuidv7-test",
        label="uuidv7 test",
        background_color="#ffffff",
        text_color="#000000",
    )
    session.add(category)
    session.flush()
    return category.id


def _make_p4x_transaction(session: Session) -> uuid.UUID:
    transaction = P4xTransaction(
        sha256_hash="uuidv7-tx-test",
        booking=date(2026, 1, 1),
        valuation=date(2026, 1, 1),
        iban="AT000000000000000000",
        amount=Decimal(1),
        subject="uuidv7 test",
        p4x_account_id=_make_p4x_account(session),
    )
    session.add(transaction)
    session.flush()
    return transaction.id


def _make_p4x_category_filter(session: Session) -> uuid.UUID:
    category_filter = P4xCategoryFilter(
        name="uuidv7-test",
        p4x_account_id=_make_p4x_account(session),
        subject_mode="contains",
        p4x_category_id=_make_p4x_category(session),
    )
    session.add(category_filter)
    session.flush()
    return category_filter.id


def _replace_seeded_about_tab(session: Session) -> dict[str, str]:
    """public_site_about_tabs is seeded with exactly its 3 fixed slots by
    85e63a22e9a7_add_public_site_content_tables.py and a UNIQUE constraint
    on slot forbids a 4th row, so a plain INSERT can never exercise this
    table's id generation. Deleting one seeded row first (rolled back like
    everything else in this test's transaction) makes room for a
    like-for-like replacement that still proves the server_default."""
    session.execute(text("DELETE FROM public_site_about_tabs WHERE slot = 'anfang'"))
    return {"slot": "'anfang'", "title": "'uuidv7 test'", "body": "'uuidv7 test'"}


def _no_extra_columns(_session: Session) -> dict[str, str]:
    return {}


# Tables with extra NOT NULL columns (plain values or FK/exclusive-arc
# CHECK constraints) that neither a Python nor a server-side default can
# satisfy: minimal values - literal SQL or a parent row created on the fly
# - needed so the INSERT can reach the id column at all. Discovered
# empirically from each table's actual NOT NULL/CHECK constraints.
_EXTRA_COLUMNS: dict[str, Callable[[Session], dict[str, str]]] = {
    "archive_dirs": lambda _s: {"name": "'uuidv7-test'"},
    "archive_files": lambda s: {
        "archive_store_item_id": f"'{_make_archive_store_item(s)}'"
    },
    "archive_file_comments": lambda s: {
        "archive_file_id": f"'{_make_archive_file(s)}'"
    },
    "archive_permissions": lambda s: {
        "archive_dir_id": f"'{_make_archive_dir(s)}'",
        "org_id": f"'{_make_org(s)}'",
        "state_id": f"'{_make_state(s)}'",
    },
    "archive_store_items": lambda _s: {
        "name": "'uuidv7-test'",
        "extension": "'bin'",
        "mime_type": "'application/octet-stream'",
        "size": "1",
        "sha256_hash": "'uuidv7-store-item-direct-test'",
    },
    "sessions": lambda s: {
        "member_id": f"'{_make_member(s)}'",
        "jti": "'uuidv7-test-jti'",
    },
    "client_user_agents": lambda _s: {"string": "'uuidv7-test-ua'"},
    "contacts": lambda _s: {"kontakttyp": "'person'", "name": "'uuidv7-test'"},
    "contacts_logs": lambda _s: {"action": "'create'", "key": "'uuidv7-test'"},
    "member_change_requests": lambda s: {
        "member_id": f"'{_make_member(s)}'",
        "status": "'pending'",
        "proposed_data": '\'{"vorname": "Test"}\'::jsonb',
    },
    "members_logs": lambda _s: {"action": "'create'", "key": "'uuidv7-test'"},
    "members_oauth2bindings": lambda s: {
        "member_id": f"'{_make_member(s)}'",
        "provider": "'google'",
        "remote_id": "'uuidv7-test-remote'",
        "remote_name": "'uuidv7-test-name'",
    },
    "p4x_accounts": lambda _s: {
        "iban": "'AT000000000000000000'",
        "init_balance": "0",
    },
    "p4x_categories": lambda _s: {
        "name": "'uuidv7-test'",
        "label": "'uuidv7 test'",
        "background_color": "'#ffffff'",
        "text_color": "'#000000'",
        "protected": "false",
    },
    "p4x_category_directs": lambda s: {
        "p4x_transaction_id": f"'{_make_p4x_transaction(s)}'",
        "p4x_category_id": f"'{_make_p4x_category(s)}'",
        "amount": "1",
    },
    "p4x_category_filter_hits": lambda s: {
        "p4x_transaction_id": f"'{_make_p4x_transaction(s)}'",
        "p4x_category_filter_id": f"'{_make_p4x_category_filter(s)}'",
    },
    "p4x_category_filters": lambda s: {
        "name": "'uuidv7-test'",
        "p4x_account_id": f"'{_make_p4x_account(s)}'",
        "subject_mode": "'contains'",
        "p4x_category_id": f"'{_make_p4x_category(s)}'",
    },
    "p4x_partners": lambda s: {"member_id": f"'{_make_member(s)}'"},
    "p4x_summary_orders": lambda s: {
        "ordered_by": f"'{_make_member(s)}'",
        "email": "'uuidv7-test@vindobona.at'",
        "summary_start": "'2026-01-01'",
        "summary_end": "'2026-01-31'",
        "finished_ok": "false",
    },
    "p4x_transactions": lambda s: {
        "sha256_hash": "'uuidv7-tx-direct-test'",
        "booking": "'2026-01-01'",
        "valuation": "'2026-01-01'",
        "iban": "'AT000000000000000000'",
        "amount": "1",
        "subject": "'uuidv7 test'",
        "p4x_account_id": f"'{_make_p4x_account(s)}'",
    },
    "public_gallery_images": lambda _s: {
        "sha256_hash": "'uuidv7-gallery-test'",
        "extension": "'jpg'",
        "content_type": "'image/jpeg'",
        "size": "1",
        "width": "1",
        "height": "1",
        "sort_order": "0",
        "is_published": "true",
        "created_at": "now()",
        "updated_at": "now()",
    },
    "public_site_about_tabs": _replace_seeded_about_tab,
    "public_site_programm_hints": lambda _s: {
        "text": "'uuidv7 test'",
        "sort_order": "0",
    },
    "public_site_quotes": lambda _s: {
        "quote": "'uuidv7 test'",
        "author": "'uuidv7 test'",
        "sort_order": "0",
    },
    "public_site_social_links": lambda _s: {
        "platform": "'uuidv7_test'",
        "label": "'uuidv7 test'",
        "url": "'https://example.invalid'",
        "is_enabled": "true",
        "sort_order": "0",
    },
    "request_logs": lambda _s: {
        "client_ip": "'127.0.0.1'",
        "client_ips": "'[]'::jsonb",
        "request_method": "'GET'",
        "request_path": "'/uuidv7-test'",
        "response_status": "200",
        "memory_usage": "0",
    },
    "scheduled_task_runs": lambda _s: {
        "job_id": "'birthday_mails'",
        "started_at": "now()",
        "finished_at": "now()",
        "exit_code": "0",
    },
    "standesdb_images": lambda s: {
        "sha256_hash": "'uuidv7-standesdb-test'",
        "owner_member_id": f"'{_make_member(s)}'",
        '"default"': "false",
    },
}


def _tables_with_uuidv7_id() -> list[str]:
    """Every table whose id column's server_default renders uuidv7()."""
    tables: list[str] = []
    for table in Base.metadata.tables.values():
        id_column = table.columns.get("id")
        if id_column is None or id_column.server_default is None:
            continue
        default_arg = id_column.server_default.arg
        if isinstance(default_arg, TextClause) and "uuidv7" in default_arg.text:
            tables.append(table.name)
    return sorted(tables)


@pytest.mark.parametrize("table_name", _tables_with_uuidv7_id())
def test_id_is_generated_server_side_for_raw_sql_insert(
    db_session: Session, table_name: str
) -> None:
    extra_columns = _EXTRA_COLUMNS.get(table_name, _no_extra_columns)(db_session)
    columns_sql = ", ".join(extra_columns) or "id"
    values_sql = ", ".join(extra_columns.values()) or "DEFAULT"
    inserted_id = db_session.execute(
        text(
            f"INSERT INTO {table_name} ({columns_sql}) "  # noqa: S608
            f"VALUES ({values_sql}) RETURNING id"
        )
    ).scalar_one()

    assert isinstance(inserted_id, uuid.UUID)
    assert inserted_id.version == 7
