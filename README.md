# vb-api

FastAPI backend for **vb** — the internal management system of Vindobona II / Vindobona nova.

> All related repos live in the [K-O-St-V-Vindobona-II](https://github.com/K-O-St-V-Vindobona-II) GitHub organization.

## Tech Stack

- **Runtime:** Python 3.14, FastAPI, SQLAlchemy (sync), Alembic
- **Database:** PostgreSQL 18
- **Storage:** S3-compatible (Garage on non-production stages, AWS S3 on production)
- **Scheduler:** ARQ (async worker with cron jobs)
- **Container:** Podman Quadlets (rootless systemd)

## Development Setup

### Prerequisites

- Podman with the `vb-api` container running (see Quadlet config)
- Python dev dependencies are installed inside the container (`requirements-dev.lock`)

For the local dev Quadlet/env setup and production/stage prerequisites, see
[`vb-deploy`'s Prerequisites](../vb-deploy/README.md#prerequisites).

### After cloning

```bash
# Install git hooks — required once per clone, prevents CI failures from formatting mismatches
pre-commit install
```

> **Why this matters:** Without `pre-commit install`, commits bypass ruff formatting and
> type-checking entirely. The CI pipeline runs `ruff format --check`, `pyright`, and `pytest`
> - all of which the pre-commit hooks mirror locally. Three of the hooks (router
> separation-of-concerns check, `pyright`, `pytest`) run inside the `vb-api` container via
> `podman exec`, so it must be running when you commit. A further hook refuses `.env*`
> files (except `.env.example`) and shell history files.

### Running tests

The test suite runs exclusively against a real PostgreSQL database, never
against a lighter embedded database (see `tests/conftest.py`) - this is what
lets it actually exercise DB-native behavior like foreign-key
`ON DELETE`/`ON UPDATE` rules, which a suite on a lighter database would
silently never enforce. The schema is rebuilt from
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

### Rollbacks and compatibility

The entrypoint also starts an older image against a newer database: if the database is at a revision the image does not know (a rollback after a release with a migration), it logs a warning and starts without migrating. That is safe only while each migration stays compatible with the previous release. So a schema change follows *expand first, contract later*: add the new column/table/constraint in one release (nullable or with a default, old code keeps working), switch the code over, and remove the old structure in a later release. A change that cannot follow this rule needs a note in its migration docstring and a fresh backup (`scripts/backup_db.py`) before the rollout.

The worker container runs with `SKIP_MIGRATIONS=true`; only the web container migrates, so there is exactly one migrating process per rollout.

### Dependencies

`requirements.lock` and `requirements-dev.lock` pin every package with hashes and are generated from `requirements.txt` / `requirements-dev.txt`:

```bash
uv pip compile requirements.txt -o requirements.lock --python-version 3.14 --generate-hashes
uv pip compile requirements-dev.txt -o requirements-dev.lock --python-version 3.14 --generate-hashes
```

Add `--upgrade` to both commands once a month to pick up new versions. The `dependency-audit` CI job runs `pip-audit` over both locks (on every run and weekly): when it fails, upgrade the affected package with `--upgrade-package <name>` and regenerate the locks; if no fixed version exists yet, accept the advisory with a written reason by adding `--ignore-vuln <ID>` to the job's command.

## Scheduler

Scheduled (cron) jobs run in a dedicated ARQ worker process — its own container (`vb-api-worker`, see `vb-deploy`), separate from the web container. Job bodies live in `app/core/scheduler.py` (unchanged sync functions — each opens its own `SessionLocal()`, records its own outcome); `app/worker.py` wraps each one in a thin async task and builds arq's real `cron_jobs` list from the declarative schedule in `app/core/job_schedule_registry.py`. Since the worker container is always a single process, there's no cross-worker duplication to guard against (no advisory lock needed, unlike the pre-ARQ, in-web-process design).

All 9 jobs share one configurable timezone (`Settings.app_timezone`, default `Europe/Vienna`) — arq's `Worker` only supports one global timezone, not a per-job override, so `db_backup`/`downsync` are no longer UTC-pinned as they were historically; the `BACKUP_HOUR + 1 = DOWNSYNC_HOUR` relationship stays the same, just computed in the configured timezone instead of UTC.

### Job registration is gated by `APP_ENVIRONMENT`

- `cleanup` runs in every stage.
- All "business" jobs (mails, reminders, chronicles, health-check reports, category-filter refresh) and `db_backup` only run in `production`.
- `downsync` only runs outside `production` — see below.

| Job ID | Schedule | Stage | Purpose |
|---|---|---|---|
| `cleanup` | hourly | all | Deletes expired sessions, reset tokens, and tracking data past retention. |
| `refresh_category_filter_hits` | daily 07:00 | production | Recomputes which transactions match each P4x category filter. |
| `birthday_mails` | daily 15:53 | production | Sends birthday greetings to VBW members whose birthday is tomorrow. |
| `debtor_reminder` | monthly, 25th 18:32 | production | Sends fee-arrears reminders (debt > 300€). |
| `standesdb_chronicles` | weekly, Tue 17:00 | production | Sends the weekly anniversaries digest (birthdays, admissions, ...). |
| `archive_health_check` | weekly, Tue 01:00 | production | Verifies archive files referenced in the DB exist in S3, reports orphans. |
| `standesdb_health_check` | weekly, Tue 03:00 | production | Same integrity check for Standesdb images. |
| `db_backup` | daily, `BACKUP_HOUR` (default 03:00) | production (+ `BACKUP_ENABLED`) | Dumps PostgreSQL, uploads to S3, deletes backups older than `BACKUP_RETENTION_DAYS`. |
| `downsync` | daily, `DOWNSYNC_HOUR` (`BACKUP_HOUR + 1`, default 04:00) | non-production | See below. |

(All times are in the configured `APP_TIMEZONE`.)

### Downsync — keeping non-production stages current with prod

The overall idea: **once a day, every non-production stage (dev, test, qa) automatically gets refreshed with real production data** — nobody has to run a script by hand.

`job_downsync()` fires shortly after the production `db_backup` job (default: one hour later, so that day's dump already exists on prod S3) and does, in this fixed order:

1. Mirrors the **entire** production AWS S3 bucket (`archive/`, `standesdb/`, `public/`, `db-backups/` — everything) down into this stage's own S3-compatible storage (Garage).
2. Immediately restores the local PostgreSQL database from the now freshly-mirrored `db-backups/` prefix (the latest prod dump) and runs `alembic upgrade head`.

This is the same logic `scripts/downsync_prod.py` already performs for manual/interactive use (see [Scripts](#scripts)) — the shared prod-credential-loading and storage-building code lives in `app/services/downsync_service.py`, used by both the CLI script and the automated job. Production never registers this job at all: it's guarded twice — once via the `APP_ENVIRONMENT` check in `build_cron_jobs()`, and again inside `job_downsync()` itself as a belt-and-suspenders safety net. `POST /api/system/downsync/trigger` (requires `systemAdmin`) enqueues the exact same task on demand (`arq_pool.enqueue_job("task_downsync")`), for an immediate re-run outside the nightly schedule.

### Inspecting the schedule

`GET /api/system/scheduled-jobs` (requires `systemAdmin`) computes each applicable job's next run straight from the declarative schedule in `app/core/job_schedule_registry.py` (via arq's own `next_cron()`) — no live worker instance needed for this, so the web and worker containers can never disagree on what's scheduled where.

### Reading the worker container's logs

The `vb-api-worker` container logs every job start/success/retry/failure via arq's own default logging — a cron run and an ad-hoc `enqueue_job()` run (e.g. a mail triggered by a request) otherwise look almost identical there. `app/core/worker_logging.py` tags each of these lines with `[scheduled]` or `[triggered]`, so a container log viewer (e.g. Dozzle) makes the distinction obvious at a glance without needing to know arq's own job-id-prefix convention.

`downsync` is the one exception: since it's registered under the same name as both a cron job and a manual-trigger target (see above), arq itself can no longer tell those two apart in its log line once a name is cron-registered — `task_downsync()` therefore logs its own `[scheduled]`/`[triggered]` line explicitly instead, from the actual job it was given.

## Scripts

Operational scripts, re-run on demand as part of regular ops:

| Script | Purpose |
|---|---|
| `scripts/backup_db.py` | Manually trigger a PostgreSQL backup to S3 (`--list`, `--cleanup`) |
| `scripts/restore_db.py` | Restore PostgreSQL from S3 backup (`--list`, `--backup-name`, `--force`) |
| `scripts/check_s3_integrity.py` | Bidirectional DB↔S3 integrity check + orphan report (read-only) |
| `scripts/downsync_prod.py` | Downsync prod AWS S3 (full mirror) → local Garage, then restore local DB from it (`--dry-run`, `--yes`, `--skip-db`, `--skip-s3`, `--no-delete`) |
| `scripts/trigger_chronicles.py` | Manually trigger the chronicle-mail job for an arbitrary reference date (`--date`, `--send`, `--to`) |
| `scripts/maintain_deleted_archive_files.py` | List, purge (hard-delete) or restore soft-deleted archive files; the only tool that permanently deletes archive data (`list`, `purge`, `purge-duplicates`, `restore`) |

Full docs (usage, parameters, env vars) for every script: [`scripts/README.md`](scripts/README.md).

## Branching

- `main` — protected, merge via PR only
- `development` — active development branch

## CI/CD

The pipeline (`.github/workflows/ci-cd.yml`) runs on pushes and pull requests to `main`, weekly (Monday 06:00 UTC) and on manual trigger:

1. **Lint & Format** - `ruff check` + `ruff format --check` + router separation-of-concerns check
2. **Typecheck, Migrations & Test** — `pyright` + `alembic upgrade head` + `pytest --cov`
3. **CodeQL Security Scan**
4. **Dependency Audit** - `pip-audit` over both lock files
5. **Read-Only Rootfs Smoke Test** - the production image started with the production hardening
6. **Build & Push Image** - pushes to `ghcr.io` only from `main` (push or manual trigger) and only after all other jobs passed

Production/stage rollout itself happens outside this pipeline: the host's own
`podman-auto-update.timer` picks up the new `:latest` image automatically, or
an operator triggers it immediately via `--tags deploy-api` — see
[`vb-deploy`'s Phase 2 — Day-2 Operations](../vb-deploy/README.md#phase-2--day-2-operations).

---

# Deutsch

FastAPI-Backend für **vb** — das interne Verwaltungssystem von Vindobona II / Vindobona nova.

> Alle zugehörigen Repos liegen in der GitHub-Organisation [K-O-St-V-Vindobona-II](https://github.com/K-O-St-V-Vindobona-II).

## Tech-Stack

- **Laufzeitumgebung:** Python 3.14, FastAPI, SQLAlchemy (synchron), Alembic
- **Datenbank:** PostgreSQL 18
- **Storage:** S3-kompatibel (Garage auf Non-Prod-Stages, AWS S3 in Produktion)
- **Scheduler:** ARQ (asynchroner Worker mit Cron-Jobs)
- **Container:** Podman Quadlets (rootless systemd)

## Entwicklungs-Setup

### Voraussetzungen

- Podman mit laufendem `vb-api`-Container (siehe Quadlet-Konfiguration)
- Python-Dev-Abhängigkeiten werden im Container installiert (`requirements-dev.lock`)

Für das lokale Dev-Quadlet-/Env-Setup sowie Production-/Stage-Voraussetzungen
siehe [`vb-deploy`s Voraussetzungen](../vb-deploy/README.md#voraussetzungen).

### Nach dem Klonen

```bash
# Git-Hooks installieren — einmalig pro Klon nötig, verhindert CI-Fehlschläge durch Formatierungs-Abweichungen
pre-commit install
```

> **Warum das wichtig ist:** Ohne `pre-commit install` umgehen Commits die
> Ruff-Formatierung und Typprüfung komplett. Die CI-Pipeline führt `ruff format --check`,
> `pyright` und `pytest` aus — alle davon werden lokal von den Pre-Commit-Hooks gespiegelt.
> Drei der Hooks (Router-Schichtentrennungs-Check, `pyright`, `pytest`) laufen per `podman exec`
> im `vb-api`-Container, der beim Commit also laufen muss. Ein weiterer Hook weist `.env*`-Dateien
> (außer `.env.example`) und Shell-History-Dateien ab.

### Tests ausführen

Die Testsuite läuft ausschließlich gegen eine echte PostgreSQL-Datenbank,
niemals gegen eine leichtere eingebettete Datenbank (siehe
`tests/conftest.py`) - nur so lässt sich DB-natives Verhalten wie
Foreign-Key-`ON DELETE`/`ON UPDATE`-Regeln tatsächlich prüfen, was eine Suite
auf einer leichteren Datenbank stillschweigend nie durchsetzen würde. Das Schema wird bei jeder Session von Grund auf über die
echten Alembic-Migrationen neu aufgebaut, sodass auch Drift zwischen
Modellen und Migrationen hier sichtbar wird.

**Einmalige Einrichtung** — eine eigene Testdatenbank auf demselben
Postgres-Server wie die Dev-Datenbank anlegen (Tests niemals gegen die
echte `vb`-Datenbank laufen lassen; die Testsession löscht und baut ihr
Schema destruktiv neu auf):

```bash
podman exec vb-api-pg psql -U vb -c "CREATE DATABASE vb_test OWNER vb;"
```

**Suite ausführen:**

```bash
podman exec -e TEST_DATABASE_URL=postgresql+psycopg2://vb:<pw>@localhost:5432/vb_test vb-api python -m pytest
```

(`<pw>` ist das Postgres-Passwort aus der eigenen `.env`; `localhost:5432`
funktioniert, weil sich `vb-api` und `vb-api-pg` einen Podman-Pod teilen.)
Ist `TEST_DATABASE_URL` nicht gesetzt, fällt `conftest.py` auf
`DATABASE_URL` zurück — akzeptiert diese aber nur, wenn sie auf einen
erlaubten Testdatenbanknamen zeigt (`vb_test`/`test`); jedes andere Ziel
wird mit einem lauten Fehler verweigert, genau um zu verhindern, dass eine
falsch konfigurierte `DATABASE_URL` je eine echte Datenbank löscht. CI
verwendet dieselbe Konvention gegen seinen eigenen, ephemeren
`postgres:18`-Service-Container (Datenbank `test`).

### Linting & Formatierung

```bash
# Prüfen
podman exec vb-api python -m ruff check .
podman exec vb-api python -m ruff format --check .

# Direkt beheben
podman exec vb-api python -m ruff check --fix .
podman exec vb-api python -m ruff format .
```

### Typprüfung

```bash
podman exec vb-api python -m pyright
```

## Umgebungsvariablen

`.env.example` kopieren und die nötigen Werte eintragen:

```bash
cp .env.example .env
```

Für die echten Production-/Stage-Werte (und wie sie verwaltet werden) siehe
[`vb-deploy`s Stages](../vb-deploy/README.md#stages-1).

`APP_ENVIRONMENT` ist **Pflicht** — die Anwendung startet ohne diese Variable nicht:

| Wert | Anwendungsfall |
|---|---|
| `development` | Lokal / Dev-VPS |
| `test` | Automatisierte Testläufe |
| `qa` | QA-Staging |
| `production` | Production-VPS |

## Datenbank-Migrationen

```bash
# Ausstehende Migrationen anwenden
podman exec vb-api alembic upgrade head

# Neue Migration erzeugen
podman exec vb-api alembic revision --autogenerate -m "Beschreibung"
```

`docker-entrypoint.sh` des Prod-Images führt `alembic upgrade head`
automatisch vor dem Start von gunicorn aus, sodass jeder Container-Neustart
(auch wenn `podman-auto-update` ein neues Image zieht) ausstehende
Migrationen selbst anwendet — dort ist kein separater manueller Schritt
nötig. Der obige manuelle Befehl bleibt relevant für die lokale Entwicklung
(das Dev-Image behält seinen einfachen `uvicorn --reload`-Befehl, keine
Auto-Migration) sowie für das Erzeugen neuer Migrationen.

### Rollbacks und Kompatibilität

Der Entrypoint startet auch ein älteres Image gegen eine neuere Datenbank: steht die Datenbank auf einer Revision, die das Image nicht kennt (Rollback nach einem Release mit Migration), loggt er eine Warnung und startet ohne zu migrieren. Das ist nur sicher, solange jede Migration zum vorherigen Release kompatibel bleibt. Eine Schemaänderung folgt daher *erst erweitern, später aufräumen*: Spalte/Tabelle/Constraint in einem Release hinzufügen (nullable oder mit Default, der alte Code funktioniert weiter), den Code umstellen und die alte Struktur in einem späteren Release entfernen. Eine Änderung, die dieser Regel nicht folgen kann, braucht einen Hinweis im Docstring der Migration und ein frisches Backup (`scripts/backup_db.py`) vor dem Rollout.

Der Worker-Container läuft mit `SKIP_MIGRATIONS=true`; nur der Web-Container migriert, pro Rollout gibt es also genau einen migrierenden Prozess.

### Abhängigkeiten

`requirements.lock` und `requirements-dev.lock` pinnen jedes Paket mit Hashes und werden aus `requirements.txt` / `requirements-dev.txt` erzeugt:

```bash
uv pip compile requirements.txt -o requirements.lock --python-version 3.14 --generate-hashes
uv pip compile requirements-dev.txt -o requirements-dev.lock --python-version 3.14 --generate-hashes
```

Einmal im Monat `--upgrade` an beide Befehle hängen, um neue Versionen zu übernehmen. Der CI-Job `dependency-audit` führt `pip-audit` über beide Locks aus (bei jedem Lauf und wöchentlich): schlägt er fehl, das betroffene Paket mit `--upgrade-package <name>` aktualisieren und die Locks neu erzeugen; gibt es noch keine behobene Version, die Meldung mit schriftlicher Begründung akzeptieren, indem `--ignore-vuln <ID>` an den Befehl des Jobs angehängt wird.

## Scheduler

Geplante (Cron-)Jobs laufen in einem eigenen ARQ-Worker-Prozess — einem
eigenen Container (`vb-api-worker`, siehe `vb-deploy`), getrennt vom
Web-Container. Die Job-Bodies liegen weiterhin in `app/core/scheduler.py`
(unveränderte synchrone Funktionen — jede öffnet ihre eigene
`SessionLocal()`, protokolliert ihr eigenes Ergebnis); `app/worker.py`
verpackt jede davon in einen dünnen Async-Task und baut arqs echte
`cron_jobs`-Liste aus dem deklarativen Zeitplan in
`app/core/job_schedule_registry.py`. Da der Worker-Container immer nur
ein einzelner Prozess ist, gibt es keine Mehrfach-Registrierung zu
verhindern (kein Advisory-Lock mehr nötig, anders als im früheren,
im Web-Prozess laufenden Design).

Alle 9 Jobs teilen sich eine konfigurierbare Zeitzone
(`Settings.app_timezone`, Standard `Europe/Vienna`) — arqs `Worker`
unterstützt nur eine globale Zeitzone, kein Per-Job-Override, deshalb
sind `db_backup`/`downsync` nicht mehr wie historisch auf UTC gepinnt;
die Beziehung `BACKUP_HOUR + 1 = DOWNSYNC_HOUR` bleibt gleich, wird nur
jetzt in der konfigurierten Zeitzone statt in UTC berechnet.

### Job-Registrierung ist über `APP_ENVIRONMENT` gesteuert

- `cleanup` läuft in jeder Stage.
- Alle "Business"-Jobs (Mails, Erinnerungen, Chroniken, Health-Check-Berichte,
  Kategoriefilter-Refresh) sowie `db_backup` laufen nur in `production`.
- `downsync` läuft nur außerhalb von `production` — siehe unten.

| Job-ID | Zeitplan | Stage | Zweck |
|---|---|---|---|
| `cleanup` | stündlich | alle | Löscht abgelaufene Sessions, Reset-Tokens und Tracking-Daten nach Ablauf der Aufbewahrungsfrist. |
| `refresh_category_filter_hits` | täglich 07:00 | production | Berechnet neu, welche Transaktionen auf welchen P4x-Kategoriefilter passen. |
| `birthday_mails` | täglich 15:53 | production | Sendet Geburtstagsgrüße an VBW-Mitglieder, die morgen Geburtstag haben. |
| `debtor_reminder` | monatlich, 25. um 18:32 | production | Sendet Beitragsrückstands-Erinnerungen (Schuld > 300 €). |
| `standesdb_chronicles` | wöchentlich, Di 17:00 | production | Sendet die wöchentliche Jubiläums-Übersicht (Geburtstage, Aufnahmen, ...). |
| `archive_health_check` | wöchentlich, Di 01:00 | production | Prüft, ob in der DB referenzierte Archiv-Dateien in S3 existieren, meldet Waisen. |
| `standesdb_health_check` | wöchentlich, Di 03:00 | production | Derselbe Integritätscheck für Standesdb-Bilder. |
| `db_backup` | täglich, `BACKUP_HOUR` (Standard 03:00) | production (+ `BACKUP_ENABLED`) | Erstellt einen PostgreSQL-Dump, lädt ihn nach S3 hoch, löscht Backups älter als `BACKUP_RETENTION_DAYS`. |
| `downsync` | täglich, `DOWNSYNC_HOUR` (`BACKUP_HOUR + 1`, Standard 04:00) | non-production | Siehe unten. |

(Alle Uhrzeiten in der konfigurierten `APP_TIMEZONE`.)

### Downsync — Non-Production-Stages aktuell mit Prod halten

Die Grundidee: **jede Non-Production-Stage (Dev, Test, QA) wird einmal
täglich automatisch mit echten Produktionsdaten aufgefrischt** — niemand
muss dafür manuell ein Skript starten.

`job_downsync()` läuft kurz nach dem Production-`db_backup`-Job (Standard:
eine Stunde später, damit der Dump des Tages auf dem Prod-S3 bereits
existiert) und macht in dieser festen Reihenfolge:

1. Spiegelt den **gesamten** Production-AWS-S3-Bucket (`archive/`,
   `standesdb/`, `public/`, `db-backups/` — alles) auf den eigenen
   S3-kompatiblen Storage dieser Stage herunter (Garage).
2. Stellt sofort die lokale PostgreSQL-Datenbank aus dem frisch
   gespiegelten `db-backups/`-Präfix wieder her (der neueste Prod-Dump) und
   führt `alembic upgrade head` aus.

Das ist dieselbe Logik, die `scripts/downsync_prod.py` bereits für den
manuellen/interaktiven Gebrauch ausführt (siehe [Skripte](#skripte)) — der
gemeinsame Code zum Laden der Prod-Credentials und zum Aufbau des Storage
liegt in `app/services/downsync_service.py`, genutzt sowohl vom CLI-Skript
als auch vom automatisierten Job. Production registriert diesen Job nie:
Er ist doppelt abgesichert — einmal über den `APP_ENVIRONMENT`-Check in
`build_cron_jobs()`, und zusätzlich innerhalb von `job_downsync()` selbst
als zusätzliches Sicherheitsnetz. `POST /api/system/downsync/trigger`
(erfordert `systemAdmin`) stößt denselben Task auf Zuruf an
(`arq_pool.enqueue_job("task_downsync")`), für einen sofortigen erneuten
Lauf außerhalb des nächtlichen Zeitplans.

### Zeitplan einsehen

`GET /api/system/scheduled-jobs` (erfordert `systemAdmin`) berechnet für
jeden anwendbaren Job den nächsten Lauf direkt aus dem deklarativen
Zeitplan in `app/core/job_schedule_registry.py` (über arqs eigenes
`next_cron()`) — dafür ist keine laufende Worker-Instanz nötig, Web- und
Worker-Container können also nie uneinig darüber sein, was wo geplant ist.

### Logs des Worker-Containers lesen

Der `vb-api-worker`-Container loggt jeden Job-Start/-Erfolg/-Retry/
-Fehlschlag über arqs eigenes Default-Logging — ein Cron-Lauf und ein
per `enqueue_job()` angestoßener Ad-hoc-Lauf (z. B. eine durch einen
Request ausgelöste Mail) sehen dort sonst fast identisch aus.
`app/core/worker_logging.py` markiert jede dieser Zeilen mit
`[scheduled]` bzw. `[triggered]`, sodass ein Container-Log-Viewer (z. B.
Dozzle) die Unterscheidung auf einen Blick zeigt, ohne dass man arqs
eigene Job-ID-Präfix-Konvention kennen muss.

`downsync` ist die eine Ausnahme: Da er unter demselben Namen sowohl als
Cron-Job als auch als manuelles Trigger-Ziel registriert ist (siehe
oben), kann arq selbst die beiden Fälle in seiner Log-Zeile nicht mehr
unterscheiden, sobald ein Name Cron-registriert ist — `task_downsync()`
loggt seine `[scheduled]`/`[triggered]`-Zeile deshalb selbst, anhand des
tatsächlich übergebenen Jobs.

## Skripte

Operative Skripte, bei Bedarf im Rahmen des regulären Betriebs erneut ausgeführt:

| Skript | Wofür |
|---|---|
| `scripts/backup_db.py` | Manuelles PostgreSQL-Backup nach S3 anstoßen (`--list`, `--cleanup`) |
| `scripts/restore_db.py` | PostgreSQL aus einem S3-Backup wiederherstellen (`--list`, `--backup-name`, `--force`) |
| `scripts/check_s3_integrity.py` | Bidirektionaler DB↔S3-Konsistenzcheck + Waisen-Bericht (read-only) |
| `scripts/downsync_prod.py` | Downsync von Prod-AWS-S3 (voller Spiegel) → lokaler Garage-Storage, danach lokale DB daraus wiederherstellen (`--dry-run`, `--yes`, `--skip-db`, `--skip-s3`, `--no-delete`) |
| `scripts/trigger_chronicles.py` | Chronik-Mail-Job manuell für ein beliebiges Referenzdatum anstoßen (`--date`, `--send`, `--to`) |
| `scripts/maintain_deleted_archive_files.py` | Weich gelöschte Archiv-Dateien auflisten, endgültig löschen oder wiederherstellen; das einzige Werkzeug, das Archivdaten dauerhaft löscht (`list`, `purge`, `purge-duplicates`, `restore`) |

Vollständige Doku (Aufruf, Parameter, Env-Vars) für jedes Skript: [`scripts/README.md`](scripts/README.md).

## Branching

- `main` — geschützt, nur Merge per PR
- `development` — aktiver Entwicklungs-Branch

## CI/CD

Die Pipeline (`.github/workflows/ci-cd.yml`) läuft bei Pushes und Pull Requests nach `main`, wöchentlich (montags 06:00 UTC) und bei manuellem Trigger:

1. **Lint & Format** - `ruff check` + `ruff format --check` + Router-Schichtentrennungs-Check
2. **Typecheck, Migrations & Test** — `pyright` + `alembic upgrade head` + `pytest --cov`
3. **CodeQL Security Scan**
4. **Dependency Audit** - `pip-audit` über beide Lock-Dateien
5. **Read-Only Rootfs Smoke Test** - das Produktions-Image, gestartet mit der Produktions-Härtung
6. **Build & Push Image** - pusht nach `ghcr.io` nur von `main` (Push oder manueller Trigger) und erst, wenn alle anderen Jobs bestanden haben

Der Production-/Stage-Rollout selbst läuft außerhalb dieser Pipeline: Der
`podman-auto-update.timer` des Zielsystems holt das neue `:latest`-Image
automatisch, oder ein Operator löst ihn sofort per `--tags deploy-api` aus
— siehe
[`vb-deploy`s Phase 2 — Tag-2-Betrieb](../vb-deploy/README.md#phase-2--tag-2-betrieb).
