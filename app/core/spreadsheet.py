"""Helpers for workbooks that are built from database text."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openpyxl import Workbook


def neutralize_formulas(workbook: Workbook) -> None:
    """Turn every formula cell of the workbook into a plain text cell.

    openpyxl stores a string that starts with "=" as a formula. The workbooks
    of this application are filled with text that people control (member
    fields, and the payment reference of a bank transfer), and none of them
    contains a formula on purpose, so opening the file must never evaluate
    anything. A string cell keeps the text exactly as it is.
    """
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"
