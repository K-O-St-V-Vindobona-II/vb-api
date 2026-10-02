"""The contact form task: hand the mail to the mailer, retry when delivery fails."""

import asyncio
import smtplib
from unittest.mock import Mock

import pytest
from arq import Retry

from app import worker

ARGS = ("Max Mustermann", "max@example.com", "Hallo!")
CONTEXT = {"job_try": 1}


def _run(context: dict[str, object] = CONTEXT):
    return asyncio.run(worker.task_send_contact_form_email(context, *ARGS))


class TestTaskSendContactFormEmail:
    def test_delegates_to_the_mailer_with_the_form_values(self, monkeypatch):
        mock_send = Mock()
        monkeypatch.setattr("app.worker.send_contact_form_email", mock_send)

        _run()

        mock_send.assert_called_once_with(*ARGS)

    @pytest.mark.parametrize(
        "error",
        [smtplib.SMTPServerDisconnected("down"), ConnectionRefusedError("no route")],
    )
    def test_delivery_failure_is_retried(self, monkeypatch, error):
        monkeypatch.setattr(
            "app.worker.send_contact_form_email", Mock(side_effect=error)
        )

        with pytest.raises(Retry):
            _run()

    @pytest.mark.parametrize("job_try", [1, 2, 4])
    def test_delay_before_the_retry_grows_with_every_try(self, monkeypatch, job_try):
        monkeypatch.setattr(
            "app.worker.send_contact_form_email",
            Mock(side_effect=smtplib.SMTPServerDisconnected("down")),
        )

        with pytest.raises(Retry) as info:
            _run({"job_try": job_try})

        assert info.value.defer_score == job_try * 60 * 1000

    def test_configuration_error_is_not_retried(self, monkeypatch):
        monkeypatch.setattr(
            "app.worker.send_contact_form_email",
            Mock(side_effect=RuntimeError("SMTP_HOST is not configured")),
        )

        with pytest.raises(RuntimeError) as info:
            _run()

        assert not isinstance(info.value, Retry)


class TestRegistration:
    def _function(self):
        return next(
            f
            for f in worker.WorkerSettings.functions
            if getattr(f, "name", None) == "task_send_contact_form_email"
        )

    def test_is_registered_with_five_tries(self):
        assert self._function().max_tries == 5

    def test_result_and_arguments_are_not_kept_after_the_job(self):
        assert self._function().keep_result_s == 0
