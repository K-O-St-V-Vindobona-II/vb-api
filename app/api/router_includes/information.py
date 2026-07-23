from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.member import Member
from app.services.information_service import get_payment_info

information_router = APIRouter()


@information_router.get("/payment")
def get_payment(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(get_current_user)],
) -> list[dict[str, str | None]]:
    """Return IBAN, BIC, and current fee details for both accounts."""
    return get_payment_info(db)
