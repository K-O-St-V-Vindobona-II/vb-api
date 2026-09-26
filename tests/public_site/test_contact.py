"""Tests for the public contact form endpoint.

The route only queues the mail for the worker and answers at once; the
delivery itself is covered in tests/core/test_mailer_contact_form.py and
tests/test_worker_contact_form.py.
"""

from unittest.mock import patch

from app.core.rate_limit import limiter

URL = "/api/public/contact"
VALID = {
    "name": "Max Mustermann",
    "email": "max@example.com",
    "message": "Hallo, ich interessiere mich für Vindobona II.",
}


class TestContactForm:
    def test_submit_success_queues_exactly_one_job(
        self, client, db_session, mock_arq_pool
    ):
        resp = client.post(URL, json=VALID)

        assert resp.status_code == 202
        assert resp.json() == {"status": "ok"}
        mock_arq_pool.enqueue_job.assert_called_once_with(
            "task_send_contact_form_email",
            VALID["name"],
            VALID["email"],
            VALID["message"],
        )

    def test_nothing_is_sent_inside_the_request(self, client, db_session):
        with (
            patch("app.core.mailer._send_message") as mock_smtp,
            patch("app.core.mailer.send_to_recipients") as mock_send,
        ):
            resp = client.post(URL, json=VALID)

        assert resp.status_code == 202
        mock_smtp.assert_not_called()
        mock_send.assert_not_called()

    def test_honeypot_field_rejects_submission(self, client, db_session, mock_arq_pool):
        resp = client.post(
            URL,
            json={**VALID, "website": "https://spam.example.com"},
        )

        assert resp.status_code == 422
        mock_arq_pool.enqueue_job.assert_not_called()

    def test_invalid_email_rejected(self, client, db_session, mock_arq_pool):
        resp = client.post(URL, json={**VALID, "email": "not-an-email"})

        assert resp.status_code == 422
        mock_arq_pool.enqueue_job.assert_not_called()

    def test_empty_message_rejected(self, client, db_session, mock_arq_pool):
        resp = client.post(URL, json={**VALID, "message": ""})

        assert resp.status_code == 422
        mock_arq_pool.enqueue_job.assert_not_called()

    def test_no_auth_required(self, client, db_session):
        resp = client.post(URL, json=VALID)

        assert resp.status_code == 202

    def test_route_stays_rate_limited(self, client, db_session, mock_arq_pool):
        limiter._storage.reset()

        statuses = [client.post(URL, json=VALID).status_code for _ in range(6)]

        assert statuses[:5] == [202] * 5
        assert statuses[5] == 429
        assert mock_arq_pool.enqueue_job.call_count == 5
        limiter._storage.reset()
