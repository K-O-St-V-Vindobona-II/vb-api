# vb-api

[English](README.md) | [Deutsch](README.de.md)

The backend of the Vindobona II system. This README covers what is specific to this repository and assumes you know the [`vb-deploy` README](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.md), the leading document for how the whole system is built, run and restored. All four repositories live in the GitHub organization [K-O-St-V-Vindobona-II](https://github.com/K-O-St-V-Vindobona-II).

## 1. Purpose and naming

`vb-api` provides the **backend**: the JSON API, the database logic and the scheduled jobs of the club's management system (members and the member database "Standesdatenbank", fees and the bookkeeping of the alumni association "AH-Kassen"/`p4x`, the archive, the public website's content, mail notifications). The frontends `vb-intern` (members and officials, login required) and `vb-www` (public website) talk to it; nothing else is needed to use the data.

**Naming convention:** the repository name is the subdomain. `vb-api` is served at **`api.vindobona2.at`** (and `vb-intern` at `intern.vindobona2.at`, `vb-www` at `www.vindobona2.at`). On other stages the domains come from the stage's inventory file in the private repository `vb-access` (see `vb-deploy` [1.3](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.md#13-the-github-organization-and-the-repositories)).

Tech stack: Python 3.14, FastAPI, SQLAlchemy (synchronous), Alembic, PostgreSQL 18, S3 compatible storage (AWS S3 on production, Garage on other stages), ARQ with Valkey for jobs. Interactive API documentation is served at `/docs` on the `development` stage only.

## 2. Containers of this repository

Everything of `vb-api` runs in **one pod, `vb-api-pod`**; its containers share a network namespace and reach each other over `localhost`:

| Container | Image | Runs code of this repository? | Always present? |
|---|---|---|---|
| `vb-api` | `ghcr.io/k-o-st-v-vindobona-ii/vb-api` | **Yes** | yes |
| `vb-api-worker` | same image as `vb-api` | **Yes** | yes |
| `vb-api-pg` | `postgres:18` | no | yes |
| `vb-api-valkey` | `valkey/valkey:8` | no | yes |
| `vb-garage` | `dxflrs/garage` | no | no, **non-production stages only** |

Quadlet definitions, ports, hardening and the exact pod layout are in `vb-deploy` (chapter [1.4](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.md#14-containers-and-pods)).

## 3. What each container does

- **`vb-api`**: serves the API (gunicorn with two uvicorn workers, container port 8000, host port 21000 on `127.0.0.1`, reached through Caddy). On start its entrypoint applies pending database migrations. Health check: the root URL answers.
- **`vb-api-worker`**: runs the scheduled and background jobs with `arq app.worker.WorkerSettings`: the nightly backup, the weekly mails, integrity checks, cleanup and, on other stages, the nightly downsync. It reads the job queue from Valkey. It does **not** migrate (`SKIP_MIGRATIONS=true`).
- **`vb-api-pg`**: the PostgreSQL database. The application connects as the non-superuser role `vb_app`, which owns the database; the bootstrap superuser is used only to create it.
- **`vb-api-valkey`**: the queue the worker takes its jobs from. Persistence is off on purpose; a lost job is a notification mail or a manually repeatable downsync.
- **`vb-garage`**: an S3 stand-in on non-production stages, holding a mirror of the production bucket.

## 4. Where the code of this repository runs

The code is baked into **one container image**, built from the `prod` target of the `Dockerfile` by the CI and published as `ghcr.io/k-o-st-v-vindobona-ii/vb-api:latest` and `...:<full commit sha>` (the image also carries the commit as the label `org.opencontainers.image.revision`). That single image is started twice: as `vb-api` (default command: gunicorn) and as `vb-api-worker` (the Quadlet replaces the command with `arq app.worker.WorkerSettings`). The entrypoint `docker-entrypoint.sh` runs `alembic upgrade head` before starting, unless `SKIP_MIGRATIONS=true`; if the database is at a revision the image does not know (a rollback), it logs a warning and starts without migrating.

The `dev` target of the same `Dockerfile` (`localhost/vb-api:dev`) is what the local development stage runs: the source folder is mounted into the container and `uvicorn --reload` restarts on every change.

Layout of the code, from the outside in; each layer calls only the next:

```
app/api/        routers and dependencies: HTTP only, no business logic, no database access
app/services/   business logic and every database query
app/models/     SQLAlchemy models
app/schemas/    Pydantic request and response models
app/core/       settings, storage, scheduler jobs and the declarative job schedule
app/worker.py   wraps the jobs for ARQ
alembic/        database migrations
scripts/        operational scripts (see scripts/README.md)
```

`scripts/check_router_soc.py` enforces the rule "routers call services only".

---

## Development

### Setup

- Podman with the `vb-api` container running from the local development Quadlets ([`vb-deploy` 5.7](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.md#57-local-development-environment)). Python dev dependencies are installed inside the container (`requirements-dev.lock`).
- Run every command through `podman exec vb-api ...`; never install packages on the host.

```bash
# once per clone: install the git hooks (they mirror the CI checks)
pre-commit install
```

Without the hooks, commits skip ruff formatting, type checking and the tests, and the CI fails later. Three hooks (router separation check, `pyright`, `pytest`) run inside the `vb-api` container, so it must be running when you commit. A further hook refuses `.env*` files (except `.env.example`) and shell history files.

### Tests

The suite runs only against a real PostgreSQL database, never a lighter embedded one (see `tests/conftest.py`): that is what lets it exercise database behavior such as foreign-key `ON DELETE`/`ON UPDATE` rules. The schema is rebuilt from scratch every session through the real Alembic migrations, so drift between models and migrations shows up here too.

**One-time setup:** create a dedicated test database on the same Postgres server (never point tests at the real `vb` database; the test session drops and rebuilds its schema):

```bash
podman exec vb-api-pg psql -U vb -c "CREATE DATABASE vb_test OWNER vb;"
```

**Run the suite** (the helper derives the test database from `DATABASE_URL` and runs the coverage gate afterwards):

```bash
podman exec vb-api sh scripts/run_pytest.sh -q
```

or directly, with an explicit database:

```bash
podman exec -e TEST_DATABASE_URL=postgresql+psycopg2://vb:<pw>@localhost:5432/vb_test vb-api python -m pytest
```

`<pw>` is the Postgres password from your `.env`; `localhost:5432` resolves because `vb-api` and `vb-api-pg` share a pod. If `TEST_DATABASE_URL` is unset, `conftest.py` falls back to `DATABASE_URL`, but accepts it only when it names an allow-listed test database (`vb_test` or `test`); anything else is refused, so a misconfigured URL can never wipe a real database. Run only one pytest session at a time against `vb_test`.

**Coverage gate.** A run writes `coverage.json`. `pytest` enforces a combined floor (`--cov-fail-under`), and `scripts/check_coverage_gate.py` then holds statement, branch and function coverage to separate minimums (`[tool.coverage_gate]` in `pyproject.toml`), so a drop in one cannot hide behind another. New business logic needs tests, and endpoints that return lists or deep relations need a test that limits the number of SQL statements (N+1 protection).

### Linting, formatting and types

```bash
podman exec vb-api python -m ruff check .          # lint
podman exec vb-api python -m ruff format --check . # format check
podman exec vb-api python -m ruff check --fix . && podman exec vb-api python -m ruff format .   # fix in place
podman exec vb-api python -m pyright               # strict type check (app, scripts, main.py)
podman exec vb-api python scripts/check_router_soc.py
```

### Environment variables

Copy `.env.example` to `.env` and fill in the required values. Real stage values live in the private repository `vb-access`; the variables and how to change them are described in `vb-deploy` ([Appendix A](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.md#appendix-a-secrets-and-required-variables)). All settings are typed in `app/core/config.py`; the code never reads the environment directly.

`APP_ENVIRONMENT` is **required**; the application refuses to start without it:

| Value | Use |
|---|---|
| `development` | Local development stage |
| `test` | Automated test runs |
| `qa` | QA stage |
| `production` | Production |

### Database migrations

```bash
# apply pending migrations
podman exec vb-api alembic upgrade head

# create a new migration from the models
podman exec vb-api alembic revision --autogenerate -m "description"
```

The production image applies pending migrations itself on every start (including after `podman auto-update` pulls a new image). The manual command is for the development stage (its image keeps a plain `uvicorn --reload` command without auto-migration) and for creating migrations.

**Rollbacks and compatibility.** The entrypoint also starts an older image against a newer database: it logs a warning and starts without migrating. That is safe only while each migration stays compatible with the previous release. A schema change therefore follows *expand first, contract later*: add the new column, table or constraint in one release (nullable or with a default, old code keeps working), switch the code over, and remove the old structure in a later release. A change that cannot follow this rule needs a note in its migration docstring and a fresh backup (`scripts/backup_db.py`) before the rollout. Only the web container migrates, so there is exactly one migrating process per rollout. To undo a migration: `podman exec vb-api alembic downgrade -1`.

New tables follow the project's database rules: UUID primary keys, foreign keys with explicit `ON DELETE`/`ON UPDATE` and an index, `TIMESTAMPTZ` columns, `created_at`/`updated_at` maintained by the database trigger.

### Dependencies

`requirements.lock` and `requirements-dev.lock` pin every package with hashes and are generated from `requirements.txt` and `requirements-dev.txt`:

```bash
uv pip compile requirements.txt -o requirements.lock --python-version 3.14 --generate-hashes
uv pip compile requirements-dev.txt -o requirements-dev.lock --python-version 3.14 --generate-hashes
```

Add `--upgrade` to both once a month to pick up new versions. The CI job `dependency-audit` runs `pip-audit` over both lock files (on every run and weekly): when it fails, upgrade the affected package with `--upgrade-package <name>` and regenerate the locks; if no fixed version exists yet, accept the advisory with a written reason by adding `--ignore-vuln <ID>` to the job's command. GitHub's dependency graph lists only the unpinned `requirements*.txt` files, not the lock files, so its alerts say little about the pinned versions; this CI job is what covers the Python packages.

### Scheduler

Scheduled jobs run in the `vb-api-worker` container. The job bodies live in `app/core/scheduler.py` (each opens its own database session and records its own outcome); `app/worker.py` wraps each in a thin async task and builds ARQ's cron list from the declarative schedule in `app/core/job_schedule_registry.py`, the single source of truth that both the web and the worker container read, so they can never disagree about what is scheduled where. All jobs share one timezone (`APP_TIMEZONE`, default `Europe/Vienna`).

Registration depends on `APP_ENVIRONMENT`: `cleanup` runs everywhere, the business jobs and `db_backup` only on `production`, `downsync` only outside `production`.

| Job | Schedule | Stage | Purpose |
|---|---|---|---|
| `cleanup` | hourly | all | Deletes expired sessions, reset tokens, old activity logs, sent mails, orphaned user agents. |
| `refresh_category_filter_hits` | daily 07:00 | production | Recomputes which transactions match each category filter. |
| `birthday_mails` | daily 15:53 | production | Birthday greetings to members whose birthday is tomorrow. |
| `debtor_reminder` | 25th, 18:32 | production | Fee arrears reminders (more than 300 EUR). |
| `standesdb_chronicles` | Tuesday 17:00 | production | The weekly anniversaries mail. |
| `archive_health_check` | Tuesday 01:00 | production | Archive files against S3, report to the archive administrators. |
| `standesdb_health_check` | Tuesday 03:00 | production | Member images against S3, report to the administrators. |
| `db_backup` | daily, `BACKUP_HOUR` (03:00) | production | Dump to S3, delete dumps older than `BACKUP_RETENTION_DAYS`. |
| `downsync` | daily, `BACKUP_HOUR` + 1 (04:00) | all except production | Mirror the production bucket, restore the newest dump, `alembic upgrade head`. |

**Downsync** refreshes every non-production stage once a day with real production data: it mirrors the whole production bucket into the stage's Garage and restores the local database from the newest production dump. With `DOWNSYNC_LIGHT=true` it mirrors only the member images, the public gallery and the newest dump (about 50 MB instead of the whole bucket; archive files are then missing on the stage). It shares its code with `scripts/downsync_prod.py` (`app/services/downsync_service.py`), is refused on production at two places, and `POST /api/system/downsync/trigger` (permission `systemAdmin`) starts the same task on demand.

`GET /api/system/scheduled-jobs` (permission `systemAdmin`) returns each job's next run, computed from the declarative schedule with ARQ's own `next_cron()`, and its last recorded run. The worker's log lines are tagged `[scheduled]` or `[triggered]` (`app/core/worker_logging.py`), so a log viewer such as Dozzle shows whether a run came from the cron schedule or from a request.

### Scripts

Operational scripts (backup, restore, integrity check, downsync, archive maintenance, chronicle mail) and the developer helpers are documented in [`scripts/README.md`](scripts/README.md).

### Branching and conventions

- `main` is protected and takes pull requests only. Work happens on `development`.
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `refactor:`, `chore:`, `docs:`, `test:`) and are written in English; so are code comments and docstrings.
- New logic comes with tests (pytest); the CI enforces the coverage gate.

### CI/CD

`.github/workflows/ci-cd.yml` runs on pushes and pull requests to `main`, weekly (Monday 06:00 UTC) and on manual trigger:

1. **Lint and format:** `ruff check`, `ruff format --check`, router separation check.
2. **Typecheck, migrations and tests:** `pyright`, `alembic upgrade head`, `pytest` with coverage and the coverage gate.
3. **CodeQL** security scan.
4. **Dependency audit:** `pip-audit` over both lock files.
5. **Read-only rootfs smoke test:** the production image started with the production hardening.
6. **Build and push the image:** only from `main` (push or manual trigger) and only after every other job passed.

The rollout is not part of the pipeline: the host's `podman-auto-update.timer` picks up the new `:latest` image overnight, or an operator deploys at once with `--tags deploy-api` (see `vb-deploy` [1.7](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.md#17-how-a-change-reaches-production)).
