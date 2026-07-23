import base64
from datetime import UTC, date, datetime

from app.models.p4x_account import P4xAccount
from app.models.p4x_transaction import P4xTransaction
from app.services.p4x_service import update_transaction_meta


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(db) -> P4xTransaction:
    account = P4xAccount(
        iban="AT00TEST",
        bic="GIBAATWWXXX",
        label="Test",
        init_date=date(2020, 1, 1),
        init_balance=0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    tx = P4xTransaction(
        sha256_hash="edit_test_tx",
        booking=date(2026, 3, 15),
        valuation=date(2026, 3, 15),
        iban="AT001",
        amount=100.0,
        subject="Test",
        p4x_account_id=account.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


class TestUpdateComment:
    def test_set_comment(self, db_session):
        tx = _seed(db_session)
        update_transaction_meta(
            db_session, tx, "Test Kommentar", None, delete_attachment=False
        )
        db_session.refresh(tx)
        assert tx.comment == "Test Kommentar"

    def test_clear_comment(self, db_session):
        tx = _seed(db_session)
        update_transaction_meta(db_session, tx, "Temp", None, delete_attachment=False)
        update_transaction_meta(db_session, tx, None, None, delete_attachment=False)
        db_session.refresh(tx)
        assert tx.comment is None


class TestUpdateAttachment:
    def test_upload_attachment(self, db_session):
        tx = _seed(db_session)
        pdf_bytes = b"%PDF-1.4 test content"
        update_transaction_meta(
            db_session, tx, None, pdf_bytes, delete_attachment=False
        )
        db_session.refresh(tx)
        assert tx.has_attachment is True

    def test_delete_attachment(self, db_session):
        tx = _seed(db_session)
        update_transaction_meta(db_session, tx, None, b"test", delete_attachment=False)
        assert tx.has_attachment is True

        update_transaction_meta(db_session, tx, None, None, delete_attachment=True)
        db_session.refresh(tx)
        assert tx.has_attachment is False

    def test_upload_ignored_when_already_has_attachment(self, db_session):
        tx = _seed(db_session)
        update_transaction_meta(
            db_session, tx, None, b"original", delete_attachment=False
        )
        original = base64.b64encode(b"original").decode()

        update_transaction_meta(
            db_session, tx, None, b"new content", delete_attachment=False
        )
        db_session.refresh(tx)
        assert tx.attachment == original

    def test_comment_and_attachment_together(self, db_session):
        tx = _seed(db_session)
        update_transaction_meta(
            db_session, tx, "Mit Anlage", b"test pdf", delete_attachment=False
        )
        db_session.refresh(tx)
        assert tx.comment == "Mit Anlage"
        assert tx.has_attachment is True
