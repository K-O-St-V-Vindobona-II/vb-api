from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import SESSION_IDLE_TIMEOUT_MINUTES
from app.db.database import get_db
from app.models.member import Member
from app.schemas.member import MemberResponse
from app.services.permission_service import calculate_permissions

members_router = APIRouter()


@members_router.get("/me")
def read_current_user(
    current_user: Annotated[Member, Depends(get_current_user)],
) -> MemberResponse:
    """Return the authenticated user's profile including permissions and preferences."""
    return MemberResponse(
        id=current_user.id,
        email=current_user.email,
        vorname=current_user.vorname,
        nachname=current_user.nachname,
        couleurname=current_user.couleurname,
        cn=current_user.cn,
        default_image=current_user.default_image,
        org_id=current_user.org_id or "",
        auth_locked=current_user.auth_locked or False,
        permissions=calculate_permissions(current_user),
        google_linked=current_user.google_linked,
        chroniclemail=current_user.chroniclemail or False,
        session_idle_timeout=SESSION_IDLE_TIMEOUT_MINUTES,
    )


@members_router.patch("/me/chroniclemail")
def toggle_chroniclemail(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, bool]:
    """Toggle the weekly chronicle email subscription for the current user."""
    current_user.chroniclemail = not current_user.chroniclemail
    db.commit()
    return {"chroniclemail": current_user.chroniclemail or False}
