"""Tests for scheduled_task_run_service.py — run-history persistence and
querying for app.core.scheduler's job registry.

record_job_run() deliberately opens its own fresh SessionLocal() (see its
docstring) rather than accepting a db parameter — like the mailer's
send_to_recipients(), that means its writes are REAL commits against the
test database, not covered by the per-test transaction rollback the
db_session fixture otherwise relies on (see conftest.py's
_block_all_emails comment for the same issue with emails). Every test that
calls record_job_run() therefore cleans up its own rows via a second,
independent SessionLocal() afterward, so nothing leaks into other tests.
list_job_runs()/get_latest_run_per_job() take `db` as a parameter, so
their tests build fixtures directly via db_session instead, which stays
properly isolated.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.database import SessionLocal
from app.models.scheduled_task_run import ScheduledTaskRun
from app.services.scheduled_task_run_service import (
    get_latest_run_per_job,
    list_job_runs,
    record_job_run,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def _cleanup_real_runs() -> Iterator[None]:
    """Deletes every ScheduledTaskRun row via a real session after the
    test — required for tests that call record_job_run() directly, since
    its commits bypass the db_session fixture's rollback."""
    yield
    db = SessionLocal()
    try:
        db.query(ScheduledTaskRun).delete()
        db.commit()
    finally:
        db.close()


class TestScheduledTaskRunConstraints:
    def test_unknown_job_id_rejected(self, db_session):
        started = datetime.now(UTC)
        db_session.add(
            ScheduledTaskRun(
                job_id="not_a_real_job",
                started_at=started,
                finished_at=started,
                exit_code=0,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_negative_exit_code_rejected(self, db_session):
        started = datetime.now(UTC)
        db_session.add(
            ScheduledTaskRun(
                job_id="cleanup",
                started_at=started,
                finished_at=started,
                exit_code=-1,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_duration_seconds_is_generated(self, db_session):
        started = datetime.now(UTC)
        finished = started + timedelta(seconds=5)
        run = ScheduledTaskRun(
            job_id="cleanup", started_at=started, finished_at=finished, exit_code=0
        )
        db_session.add(run)
        db_session.commit()
        db_session.refresh(run)

        assert round(float(run.duration_seconds)) == 5


@pytest.mark.usefixtures("_cleanup_real_runs")
class TestRecordJobRun:
    def test_inserts_a_row(self):
        started = datetime.now(UTC)

        record_job_run("cleanup", started, exit_code=0, output="5 removed")

        db = SessionLocal()
        try:
            row = (
                db.query(ScheduledTaskRun)
                .filter(ScheduledTaskRun.started_at == started)
                .one()
            )
        finally:
            db.close()
        assert row.job_id == "cleanup"
        assert row.exit_code == 0
        assert row.output == "5 removed"

    def test_truncates_long_output(self):
        started = datetime.now(UTC)

        record_job_run("cleanup", started, exit_code=1, output="x" * 5000)

        db = SessionLocal()
        try:
            row = (
                db.query(ScheduledTaskRun)
                .filter(ScheduledTaskRun.started_at == started)
                .one()
            )
        finally:
            db.close()
        assert len(row.output) == 4000

    def test_never_raises_even_if_persistence_fails(self):
        started = datetime.now(UTC)

        with patch(
            "app.services.scheduled_task_run_service.SessionLocal",
            side_effect=RuntimeError("db unavailable"),
        ):
            record_job_run(
                "cleanup", started, exit_code=0, output="ok"
            )  # must not raise


def _make_run(db, job_id, started_at, *, exit_code=0):
    run = ScheduledTaskRun(
        job_id=job_id,
        started_at=started_at,
        finished_at=started_at,
        exit_code=exit_code,
    )
    db.add(run)
    db.commit()
    return run


class TestListJobRuns:
    def test_filters_by_job_id_and_orders_newest_first(self, db_session):
        base = datetime.now(UTC)
        _make_run(db_session, "cleanup", base)
        _make_run(db_session, "cleanup", base + timedelta(minutes=1))
        _make_run(db_session, "db_backup", base + timedelta(minutes=2))

        result = list_job_runs(db_session, "cleanup", page=1, page_size=25)

        assert result["total"] == 2
        assert [r.started_at for r in result["items"]] == sorted(
            (r.started_at for r in result["items"]), reverse=True
        )
        assert all(r.job_id == "cleanup" for r in result["items"])

    def test_pagination(self, db_session):
        base = datetime.now(UTC)
        for i in range(5):
            _make_run(db_session, "cleanup", base + timedelta(minutes=i))

        page1 = list_job_runs(db_session, "cleanup", page=1, page_size=2)
        page2 = list_job_runs(db_session, "cleanup", page=2, page_size=2)

        assert page1["total"] == 5
        assert len(page1["items"]) == 2
        assert len(page2["items"]) == 2
        assert page1["items"][0].id != page2["items"][0].id


class TestGetLatestRunPerJob:
    def test_returns_only_the_newest_run_per_job(self, db_session):
        base = datetime.now(UTC)
        _make_run(db_session, "cleanup", base, exit_code=1)
        _make_run(db_session, "cleanup", base + timedelta(minutes=5), exit_code=0)
        _make_run(db_session, "db_backup", base, exit_code=0)

        latest = get_latest_run_per_job(db_session)

        assert set(latest.keys()) == {"cleanup", "db_backup"}
        assert latest["cleanup"].exit_code == 0
        assert latest["cleanup"].started_at == base + timedelta(minutes=5)

    def test_empty_when_no_runs_recorded(self, db_session):
        assert get_latest_run_per_job(db_session) == {}

    def test_no_n_plus_one(self, db_session, count_queries):
        base = datetime.now(UTC)
        _make_run(db_session, "cleanup", base)

        with count_queries() as small:
            small_result = get_latest_run_per_job(db_session)

        for job_id in [
            "refresh_category_filter_hits",
            "birthday_mails",
            "debtor_reminder",
            "standesdb_chronicles",
            "archive_health_check",
            "standesdb_health_check",
            "db_backup",
            "downsync",
        ]:
            _make_run(db_session, job_id, base)

        with count_queries() as large:
            large_result = get_latest_run_per_job(db_session)

        assert len(small_result) == 1
        assert len(large_result) == 9
        assert large.count == small.count
