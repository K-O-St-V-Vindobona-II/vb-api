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
[`vb-deploy`'s Prerequisites](../vb-deploy/README.md#prerequisites).

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
[`vb-deploy`'s Phase 2 — Day-2 Operations](../vb-deploy/README.md#phase-2--day-2-operations).

---

# Deutsch

FastAPI-Backend für **vb** — das interne Verwaltungssystem von Vindobona II / Vindobona nova.

> Alle zugehörigen Repos liegen in der GitHub-Organisation [K-O-St-V-Vindobona-II](https://github.com/K-O-St-V-Vindobona-II).

## Tech-Stack

- **Laufzeitumgebung:** Python 3.12, FastAPI, SQLAlchemy (synchron), Alembic
- **Datenbank:** PostgreSQL 18
- **Storage:** S3-kompatibel (MinIO auf Dev-VPS, AWS S3 in Produktion)
- **Scheduler:** APScheduler (asynchron)
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

### Tests ausführen

Die Testsuite läuft ausschließlich gegen eine echte PostgreSQL-Datenbank,
niemals gegen SQLite (siehe `tests/conftest.py`) — nur so lässt sich
DB-natives Verhalten wie Foreign-Key-`ON DELETE`/`ON UPDATE`-Regeln
tatsächlich prüfen, was eine SQLite-gestützte Suite stillschweigend nie
durchsetzen würde. Das Schema wird bei jeder Session von Grund auf über die
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

## Scheduler

Hintergrund-Jobs laufen über eine einzige In-Process-APScheduler-Instanz
(`app/core/scheduler.py`), einmal pro Deployment gestartet (ein
Postgres-Advisory-Lock stellt sicher, dass nur ein gunicorn-Worker-Prozess
Jobs tatsächlich registriert/auslöst, obwohl jeder Worker seine eigene
Scheduler-Instanz hochfährt).

### Job-Registrierung ist über `APP_ENVIRONMENT` gesteuert

- `cleanup` läuft in jeder Stage.
- Alle "Business"-Jobs (Mails, Erinnerungen, Chroniken, Health-Check-Berichte,
  Kategoriefilter-Refresh) sowie `db_backup` laufen nur in `production`.
- `downsync` läuft nur außerhalb von `production` — siehe unten.

| Job-ID | Zeitplan | Stage | Zweck |
|---|---|---|---|
| `cleanup` | stündlich | alle | Löscht abgelaufene Sessions, Reset-Tokens und Tracking-Daten nach Ablauf der Aufbewahrungsfrist. |
| `refresh_category_filter_hits` | täglich 07:00 (Wien) | production | Berechnet neu, welche Transaktionen auf welchen P4x-Kategoriefilter passen. |
| `birthday_mails` | täglich 15:53 (Wien) | production | Sendet Geburtstagsgrüße an VBW-Mitglieder, die morgen Geburtstag haben. |
| `debtor_reminder` | monatlich, 25. um 18:32 (Wien) | production | Sendet Beitragsrückstands-Erinnerungen (Schuld > 300 €). |
| `standesdb_chronicles` | wöchentlich, Di 17:00 (Wien) | production | Sendet die wöchentliche Jubiläums-Übersicht (Geburtstage, Aufnahmen, ...). |
| `archive_health_check` | wöchentlich, Di 01:00 (Wien) | production | Prüft, ob in der DB referenzierte Archiv-Dateien in S3 existieren, meldet Waisen. |
| `standesdb_health_check` | wöchentlich, Di 03:00 (Wien) | production | Derselbe Integritätscheck für Standesdb-Bilder. |
| `db_backup` | täglich, `BACKUP_HOUR` UTC (Standard 03:00) | production (+ `BACKUP_ENABLED`) | Erstellt einen PostgreSQL-Dump, lädt ihn nach S3 hoch, löscht Backups älter als `BACKUP_RETENTION_DAYS`. |
| `downsync` | täglich, `DOWNSYNC_HOUR` UTC (`BACKUP_HOUR + 1`, Standard 04:00) | non-production | Siehe unten. |

### Downsync — Non-Production-Stages aktuell mit Prod halten

Die Grundidee: **jede Non-Production-Stage (Dev, Test, QA) wird einmal
täglich automatisch mit echten Produktionsdaten aufgefrischt** — niemand
muss dafür manuell ein Skript starten.

`job_downsync()` läuft kurz nach dem Production-`db_backup`-Job (Standard:
eine Stunde später, damit der Dump des Tages auf dem Prod-S3 bereits
existiert) und macht in dieser festen Reihenfolge:

1. Spiegelt den **gesamten** Production-AWS-S3-Bucket (`archive/`,
   `standesdb/`, `public/`, `db-backups/` — alles) auf den eigenen
   S3-kompatiblen Storage dieser Stage herunter (z. B. MinIO auf dem Dev-VPS).
2. Stellt sofort die lokale PostgreSQL-Datenbank aus dem frisch
   gespiegelten `db-backups/`-Präfix wieder her (der neueste Prod-Dump) und
   führt `alembic upgrade head` aus.

Das ist dieselbe Logik, die `scripts/downsync_prod.py` bereits für den
manuellen/interaktiven Gebrauch ausführt (siehe [Skripte](#skripte)) — der
gemeinsame Code zum Laden der Prod-Credentials und zum Aufbau des Storage
liegt in `app/services/downsync_service.py`, genutzt sowohl vom CLI-Skript
als auch vom automatisierten Job. Production registriert diesen Job nie:
Er ist doppelt abgesichert — einmal über den `APP_ENVIRONMENT`-Check in
`start_scheduler()`, und zusätzlich innerhalb von `job_downsync()` selbst
als zusätzliches Sicherheitsnetz.

### Zeitplan einsehen

`GET /api/system/scheduled-jobs` (erfordert `systemAdmin`) listet, was auf
der laufenden Instanz tatsächlich registriert ist. Da die Registrierung
selbst pro Stage gesteuert wird, spiegelt dieser Endpunkt immer die
Realität wider — es gibt keine separate "welcher Job gilt für welche
Stage"-Liste, die aus dem Tritt geraten könnte.

## Skripte

Operative Skripte, bei Bedarf im Rahmen des regulären Betriebs erneut ausgeführt:

| Skript | Wofür |
|---|---|
| `scripts/backup_db.py` | Manuelles PostgreSQL-Backup nach S3 anstoßen (`--list`, `--cleanup`) |
| `scripts/restore_db.py` | PostgreSQL aus einem S3-Backup wiederherstellen (`--list`, `--backup-name`, `--force`) |
| `scripts/check_s3_integrity.py` | Bidirektionaler DB↔S3-Konsistenzcheck + Waisen-Bericht (read-only) |
| `scripts/downsync_prod.py` | Downsync von Prod-AWS-S3 (voller Spiegel) → lokales MinIO, danach lokale DB daraus wiederherstellen (`--dry-run`, `--yes`, `--skip-db`, `--skip-s3`, `--no-delete`) |
| `scripts/trigger_chronicles.py` | Chronik-Mail-Job manuell für ein beliebiges Referenzdatum anstoßen (`--date`, `--send`, `--to`) |

Vollständige Doku (Aufruf, Parameter, Env-Vars) für jedes Skript: [`scripts/README.md`](scripts/README.md).

## Branching

- `main` — geschützt, nur Merge per PR
- `development` — aktiver Entwicklungs-Branch

## CI/CD

Die Pipeline (`.github/workflows/ci-cd.yml`) läuft bei jedem Push nach `development` und bei PRs nach `main`:

1. **Lint & Format** — `ruff check` + `ruff format --check`
2. **Typecheck, Migrations & Test** — `pyright` + `alembic upgrade head` + `pytest --cov`
3. **CodeQL Security Scan**
4. **Build & Push Image** — pusht nach `ghcr.io` bei Release oder manuellem Trigger

Der Production-/Stage-Rollout selbst läuft außerhalb dieser Pipeline: Der
`podman-auto-update.timer` des Zielsystems holt das neue `:latest`-Image
automatisch, oder ein Operator löst ihn sofort per `--tags deploy-api` aus
— siehe
[`vb-deploy`s Phase 2 — Tag-2-Betrieb](../vb-deploy/README.md#phase-2--tag-2-betrieb).
