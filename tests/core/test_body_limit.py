"""Tests for the request body limit middleware (413 before the app reads a body)."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.body_limit import MaxBodySizeMiddleware

UPLOAD_LIMIT = 1000
JSON_LIMIT = 100
HANDLER_CALLS: list[bool] = []


@pytest.fixture
def limited_client() -> TestClient:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/never-read")
    async def never_read() -> dict[str, bool]:
        HANDLER_CALLS.append(True)
        return {"ok": True}

    app.add_middleware(
        MaxBodySizeMiddleware,
        max_upload_bytes=UPLOAD_LIMIT,
        max_json_bytes=JSON_LIMIT,
    )
    return TestClient(app)


class TestDeclaredLength:
    def test_body_within_the_limit_passes(self, limited_client: TestClient) -> None:
        response = limited_client.post("/echo", content=b"x" * JSON_LIMIT)
        assert response.status_code == 200
        assert response.json() == {"size": JSON_LIMIT}

    def test_json_body_above_the_limit_is_rejected(
        self, limited_client: TestClient
    ) -> None:
        response = limited_client.post("/echo", content=b"x" * (JSON_LIMIT + 1))
        assert response.status_code == 413
        assert "detail" in response.json()

    def test_upload_gets_the_larger_limit(self, limited_client: TestClient) -> None:
        headers = {"content-type": "multipart/form-data; boundary=b"}
        allowed = limited_client.post("/echo", content=b"x" * 500, headers=headers)
        assert allowed.status_code == 200
        rejected = limited_client.post(
            "/echo", content=b"x" * (UPLOAD_LIMIT + 1), headers=headers
        )
        assert rejected.status_code == 413

    def test_a_declared_length_above_the_limit_never_reaches_the_handler(
        self, limited_client: TestClient
    ) -> None:
        HANDLER_CALLS.clear()

        # This handler never reads the body, so only the declared length can
        # tell that the request is too large.
        response = limited_client.post("/never-read", content=b"x" * (JSON_LIMIT + 1))

        assert response.status_code == 413
        assert HANDLER_CALLS == []

    def test_requests_without_a_body_are_not_touched(
        self, limited_client: TestClient
    ) -> None:
        assert limited_client.get("/ping").status_code == 200


class TestStreamedBody:
    def test_body_without_content_length_is_counted_while_read(
        self, limited_client: TestClient
    ) -> None:
        def chunks():
            for _ in range(5):
                yield b"x" * 30

        # A generator body is sent chunked: there is no Content-Length to check.
        response = limited_client.post("/echo", content=chunks())
        assert response.status_code == 413

    def test_streamed_body_within_the_limit_passes(
        self, limited_client: TestClient
    ) -> None:
        def chunks():
            for _ in range(3):
                yield b"x" * 30

        response = limited_client.post("/echo", content=chunks())
        assert response.status_code == 200
        assert response.json() == {"size": 90}


class TestRegisteredInTheApplication:
    def test_a_large_json_body_is_refused_by_the_real_app(self, client) -> None:
        body = b'{"email": "' + b"x" * (2 * 1024 * 1024) + b'"}'

        response = client.post(
            "/api/auth/login",
            content=body,
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 413
        assert response.json()["detail"] == "Die Anfrage ist zu groß."
        assert response.headers["x-content-type-options"] == "nosniff"
