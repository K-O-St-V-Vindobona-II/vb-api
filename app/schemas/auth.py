import os

from pydantic import EmailStr, field_validator

from app.schemas.base import StrictInputModel


class ForgotPasswordRequest(StrictInputModel):
    email: EmailStr


class ResetPasswordRequest(StrictInputModel):
    email: EmailStr
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, v: str) -> str:
        # Reads the value from .env, fallback is 8
        min_length = int(os.environ.get("PASSWORD_MIN_LENGTH", "8"))
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
