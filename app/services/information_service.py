from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.models.p4x_account import P4xAccount
from app.services.p4x_service import fee_for_month

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def get_current_fee(db: Session) -> str:
    today = datetime.now(UTC).date().replace(day=1)
    return str(int(fee_for_month(db, today)))


def get_payment_info(db: Session) -> list[dict[str, str | None]]:
    """Return IBAN, BIC, and current fee details for both accounts.

    BIC is nullable on P4xAccount, so a dict value of None (vs. missing
    entirely) is a legitimate, pre-existing possibility here.
    """
    account = db.get(P4xAccount, 1)

    return [
        {
            "title": "Aktivitas",
            "name": "K.Ö.St.V. Vindobona II",
            "iban": "AT74 2011 1007 6518 5563",
            "bic": "GIBAATWWXXX",
            "fee": ("Der Aktivenbeitrag beläuft sich auf EUR 30,- / Semester"),
        },
        {
            "title": "Altherrenschaft",
            "name": ("Katholische, österreichische Studentenverbindung Vindobona II"),
            "iban": account.iban if account else "",
            "bic": account.bic if account else "",
            "fee": (
                f"Der Altherren-Beitrag beläuft sich auf EUR {get_current_fee(db)}"
                " / Monat"
            ),
        },
    ]
