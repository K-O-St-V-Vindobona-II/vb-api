from pydantic import BaseModel, EmailStr, Field, field_validator

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


# A reset token is secrets.token_urlsafe(32) (43 characters); Google ID tokens
# are a few KB at most. The bounds only keep absurd inputs out of the hashing,
# signature and lookup code paths.
_RESET_TOKEN_MAX_LENGTH = 128
_GOOGLE_CREDENTIAL_MAX_LENGTH = 4096
_LINK_PASSWORD_MAX_LENGTH = 1024


class ResetPasswordRequest(StrictInputModel):
    email: EmailStr
    token: str = Field(max_length=_RESET_TOKEN_MAX_LENGTH)
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
    # The JWT (id_token) received from Google by the frontend
    credential: str = Field(max_length=_GOOGLE_CREDENTIAL_MAX_LENGTH)


class GoogleLinkRequest(StrictInputModel):
    credential: str = Field(max_length=_GOOGLE_CREDENTIAL_MAX_LENGTH)
    email: EmailStr
    password: str = Field(max_length=_LINK_PASSWORD_MAX_LENGTH)
