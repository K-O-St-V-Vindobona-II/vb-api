"""Regression tests for app/core/logging_config.py's JsonFormatter.

See the 2026-08-15 timezone audit: the "timestamp" field was previously
hardcoded to datetime.now(UTC), inconsistent with the rest of the app
(mailer subject timestamps) and with the log-viewing infrastructure
(vb-deploy/quadlets/logging/logging-dozzle.container sets
TZ=Europe/Vienna) — an operator reading the JSON payload's own timestamp
field would see UTC while everything else around it shows Vienna time.
"""

import json
import logging
import sys

import pytest

from app.core.logging_config import JsonFormatter, setup_logging


def _make_record(message: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )


class TestJsonFormatter:
    def test_basic_fields(self) -> None:
        formatted = JsonFormatter().format(_make_record("hello world"))
        payload = json.loads(formatted)
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"
        assert payload["message"] == "hello world"
        assert "exception" not in payload

    def test_timestamp_uses_configured_app_timezone(self, monkeypatch) -> None:
        # UTC+14 is never equal to UTC, so this reliably fails if the code
        # reverts to datetime.now(UTC).
        monkeypatch.setenv("APP_TIMEZONE", "Pacific/Kiritimati")
        formatted = JsonFormatter().format(_make_record())
        payload = json.loads(formatted)
        assert payload["timestamp"].endswith("+14:00")

    def test_exception_included_when_present(self) -> None:
        try:
            msg = "boom"
            raise ValueError(msg)
        except ValueError:
            record = logging.LogRecord(
                name="test.logger",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=None,
                exc_info=sys.exc_info(),
            )
        payload = json.loads(JsonFormatter().format(record))
        assert "ValueError: boom" in payload["exception"]


@pytest.fixture
def root_logger():
    """The root logger, put back exactly as it was after the test: pytest's
    own capture handlers live on it."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield root
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


class TestSetupLogging:
    def test_existing_handlers_are_replaced_by_one_json_handler(
        self, root_logger: logging.Logger
    ) -> None:
        stale = logging.NullHandler()
        root_logger.addHandler(stale)

        setup_logging()

        assert stale not in root_logger.handlers
        (handler,) = root_logger.handlers
        assert isinstance(handler.formatter, JsonFormatter)
        assert root_logger.level == logging.INFO

    def test_calling_it_twice_does_not_duplicate_the_output(
        self, root_logger: logging.Logger
    ) -> None:
        setup_logging()
        setup_logging()

        assert len(root_logger.handlers) == 1
