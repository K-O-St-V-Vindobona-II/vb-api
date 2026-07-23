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
from pydantic import BaseModel, ValidationError

from main import _unhandled_exception_handler, _validation_error_handler


class _Payload(BaseModel):
    name: str
    age: int


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(ValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.get("/raise-validation-error")
    def raise_validation_error() -> None:
        _Payload(name="Test", age="not-a-number")

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
