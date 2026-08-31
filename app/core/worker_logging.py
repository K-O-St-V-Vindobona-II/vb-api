"""Tags arq's own job-lifecycle log lines with their trigger origin.

arq already distinguishes a cron-triggered job run from an ad-hoc
`enqueue_job()` run in the `ref` value it builds internally for every
job-lifecycle log line (job start/success/retry/abort/failure) — see
`arq.worker.Worker.run_job()`: a cron job's `ref` is the bare function
name, while any other job's `ref` is `<job_id>:<function_name>`. That
distinction is real and stable, but implicit: reading it back out of the
worker's log stream requires knowing arq's own convention.

This filter makes it explicit — it prefixes each of arq's job-lifecycle
log lines with `[scheduled]` or `[triggered]`, purely by checking whether
that `ref` contains a colon, without touching arq's own timing/argument/
result output or any job execution logic.

The filter intentionally matches on the exact, literal message templates
arq uses for those log calls (verified against the pinned arq version)
rather than on argument shape alone — a generic "record has 2+ args"
check also matches arq's own startup banner (`'Starting worker for %d
functions: %s'`), which is not a job-lifecycle line and must stay
untouched. Should a future arq upgrade change these templates, the
filter simply stops matching and log lines are left untagged — it never
raises.

One task in this codebase, `task_downsync`, is registered under the same
name as both a cron job and an ad-hoc `enqueue_job()` target (see its
docstring in app/worker.py). arq resolves a job's dispatch function by
name before deciding how to log it, so *any* run of a name that is
registered as a cron job gets the bare, job-id-less `ref` — even one
triggered manually via `POST /api/system/downsync/trigger`, not by the
scheduler. `TaskOriginLogFilter` can't tell those two apart for that one
task; `describe_job_origin()` below is what `task_downsync()` itself
uses instead, since the *actual* job_id it receives in `ctx` still
carries the distinction that arq's own log line discards.
"""

import logging

_JOB_LIFECYCLE_TEMPLATES: frozenset[str] = frozenset(
    {
        "%6.2fs → %s(%s)%s",
        "%6.2fs ← %s ● %s",
        "%6.2fs ↻ %s retrying job in %0.2fs",
        "%6.2fs ↻ %s cancelled, will be run again",
        "%6.2fs ⊘ %s aborted",
        "%6.2fs ! %s failed, %s: %s",
        "%6.2fs ! %s max retries %d exceeded",
    }
)


class TaskOriginLogFilter(logging.Filter):
    """Prefixes arq job-lifecycle log records with their trigger origin."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg not in _JOB_LIFECYCLE_TEMPLATES:
            return True
        if not isinstance(record.args, tuple) or len(record.args) < 2:
            return True
        ref = record.args[1]
        if not isinstance(ref, str):
            return True
        origin = "triggered" if ":" in ref else "scheduled"
        record.msg = f"[{origin}] {record.msg}"
        return True


def describe_job_origin(job_id: str) -> str:
    """Classifies a raw arq job_id as "scheduled" or "triggered".

    arq's cron dispatch (`arq.worker.Worker.run_cron()`) always builds
    `job_id` as `<cron job name>:<next run in unix ms>` for every entry
    in this codebase (all of them set `unique=True` in
    `build_cron_jobs()`) — a colon is guaranteed present. Every ad-hoc
    `enqueue_job()` call in this codebase omits `_job_id`, so arq falls
    back to a random UUID4 hex string instead (lowercase hex digits
    only, never a colon).
    """
    return "scheduled" if ":" in job_id else "triggered"


def install_task_origin_log_filter() -> None:
    """Attaches TaskOriginLogFilter to arq's job logger, once.

    Idempotent: safe to call more than once (e.g. across test modules
    that import app.worker independently) without stacking duplicate
    prefixes onto the same log records.
    """
    target = logging.getLogger("arq.worker")
    if any(isinstance(f, TaskOriginLogFilter) for f in target.filters):
        return
    target.addFilter(TaskOriginLogFilter())
