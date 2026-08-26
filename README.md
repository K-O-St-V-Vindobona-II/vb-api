# vb-api

FastAPI backend for **vb** — the internal management system of Vindobona II / Vindobona nova.

> All related repos live in the [K-O-St-V-Vindobona-II](https://github.com/K-O-St-V-Vindobona-II) GitHub organization.

## Tech Stack

- **Runtime:** Python 3.12, FastAPI, SQLAlchemy (sync), Alembic
- **Database:** PostgreSQL 18
- **Storage:** S3-compatible (MinIO on Dev-VPS, AWS S3 on production)
- **Scheduler:** APScheduler (async)
- **Container:** Podman Quadlets (rootless systemd)

## Development Setup

### Prerequisites

- Podman with the `vb-api` container running (see Quadlet config)
- Python dev dependencies are installed inside the container (`requirements-dev.lock`)

For the local dev Quadlet/env setup and production/stage prerequisites, see
[`vb-deploy`'s Voraussetzungen](../vb-deploy/README.md#voraussetzungen).

### After cloning

```bash
# Install git hooks — required once per clone, prevents CI failures from formatting mismatches
pre-commit install
```

> **Why this matters:** Without `pre-commit install`, commits bypass ruff formatting and
> type-checking entirely. The CI pipeline runs `ruff format --check`, `pyright`, and `pytest`
> — all of which the pre-commit hooks mirror locally.

### Running tests

The test suite runs exclusively against a real PostgreSQL database, never
SQLite (see `tests/conftest.py`) — this is what lets it actually exercise
DB-native behavior like foreign-key `ON DELETE`/`ON UPDATE` rules, which a
SQLite-backed suite would silently never enforce. The schema is rebuilt from
scratch every session via the real Alembic migrations, so drift between
models and migrations surfaces here too.

**One-time setup** — create a dedicated test database on the same Postgres
server as your Dev database (never point tests at the real `vb` database;
the test session destructively drops and rebuilds its schema):

```bash
podman exec vb-api-pg psql -U vb -c "CREATE DATABASE vb_test OWNER vb;"
```

**Run the suite:**

```bash
podman exec -e TEST_DATABASE_URL=postgresql+psycopg2://vb:<pw>@localhost:5432/vb_test vb-api python -m pytest
```

(`<pw>` is the Postgres password from your `.env`; `localhost:5432` resolves
because `vb-api` and `vb-api-pg` share a Podman pod.) If `TEST_DATABASE_URL`
is unset, `conftest.py` falls back to `DATABASE_URL` — but only accepts it
if it points at an allowlisted test database name (`vb_test`/`test`); any
other target is refused with a loud error, precisely to prevent a
misconfigured `DATABASE_URL` from ever wiping a real database. CI uses the
same convention against its own ephemeral `postgres:18` service container
(database `test`).

### Linting & formatting

```bash
# Check
podman exec vb-api python -m ruff check .
podman exec vb-api python -m ruff format --check .

# Fix in-place
podman exec vb-api python -m ruff check --fix .
podman exec vb-api python -m ruff format .
```

### Type checking

```bash
podman exec vb-api python -m pyright
```

## Environment Variables

Copy `.env.example` and fill in the required values:

```bash
cp .env.example .env
```

For the actual production/stage values (and how they're managed), see
[`vb-deploy`'s Stages](../vb-deploy/README.md#stages).

`APP_ENVIRONMENT` is **required** — the application refuses to start without it:

| Value | Use case |
|---|---|
| `development` | Local / Dev-VPS |
| `test` | Automated test runs |
| `qa` | QA staging |
| `production` | Production VPS |

## Database Migrations

```bash
# Apply pending migrations
podman exec vb-api alembic upgrade head

# Create a new migration
podman exec vb-api alembic revision --autogenerate -m "description"
```

The prod image's `docker-entrypoint.sh` runs `alembic upgrade head` automatically before starting gunicorn, so every container restart (including `podman-auto-update` pulling a new image) applies pending migrations itself — no separate manual step needed there. The manual command above stays relevant for local development (the dev image keeps its plain `uvicorn --reload` command, no auto-migration) and for creating new migrations.

## Scheduler

Background jobs run via a single in-process APScheduler instance (`app/core/scheduler.py`), started once per deployment (a Postgres advisory lock ensures only one gunicorn worker process actually registers/fires jobs, even though every worker boots its own scheduler instance).

### Job registration is gated by `APP_ENVIRONMENT`

- `cleanup` runs in every stage.
- All "business" jobs (mails, reminders, chronicles, health-check reports, category-filter refresh) and `db_backup` only run in `production`.
- `downsync` only runs outside `production` — see below.

| Job ID | Schedule | Stage | Purpose |
|---|---|---|---|
| `cleanup` | hourly | all | Deletes expired sessions, reset tokens, and tracking data past retention. |
| `refresh_category_filter_hits` | daily 07:00 (Vienna) | production | Recomputes which transactions match each P4x category filter. |
| `birthday_mails` | daily 15:53 (Vienna) | production | Sends birthday greetings to VBW members whose birthday is tomorrow. |
| `debtor_reminder` | monthly, 25th 18:32 (Vienna) | production | Sends fee-arrears reminders (debt > 300€). |
| `standesdb_chronicles` | weekly, Tue 17:00 (Vienna) | production | Sends the weekly anniversaries digest (birthdays, admissions, ...). |
| `archive_health_check` | weekly, Tue 01:00 (Vienna) | production | Verifies archive files referenced in the DB exist in S3, reports orphans. |
| `standesdb_health_check` | weekly, Tue 03:00 (Vienna) | production | Same integrity check for Standesdb images. |
| `db_backup` | daily, `BACKUP_HOUR` UTC (default 03:00) | production (+ `BACKUP_ENABLED`) | Dumps PostgreSQL, uploads to S3, deletes backups older than `BACKUP_RETENTION_DAYS`. |
| `downsync` | daily, `DOWNSYNC_HOUR` UTC (`BACKUP_HOUR + 1`, default 04:00) | non-production | See below. |

### Downsync — keeping non-production stages current with prod

The overall idea: **once a day, every non-production stage (dev, test, qa) automatically gets refreshed with real production data** — nobody has to run a script by hand.

`job_downsync()` fires shortly after the production `db_backup` job (default: one hour later, so that day's dump already exists on prod S3) and does, in this fixed order:

1. Mirrors the **entire** production AWS S3 bucket (`archive/`, `standesdb/`, `public/`, `db-backups/` — everything) down into this stage's own S3-compatible storage (e.g. MinIO on the Dev-VPS).
2. Immediately restores the local PostgreSQL database from the now freshly-mirrored `db-backups/` prefix (the latest prod dump) and runs `alembic upgrade head`.

This is the same logic `scripts/downsync_prod.py` already performs for manual/interactive use (see [Scripts](#scripts)) — the shared prod-credential-loading and storage-building code lives in `app/services/downsync_service.py`, used by both the CLI script and the automated job. Production never registers this job at all: it's guarded twice — once via the `APP_ENVIRONMENT` check in `start_scheduler()`, and again inside `job_downsync()` itself as a belt-and-suspenders safety net.

### Inspecting the schedule

`GET /api/system/scheduled-jobs` (requires `systemAdmin`) lists whatever is actually registered on the running instance. Since registration itself is gated per stage, this endpoint always reflects reality — there's no separate "which job applies to which stage" list that could drift out of sync.

## Scripts

Operational scripts, re-run on demand as part of regular ops:

| Script | Purpose |
|---|---|
| `scripts/backup_db.py` | Manually trigger a PostgreSQL backup to S3 (`--list`, `--cleanup`) |
| `scripts/restore_db.py` | Restore PostgreSQL from S3 backup (`--list`, `--backup-name`, `--force`) |
| `scripts/check_s3_integrity.py` | Bidirectional DB↔S3 integrity check + orphan report (read-only) |
| `scripts/downsync_prod.py` | Downsync prod AWS S3 (full mirror) → local MinIO, then restore local DB from it (`--dry-run`, `--yes`, `--skip-db`, `--skip-s3`, `--no-delete`) |
| `scripts/trigger_chronicles.py` | Manually trigger the chronicle-mail job for an arbitrary reference date (`--date`, `--send`, `--to`) |

Full docs (usage, parameters, env vars) for every script: [`scripts/README.md`](scripts/README.md).

## Branching

- `main` — protected, merge via PR only
- `development` — active development branch

## CI/CD

The pipeline (`.github/workflows/ci-cd.yml`) runs on every push to `development` and on PRs to `main`:

1. **Lint & Format** — `ruff check` + `ruff format --check`
2. **Typecheck, Migrations & Test** — `pyright` + `alembic upgrade head` + `pytest --cov`
3. **CodeQL Security Scan**
4. **Build & Push Image** — pushes to `ghcr.io` on release or manual trigger

Production/stage rollout itself happens outside this pipeline: the host's own
`podman-auto-update.timer` picks up the new `:latest` image automatically, or
an operator triggers it immediately via `--tags deploy-api` — see
[`vb-deploy`'s Phase 2 — Tag-2-Betrieb](../vb-deploy/README.md#phase-2--tag-2-betrieb).
