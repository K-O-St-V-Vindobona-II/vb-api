from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.member import Member

information_router = APIRouter()


def _current_fee(db: Session) -> str:
    today = datetime.now(UTC).date().replace(day=1).isoformat()
    row = db.execute(
        text("SELECT fee FROM p4x_fees WHERE start <= :d ORDER BY start DESC LIMIT 1"),
        {"d": today},
    ).fetchone()
    return str(int(row[0])) if row else "0"


@information_router.get("/payment")
def get_payment_info(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(get_current_user)],
) -> list[dict[str, str]]:
    """Return IBAN, BIC, and current fee details for both accounts."""
    acc = db.execute(text("SELECT iban, bic FROM p4x_accounts WHERE id = 1")).fetchone()

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
            "iban": acc[0] if acc else "",
            "bic": acc[1] if acc else "",
            "fee": (
                f"Der Altherren-Beitrag beläuft sich auf EUR {_current_fee(db)} / Monat"
            ),
        },
    ]
