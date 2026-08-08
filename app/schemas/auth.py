from pydantic import BaseModel, EmailStr, field_validator

from app.core.config import get_settings
from app.schemas.base import StrictInputModel


class StatusMessageResponse(BaseModel):
    """Shared {status, message} shape for the auth endpoints that return a
    plain acknowledgement with a user-facing message - see
    forgot_password/reset_password/unlink_google_account in
    app/api/router_includes/auth.py. Distinct from the plain
    StatusResponse in app/schemas/base.py, which has no message field."""

    status: str
    message: str


class ForgotPasswordRequest(StrictInputModel):
    email: EmailStr


class ResetPasswordRequest(StrictInputModel):
    email: EmailStr
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, v: str) -> str:
        min_length = get_settings().password_min_length
        if len(v) < min_length:
            msg = f"Das Passwort muss mindestens {min_length} Zeichen lang sein."
            raise ValueError(msg)
        return v


class GoogleLoginRequest(StrictInputModel):
    credential: str  # The JWT (id_token) received from Google by the frontend


class GoogleLinkRequest(StrictInputModel):
    credential: str
    email: EmailStr
    password: str
