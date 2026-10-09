"""An entry set aside before the account's start date comes back when the date
moves earlier."""

import json
from datetime import date
from typing import TYPE_CHECKING

from app.models.p4x_transaction import P4xTransaction
from app.services import p4x_import_service
from app.services.p4x_import_service import GEORGE_BIC, parse_george_json
from tests.p4x.test_p4x_service_queries import _create_account

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.p4x_account import P4xAccount

ENTRY = {
    "booking": "2026-02-10T00:00:00.000+0100",
    "valuation": "2026-02-10",
    "partnerAccount": {"iban": "AT611904300234573201"},
    "amount": {"value": "1500", "precision": "2"},
    "reference": "Beitrag",
}


def _run(db: Session, account: P4xAccount, *entries: object) -> dict[str, int]:
    raw = json.dumps(list(entries))
    parsed = parse_george_json(GEORGE_BIC, raw)
    return p4x_import_service.import_and_apply_filters(
        db, account, parsed.entries, json.loads(raw)
    )


def _move_start(db: Session, account: P4xAccount, start: date) -> None:
    account.init_date = start
    db.commit()


def test_entry_is_restored_not_inserted_twice(db_session: Session) -> None:
    account = _create_account(db_session)
    assert _run(db_session, account, ENTRY)["new"] == 1
    _move_start(db_session, account, date(2026, 4, 1))
    assert _run(db_session, account, ENTRY)["before_init_date"] == 1
    _move_start(db_session, account, date(2015, 1, 1))

    summary = _run(db_session, account, ENTRY)

    assert summary["restored"] == 1
    assert "new" not in summary
    rows = db_session.query(P4xTransaction).all()
    assert len(rows) == 1
    assert rows[0].deleted_at is None


def test_the_comment_of_the_set_aside_entry_comes_back_with_it(
    db_session: Session,
) -> None:
    account = _create_account(db_session)
    _run(db_session, account, ENTRY)
    db_session.query(P4xTransaction).update({"comment": "Mitgliedsbeitrag Maier"})
    db_session.commit()
    _move_start(db_session, account, date(2026, 4, 1))
    _run(db_session, account, ENTRY)
    _move_start(db_session, account, date(2015, 1, 1))

    _run(db_session, account, ENTRY)

    assert db_session.query(P4xTransaction).one().comment == "Mitgliedsbeitrag Maier"


def test_an_entry_that_is_still_before_the_start_stays_set_aside(
    db_session: Session,
) -> None:
    account = _create_account(db_session)
    _run(db_session, account, ENTRY)
    _move_start(db_session, account, date(2026, 4, 1))

    first = _run(db_session, account, ENTRY)
    second = _run(db_session, account, ENTRY)

    assert first["before_init_date"] == second["before_init_date"] == 1
    assert "restored" not in second
    assert db_session.query(P4xTransaction).one().deleted_at is not None


def test_an_active_entry_is_reported_as_existing_and_not_restored(
    db_session: Session,
) -> None:
    account = _create_account(db_session)
    _run(db_session, account, ENTRY)

    summary = _run(db_session, account, ENTRY)

    assert summary["existing"] == 1
    assert "restored" not in summary
