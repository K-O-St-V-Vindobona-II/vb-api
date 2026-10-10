"""Each typed authentication error maps to exactly one response, wherever it
is raised. The routes of this throwaway app only raise."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.error_handlers import register_auth_error_handlers
from app.core.security import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH
from app.services.auth_service import (
    AccountLockedError,
    AccountNotLinkedError,
    AuthenticationFailedError,
    GoogleAuthUnavailableError,
    InvalidResetTokenError,
    InvalidTokenError,
    RefreshRejectedError,
)

ERRORS = {
    "invalid-token": InvalidTokenError("Anmeldedaten ungültig."),
    "failed": AuthenticationFailedError("Ungültiger Google-Token."),
    "refresh": RefreshRejectedError("Token reuse detected"),
    "locked": AccountLockedError("Account is locked"),
    "reset": InvalidResetTokenError("Der Reset-Token ist abgelaufen."),
    "not-linked": AccountNotLinkedError("ACCOUNT_NOT_LINKED"),
    "unavailable": GoogleAuthUnavailableError("Google ist nicht erreichbar."),
}


def _client() -> TestClient:
    app = FastAPI()
    register_auth_error_handlers(app)

    def _raiser(error: Exception):
        def endpoint() -> None:
            raise error

        return endpoint

    for name, error in ERRORS.items():
        app.add_api_route(f"/{name}", _raiser(error))
    return TestClient(app)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return _client()


@pytest.mark.parametrize(
    ("name", "status_code", "detail"),
    [
        ("invalid-token", 401, "Anmeldedaten ungültig."),
        ("failed", 401, "Ungültiger Google-Token."),
        ("refresh", 401, "Session expired or invalid."),
        ("locked", 403, "Account is locked"),
        ("reset", 400, "Der Reset-Token ist abgelaufen."),
        ("not-linked", 404, "ACCOUNT_NOT_LINKED"),
        ("unavailable", 503, "Google ist nicht erreichbar."),
    ],
)
def test_the_error_becomes_its_status_and_detail(client, name, status_code, detail):
    response = client.get(f"/{name}")

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}


def test_only_an_invalid_token_carries_the_bearer_challenge(client):
    challenged = [
        name
        for name in ERRORS
        if client.get(f"/{name}").headers.get("WWW-Authenticate") == "Bearer"
    ]

    assert challenged == ["invalid-token"]


def test_a_rejected_refresh_removes_the_cookie(client):
    set_cookie = client.get("/refresh").headers["set-cookie"]

    assert f"{REFRESH_COOKIE_NAME}=" in set_cookie
    assert f"Path={REFRESH_COOKIE_PATH}" in set_cookie
    assert "Max-Age=0" in set_cookie


def test_the_reason_of_a_rejected_refresh_stays_on_the_server(client):
    assert "reuse" not in client.get("/refresh").text
