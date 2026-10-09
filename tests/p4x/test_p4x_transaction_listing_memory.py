"""A page of transactions does not load their attachments and raw entries."""

import base64
from datetime import date

from sqlalchemy import inspect

from app.models.p4x_transaction import P4xTransaction
from app.services import p4x_account_service
from tests.p4x.test_p4x_service_queries import _add_tx, _create_account

TWO_MB_PDF = base64.b64encode(b"%PDF" + b"0" * (2 * 1024 * 1024)).decode()


def _account_with_attachments(db_session, n):
    account = _create_account(db_session)
    for i in range(n):
        tx = _add_tx(
            db_session,
            account,
            date(2026, 3, 10),
            10.0,
            iban=f"AT{i}",
            hash_suffix=str(i),
        )
        tx.attachment = TWO_MB_PDF
        tx.raw = "{}"
    db_session.commit()
    db_session.expire_all()
    return account


def test_listing_does_not_load_attachments_or_raw_entries(db_session):
    account = _account_with_attachments(db_session, 12)

    items, _ = p4x_account_service.get_transactions_by_month(
        db_session, account, 2026, 3, 1
    )

    assert len(items) == 12
    for tx in items:
        assert {"attachment", "raw"} <= inspect(tx).unloaded
        assert "attachment" not in tx.__dict__
        assert "raw" not in tx.__dict__


def test_has_attachment_needs_no_attachment_text(db_session):
    account = _account_with_attachments(db_session, 1)
    empty = _add_tx(
        db_session, account, date(2026, 3, 11), 5.0, iban="AT99", hash_suffix="none"
    )
    blank = _add_tx(
        db_session, account, date(2026, 3, 12), 6.0, iban="AT98", hash_suffix="blank"
    )
    blank.attachment = ""
    db_session.commit()
    empty_id, blank_id = empty.id, blank.id
    db_session.expire_all()

    items, _ = p4x_account_service.get_transactions_by_month(
        db_session, account, 2026, 3, 1
    )

    flags = {tx.id: tx.has_attachment for tx in items}
    assert flags[empty_id] is False
    assert flags[blank_id] is False
    assert sorted(flags.values()) == [False, False, True]
    assert all("attachment" not in tx.__dict__ for tx in items)


def test_attachment_and_raw_still_load_on_access(db_session):
    account = _account_with_attachments(db_session, 1)
    tx = db_session.query(P4xTransaction).filter_by(p4x_account_id=account.id).one()

    assert tx.attachment == TWO_MB_PDF
    assert tx.raw == "{}"


def test_has_attachment_follows_a_change_after_a_reload(db_session):
    account = _create_account(db_session)
    tx = _add_tx(db_session, account, date(2026, 3, 10), 10.0)
    assert tx.has_attachment is False

    tx.attachment = TWO_MB_PDF
    db_session.commit()
    db_session.refresh(tx)
    assert tx.has_attachment is True

    tx.attachment = None
    db_session.commit()
    db_session.refresh(tx)
    assert tx.has_attachment is False
