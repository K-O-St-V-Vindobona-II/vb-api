"""Entry point for the ARQ worker process.

Runs as its own container, separate from the web container (see
vb-deploy's `vb-api-worker` Quadlet, `Exec=arq app.worker.WorkerSettings`
— arq's CLI resolves this dotted path). Holds every scheduled (cron) job
and ad-hoc background task that used to run inside the web container's
`app/core/scheduler.py`/FastAPI `BackgroundTasks` — split out for
resource isolation from request-serving and for durable, Valkey-backed
execution instead of in-process, fire-and-forget work.

Every `task_*` function below is a thin async wrapper around a plain sync
job body still defined in `app/core/scheduler.py` — job bodies themselves
are unchanged (still open their own `SessionLocal()`, still call
`record_job_run(...)`, still catch a broad `Exception` around their own
body). `asyncio.to_thread()` moves the actual (blocking DB/SMTP/S3/
subprocess) work off arq's event loop, so one slow job never blocks
another job from being picked up concurrently.

`install_task_origin_log_filter()` tags every job-lifecycle log line
arq itself prints with `[scheduled]`/`[triggered]` (see
app/core/worker_logging.py) — installed once here at import time, since
this module is what arq's CLI imports to load `WorkerSettings`.
`task_downsync()` additionally logs its own origin explicitly (see its
own docstring below) — the one task where that filter can't tell the
two apart.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any, ClassVar, cast

from arq import cron
from arq.connections import RedisSettings

# Unlike the web container (main.py imports this too), this process never
# otherwise imports every model module — without it, SQLAlchemy's lazy
# mapper configuration fails the first time a relationship() resolves a
# string-based reference (e.g. Member -> MemberBadge) to a model class
# nothing in this process has imported yet (empirically observed:
# InvalidRequestError, "failed to locate a name").
import app.db.base  # noqa: F401  # pyright: ignore[reportUnusedImport]
from app.core.config import get_settings
from app.core.datetime_utils import get_app_timezone
from app.core.job_schedule_registry import applicable_entries
from app.core.mailer import (
    send_entry_changed_email,
    send_member_change_request_resolved_email,
    send_member_change_request_submitted_email,
    send_own_image_changed_email,
    send_reset_email,
)
from app.core.scheduler import (
    job_archive_health_check,
    job_birthday_mails,
    job_cleanup,
    job_db_backup,
    job_debtor_reminder,
    job_downsync,
    job_refresh_category_filter_hits,
    job_standesdb_chronicles,
    job_standesdb_health_check,
)
from app.core.worker_logging import describe_job_origin, install_task_origin_log_filter

if TYPE_CHECKING:
    from arq.cron import CronJob

install_task_origin_log_filter()

logger = logging.getLogger(__name__)


async def task_health_check(ctx: dict[str, Any]) -> str:  # noqa: ARG001 -- ctx is arq's required task signature
    """Trivial task with no external dependencies, useful to confirm the
    worker is actually processing jobs (e.g. via `enqueue_job` from a
    shell) independent of any real job's business logic."""
    return "ok"


async def task_cleanup(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    await asyncio.to_thread(job_cleanup)


async def task_refresh_category_filter_hits(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    await asyncio.to_thread(job_refresh_category_filter_hits)


async def task_birthday_mails(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    await asyncio.to_thread(job_birthday_mails)


async def task_debtor_reminder(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    await asyncio.to_thread(job_debtor_reminder)


async def task_standesdb_chronicles(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    await asyncio.to_thread(job_standesdb_chronicles)


async def task_archive_health_check(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    await asyncio.to_thread(job_archive_health_check)


async def task_standesdb_health_check(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    await asyncio.to_thread(job_standesdb_health_check)


async def task_db_backup(ctx: dict[str, Any]) -> None:  # noqa: ARG001
    await asyncio.to_thread(job_db_backup)


async def task_downsync(ctx: dict[str, Any]) -> None:
    """Shared by the nightly non-production cron entry (see
    build_cron_jobs()) AND the manual `POST /api/system/downsync/trigger`
    endpoint (`arq_pool.enqueue_job("task_downsync")`) — one execution
    path regardless of how it was triggered.

    Registering the same name as both a cron job and an ad-hoc enqueue
    target means arq's own job-lifecycle log line can't tell the two
    apart here (see app/core/worker_logging.py's module docstring) — it
    always logs a bare, job-id-less ref for this task, cron-triggered or
    not. This function logs its own origin explicitly instead, since
    `ctx["job_id"]` still carries the real distinction.
    """
    logger.info(
        "[%s] task_downsync starting (job_id=%s)",
        describe_job_origin(ctx["job_id"]),
        ctx["job_id"],
    )
    await asyncio.to_thread(job_downsync)


async def task_send_reset_email(ctx: dict[str, Any], email: str, token: str) -> None:  # noqa: ARG001
    await asyncio.to_thread(send_reset_email, email, token)


async def task_send_entry_changed_email(
    ctx: dict[str, Any],  # noqa: ARG001
    to_emails: list[str],
    entry_type: str,
    entry_cn: str,
    diff: dict[str, dict[str, object]],
    *,
    change_type: str,
    modifier_cn: str,
) -> None:
    await asyncio.to_thread(
        send_entry_changed_email,
        to_emails,
        entry_type,
        entry_cn,
        diff,
        change_type=change_type,
        modifier_cn=modifier_cn,
    )


async def task_send_member_change_request_submitted_email(
    ctx: dict[str, Any],  # noqa: ARG001
    to_emails: list[str],
    member_cn: str,
    diff: dict[str, dict[str, object]],
) -> None:
    await asyncio.to_thread(
        send_member_change_request_submitted_email, to_emails, member_cn, diff
    )


async def task_send_member_change_request_resolved_email(
    ctx: dict[str, Any],  # noqa: ARG001
    to_email: str,
    diff: dict[str, dict[str, object]],
    field_decisions: dict[str, str],
) -> None:
    await asyncio.to_thread(
        send_member_change_request_resolved_email, to_email, diff, field_decisions
    )


async def task_send_own_image_changed_email(
    ctx: dict[str, Any],  # noqa: ARG001
    to_emails: list[str],
    member_cn: str,
    action: str,
) -> None:
    await asyncio.to_thread(send_own_image_changed_email, to_emails, member_cn, action)


_JOB_ID_TO_TASK: dict[str, Any] = {
    "cleanup": task_cleanup,
    "refresh_category_filter_hits": task_refresh_category_filter_hits,
    "birthday_mails": task_birthday_mails,
    "debtor_reminder": task_debtor_reminder,
    "standesdb_chronicles": task_standesdb_chronicles,
    "archive_health_check": task_archive_health_check,
    "standesdb_health_check": task_standesdb_health_check,
    "db_backup": task_db_backup,
    "downsync": task_downsync,
}


def build_cron_jobs() -> list[CronJob]:
    """Build arq's real cron entries from the shared, declarative
    JOB_REGISTRY — kept as a plain, directly-testable function (rather
    than baking the result straight into WorkerSettings) so tests can
    exercise the per-stage filtering logic without needing a real arq
    Worker instance, exactly like get_scheduled_jobs() on the web side.
    """
    settings = get_settings()
    entries = applicable_entries(
        app_environment=cast("str", settings.app_environment),
        backup_enabled=settings.backup_enabled,
    )
    return [
        cron(
            _JOB_ID_TO_TASK[entry.id],
            # arq defaults an unset name to "cron:" + the coroutine's
            # __qualname__ — an explicit name matching the task function
            # itself keeps the dispatch key identical to what
            # arq_pool.enqueue_job("task_downsync") uses for the manual
            # trigger endpoint (see task_downsync's own docstring), and
            # keeps every job's name predictable, not just that one.
            name=f"task_{entry.id}",
            day=entry.day,
            weekday=entry.weekday,
            hour=entry.hour,
            minute=entry.minute,
            unique=True,
            max_tries=1,
        )
        for entry in entries
    ]


class WorkerSettings:
    """arq's own settings container, read by dotted path from the CLI."""

    functions: ClassVar[list[object]] = [
        task_health_check,
        task_send_reset_email,
        task_send_entry_changed_email,
        task_send_member_change_request_submitted_email,
        task_send_member_change_request_resolved_email,
        task_send_own_image_changed_email,
    ]
    cron_jobs: ClassVar[list[CronJob]] = build_cron_jobs()
    # One shared, configurable timezone for every cron job (see the
    # migration's design notes) — arq's Worker only supports one global
    # timezone, unlike APScheduler's former per-job override.
    timezone = get_app_timezone()
    # Settings._validate_tier1 already exits the process if valkey_url is
    # unset, so by the time get_settings() returns, it is guaranteed
    # non-None (see app/services/system_service.py's get_app_environment()
    # for the same pattern). Attribute is named redis_settings, not
    # valkey_settings -- arq's own Worker class looks it up by this exact
    # name (wire-compatible, protocol-level API, not ours to rename).
    redis_settings = RedisSettings.from_dsn(cast("str", get_settings().valkey_url))
