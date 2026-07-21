# vb-api

FastAPI backend for **vb** — the internal management system of Vindobona II / Vindobona nova.

> All related repos live in the [K-O-St-V-Vindobona-II](https://github.com/K-O-St-V-Vindobona-II) GitHub organization.

## Tech Stack

- **Runtime:** Python 3.12, FastAPI, SQLAlchemy (sync), Alembic
- **Database:** PostgreSQL 17
- **Storage:** S3-compatible (MinIO on Dev-VPS, AWS S3 on production)
- **Scheduler:** APScheduler (async)
- **Container:** Podman Quadlets (rootless systemd)

## Development Setup

### Prerequisites

- Podman with the `vb-api` container running (see Quadlet config)
- Python dev dependencies are installed inside the container (`requirements-dev.lock`)

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
same convention against its own ephemeral `postgres:17` service container
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

## Scripts

Operational scripts, re-run on demand as part of regular ops:

| Script | Purpose |
|---|---|
| `scripts/backup_db.py` | Manually trigger a PostgreSQL backup to S3 (`--list`, `--cleanup`) |
| `scripts/restore_db.py` | Restore PostgreSQL from S3 backup (`--list`, `--backup-name`, `--force`) |
| `scripts/check_s3_integrity.py` | Bidirectional DB↔S3 integrity check + orphan report (read-only) |
| `scripts/downsync_prod.py` | Downsync prod AWS S3 (full mirror) → local MinIO, then restore local DB from it (`--dry-run`, `--yes`, `--skip-db`, `--skip-s3`, `--no-delete`) |
| `scripts/trigger_chronicles.py` | Manually trigger the chronicle-mail job for an arbitrary reference date (`--date`, `--send`, `--to`) |

Migration archive (`scripts/migration_archive/`) — one-time tools kept for historical reference, already run in production, no longer part of regular ops:

| Script | Purpose |
|---|---|
| `scripts/migration_archive/migrate_to_s3.py` | One-time local filesystem → S3 migration |
| `scripts/migration_archive/sqlite2pg.py` | One-time SQLite → PostgreSQL migration (legacy) |
| `scripts/migration_archive/migrate_public_gallery.py` | One-time Flickr-hosted gallery → `public_gallery_images` migration |

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
