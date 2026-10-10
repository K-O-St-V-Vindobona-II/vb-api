"""Tests for the global exception handlers registered in main.py.

These mount the real handler functions onto a throwaway FastAPI app with
dedicated routes that deliberately raise, rather than exercising the full
app: Starlette's ServerErrorMiddleware re-raises the original exception
under TestClient's default raise_server_exceptions=True regardless of a
registered handler being present, so a handler can only be observed via
raise_server_exceptions=False — and doing that against the full app would
also swallow real bugs in unrelated routes.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError, field_validator

from app.core.config import get_settings
from main import _unhandled_exception_handler, _validation_error_handler
from main import app as real_app

SECURITY_HEADER_NAMES = (
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Cache-Control",
    "Pragma",
    "Expires",
)


class _Payload(BaseModel):
    name: str
    age: int


class _Checked(BaseModel):
    code: str

    @field_validator("code")
    @classmethod
    def _must_be_short(cls, value: str) -> str:
        if len(value) > 3:
            msg = "code is too long"
            raise ValueError(msg)
        return value


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(ValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.get("/raise-validation-error")
    def raise_validation_error() -> None:
        _Payload(name="Test", age="not-a-number")

    @app.get("/raise-validation-error-with-context")
    def raise_validation_error_with_context() -> None:
        _Checked(code="far-too-long-secret-input")

    @app.get("/raise-generic-error")
    def raise_generic_error() -> None:
        msg = "deliberate test failure"
        raise RuntimeError(msg)

    return app


class TestValidationErrorHandler:
    def test_returns_422_with_detail_list(self) -> None:
        client = TestClient(_build_test_app(), raise_server_exceptions=False)
        resp = client.get("/raise-validation-error")
        assert resp.status_code == 422
        data = resp.json()
        assert isinstance(data["detail"], list)
        assert data["detail"][0]["loc"] == ["age"]

    def test_response_is_json_serializable_without_error(self) -> None:
        client = TestClient(_build_test_app(), raise_server_exceptions=False)
        resp = client.get("/raise-validation-error")
        assert resp.headers["content-type"] == "application/json"


class TestUnhandledExceptionHandler:
    def test_returns_500_with_generic_german_detail(self) -> None:
        client = TestClient(_build_test_app(), raise_server_exceptions=False)
        resp = client.get("/raise-generic-error")
        assert resp.status_code == 500
        assert resp.json() == {"detail": "Ein unerwarteter Fehler ist aufgetreten."}

    def test_does_not_leak_exception_message_or_traceback(self) -> None:
        client = TestClient(_build_test_app(), raise_server_exceptions=False)
        resp = client.get("/raise-generic-error")
        assert "deliberate test failure" not in resp.text
        assert "Traceback" not in resp.text

    def test_response_is_json_not_plain_text(self) -> None:
        client = TestClient(_build_test_app(), raise_server_exceptions=False)
        resp = client.get("/raise-generic-error")
        assert resp.headers["content-type"] == "application/json"


class TestValidationErrorIsNotEchoed:
    def test_the_offending_input_is_left_out(self) -> None:
        client = TestClient(_build_test_app(), raise_server_exceptions=False)

        resp = client.get("/raise-validation-error")

        assert "not-a-number" not in resp.text
        assert all("input" not in item for item in resp.json()["detail"])

    def test_context_and_documentation_link_are_left_out(self) -> None:
        client = TestClient(_build_test_app(), raise_server_exceptions=False)

        resp = client.get("/raise-validation-error-with-context")

        assert resp.status_code == 422
        assert "far-too-long-secret-input" not in resp.text
        for item in resp.json()["detail"]:
            assert not {"input", "ctx", "url"} & set(item)

    def test_location_message_and_type_stay_for_the_frontend(self) -> None:
        client = TestClient(_build_test_app(), raise_server_exceptions=False)

        resp = client.get("/raise-validation-error-with-context")

        (item,) = resp.json()["detail"]
        assert item["loc"] == ["code"]
        assert item["msg"] == "Value error, code is too long"
        assert item["type"] == "value_error"


class TestServerErrorKeepsTheSecurityHeaders:
    def test_a_500_carries_every_header_of_a_normal_response(self) -> None:
        normal = TestClient(real_app).get("/")
        failing = TestClient(_build_test_app(), raise_server_exceptions=False).get(
            "/raise-generic-error"
        )

        expected = {name: normal.headers[name] for name in SECURITY_HEADER_NAMES}
        actual = {name: failing.headers.get(name) for name in SECURITY_HEADER_NAMES}
        assert actual == expected

    def test_the_headers_have_their_documented_values(self) -> None:
        failing = TestClient(_build_test_app(), raise_server_exceptions=False).get(
            "/raise-generic-error"
        )

        assert {name: failing.headers[name] for name in SECURITY_HEADER_NAMES} == {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Expires": "0",
        }

    def test_the_body_stays_the_generic_german_message(self) -> None:
        failing = TestClient(_build_test_app(), raise_server_exceptions=False).get(
            "/raise-generic-error"
        )

        assert failing.json() == {"detail": "Ein unerwarteter Fehler ist aufgetreten."}


class TestApiDescription:
    def test_the_documented_token_lifetime_is_the_configured_one(self):
        lifetime = get_settings().session_lifetime_minutes

        assert f"Tokens expire after {lifetime} minutes." in real_app.description
