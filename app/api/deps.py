from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.member import Member
from app.services import auth_service

# This defines the security scheme.
# It tells FastAPI (and the Swagger UI!) where a user can get a token.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> Member:
    """
    Dependency that extracts the JWT, verifies it cryptographically,
    checks the server-side session, enforces inactivity timeouts,
    and returns the Member object. Every rejection is a typed
    AuthServiceError that app.api.error_handlers turns into the response.
    """
    return auth_service.authenticate_access_token(db, token)


# Routers annotate the signed-in user with this alias instead of importing the
# ORM model themselves.
CurrentUser = Annotated[Member, Depends(get_current_user)]
