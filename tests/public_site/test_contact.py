"""Tests for the public contact form endpoint."""

from unittest.mock import patch


class TestContactForm:
    def test_submit_success(self, client, db_session):
        with patch(
            "app.api.router_includes.public_site.send_to_recipients"
        ) as mock_send:
            resp = client.post(
                "/api/public/contact",
                json={
                    "name": "Max Mustermann",
                    "email": "max@example.com",
                    "message": "Hallo, ich interessiere mich für Vindobona II.",
                },
            )
        assert resp.status_code == 202
        assert resp.json() == {"status": "ok"}
        mock_send.assert_called_once()
        _args, kwargs = mock_send.call_args
        assert kwargs["reply_to"] == "max@example.com"

    def test_recipients_are_fixed(self, client, db_session):
        with patch(
            "app.api.router_includes.public_site.send_to_recipients"
        ) as mock_send:
            client.post(
                "/api/public/contact",
                json={
                    "name": "Max",
                    "email": "max@example.com",
                    "message": "Hallo!",
                },
            )
        called_recipients = mock_send.call_args[0][0]
        assert called_recipients == [
            "philchc@vindobona2.at",
            "vindoboneninfo@gmail.com",
        ]

    def test_honeypot_field_rejects_submission(self, client, db_session):
        with patch(
            "app.api.router_includes.public_site.send_to_recipients"
        ) as mock_send:
            resp = client.post(
                "/api/public/contact",
                json={
                    "name": "Bot",
                    "email": "bot@example.com",
                    "message": "spam",
                    "website": "https://spam.example.com",
                },
            )
        assert resp.status_code == 422
        mock_send.assert_not_called()

    def test_invalid_email_rejected(self, client, db_session):
        resp = client.post(
            "/api/public/contact",
            json={
                "name": "Max",
                "email": "not-an-email",
                "message": "Hallo!",
            },
        )
        assert resp.status_code == 422

    def test_empty_message_rejected(self, client, db_session):
        resp = client.post(
            "/api/public/contact",
            json={
                "name": "Max",
                "email": "max@example.com",
                "message": "",
            },
        )
        assert resp.status_code == 422

    def test_no_auth_required(self, client, db_session):
        with patch("app.api.router_includes.public_site.send_to_recipients"):
            resp = client.post(
                "/api/public/contact",
                json={
                    "name": "Max",
                    "email": "max@example.com",
                    "message": "Hallo!",
                },
            )
        assert resp.status_code == 202
