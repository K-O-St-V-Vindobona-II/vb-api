from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.db.database import get_db
from app.schemas.information import PaymentInfoEntry
from app.services.information_service import get_payment_info

information_router = APIRouter()


@information_router.get("/payment")
def get_payment(
    db: Annotated[Session, Depends(get_db)],
    _user: CurrentUser,
) -> list[PaymentInfoEntry]:
    """Return IBAN, BIC, and current fee details for both accounts."""
    return [PaymentInfoEntry.model_validate(entry) for entry in get_payment_info(db)]
