"""Tests for the SecurityHeadersMiddleware registered in main.py.

Uses the real app (via the shared `client` fixture) against the
unauthenticated health-check route, since this middleware runs
unconditionally on every response — no throwaway app needed here, unlike
the exception handlers (see test_exception_handlers.py).
"""


class TestSecurityHeaders:
    def test_sets_content_type_options(self, client):
        resp = client.get("/")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_sets_frame_options(self, client):
        resp = client.get("/")
        assert resp.headers["X-Frame-Options"] == "DENY"

    def test_sets_referrer_policy(self, client):
        resp = client.get("/")
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_sets_no_store_cache_headers(self, client):
        resp = client.get("/")
        assert resp.headers["Cache-Control"] == "no-store"
        assert resp.headers["Pragma"] == "no-cache"
        assert resp.headers["Expires"] == "0"
