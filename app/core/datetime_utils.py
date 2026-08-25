"""Single source of truth for Settings.app_timezone-based wall-clock time.

Every call site that means "today"/"now" the way a Vienna-based admin or
member would understand it (permission windows, report/month boundaries,
filename dates, form defaults) must go through local_now()/local_today()/
local_day_bounds_utc() here -- never construct
ZoneInfo(get_settings().app_timezone) or datetime.now(UTC) ad hoc for that
purpose. See the 2026-08-15 timezone audit: that ad-hoc pattern is exactly
how a UTC-vs-Vienna 1-2h skew (DST-dependent) ended up baked into role
activation windows and "today" statistics.

DB storage (TIMESTAMPTZ columns) stays UTC regardless of this convention --
see Settings.app_timezone's own docstring in app/core/config.py. The
db_backup/downsync scheduled jobs' own *trigger timing* (which hour they
fire) also stays UTC-anchored on purpose (see BACKUP_HOUR in
app/core/scheduler.py) -- but the *filename* a backup gets once it runs
goes through local_now() like any other human-facing timestamp (see
run_backup() in app/services/backup_service.py), precisely so an admin
reading backup names doesn't have to mentally convert UTC to Vienna time.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def get_app_timezone() -> ZoneInfo:
    """ZoneInfo instance for Settings.app_timezone.

    ZoneInfo(key) is itself cache-keyed by the zoneinfo module (repeated
    calls with the same key return the same cached instance), so no extra
    @lru_cache is needed here -- and none is wanted, since it would need
    its own cache_clear() coupled to get_settings()'s, duplicating that
    machinery for no benefit.
    """
    return ZoneInfo(get_settings().app_timezone)


def local_now() -> datetime:
    """Aware 'now' in Settings.app_timezone (default Europe/Vienna).

    Use for every wall-clock concern a Vienna-based admin/member would
    recognize as "now" (permission windows, report defaults, form
    prefills, log timestamps). Never use datetime.now(UTC) for these --
    the container OS clock runs in UTC, so a UTC "now" silently drifts
    1-2h (DST-dependent) from Vienna wall-clock, exactly the class of bug
    this module exists to prevent.
    """
    return datetime.now(get_app_timezone())


def local_today() -> date:
    """Today's calendar date in Settings.app_timezone. See local_now()."""
    return local_now().date()


def local_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """UTC instant bounds [start, end) of one Vienna-local calendar day.

    For filtering TIMESTAMPTZ columns (e.g. RequestLog.created_at) by a
    Vienna-local day without mislabeling the boundary by 1-2h the way a
    truncated datetime.now(UTC) would. Built from ZoneInfo, not a fixed
    24h timedelta -- Vienna's two annual DST transition days are 23h
    (spring-forward) or 25h (fall-back) long, never exactly 24h.
    """
    tz = get_app_timezone()
    next_day = day + timedelta(days=1)
    start_local = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end_local = datetime.combine(next_day, datetime.min.time(), tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)
