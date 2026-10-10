from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import get_settings, require_setting
from app.core.datetime_utils import local_today
from app.models.p4x_account import GIROKONTO_ACCOUNT_ID, P4xAccount
from app.services.p4x_fee_balance_service import fee_for_month

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def get_current_fee(db: Session) -> str:
    """The current monthly fee as text: whole euros without decimals, otherwise
    two decimals with a comma (15.50 is "15,50", not "15")."""
    fee = fee_for_month(db, local_today().replace(day=1))
    if fee == fee.to_integral_value():
        return str(int(fee))
    return f"{fee:.2f}".replace(".", ",")


def _get_aktivitas_payment_info() -> dict[str, str | None]:
    """The Aktivitas account and fee text, which come from the settings (the
    Altherrenschaft half comes from the database)."""
    settings = get_settings()
    return {
        "title": "Aktivitas",
        "name": require_setting(
            settings.aktivitas_account_holder, "AKTIVITAS_ACCOUNT_HOLDER"
        ),
        "iban": require_setting(settings.aktivitas_iban, "AKTIVITAS_IBAN"),
        "bic": require_setting(settings.aktivitas_bic, "AKTIVITAS_BIC"),
        "fee": require_setting(settings.aktivitas_fee_text, "AKTIVITAS_FEE_TEXT"),
    }


def get_payment_info(db: Session) -> list[dict[str, str | None]]:
    """Return IBAN, BIC, and current fee details for both accounts.

    BIC is nullable on P4xAccount, so a dict value of None (vs. missing
    entirely) is a legitimate, pre-existing possibility here.
    """
    account = db.get(P4xAccount, GIROKONTO_ACCOUNT_ID)

    return [
        _get_aktivitas_payment_info(),
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
