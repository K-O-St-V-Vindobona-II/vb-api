"""Free-text fields of the unauthenticated auth requests have an upper bound."""

import pytest
from pydantic import ValidationError

from app.schemas.auth import (
    GoogleLinkRequest,
    GoogleLoginRequest,
    ResetPasswordRequest,
)


class TestRequestBounds:
    def test_reset_token_over_the_bound_is_rejected(self):
        with pytest.raises(ValidationError):
            ResetPasswordRequest(
                email="a@vindobona.at", token="t" * 129, password="long-enough-password"
            )

    def test_genuine_reset_token_is_accepted(self):
        request = ResetPasswordRequest(
            email="a@vindobona.at", token="t" * 43, password="long-enough-password"
        )

        assert len(request.token) == 43

    @pytest.mark.parametrize("model", [GoogleLoginRequest, GoogleLinkRequest])
    def test_google_credential_over_the_bound_is_rejected(self, model):
        extra = {"email": "a@vindobona.at", "password": "pw"}
        fields = extra if model is GoogleLinkRequest else {}

        with pytest.raises(ValidationError):
            model(credential="c" * 4097, **fields)

    def test_link_password_over_the_bound_is_rejected(self):
        with pytest.raises(ValidationError):
            GoogleLinkRequest(
                credential="c", email="a@vindobona.at", password="p" * 1025
            )

    def test_oversized_credential_never_reaches_google(self, client):
        response = client.post("/api/auth/google", json={"credential": "c" * 5000})

        assert response.status_code == 422
