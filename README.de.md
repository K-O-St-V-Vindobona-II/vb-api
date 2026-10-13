# vb-api

[English](README.md) | [Deutsch](README.de.md)

Das Backend des Vindobona-II-Systems. Dieses README behandelt, was für dieses Repository spezifisch ist, und setzt das [`vb-deploy`-README](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.de.md) voraus, das führende Dokument dafür, wie das ganze System aufgebaut ist, betrieben und wiederhergestellt wird. Alle vier Repositories liegen in der GitHub-Organisation [K-O-St-V-Vindobona-II](https://github.com/K-O-St-V-Vindobona-II).

## 1. Aufgabe und Namenskonvention

`vb-api` stellt das **Backend** bereit: die JSON-API, die Datenbank-Logik und die geplanten Jobs des Verwaltungssystems des Vereins (Mitglieder und die Mitgliederdatenbank "Standesdatenbank", Beiträge und die Buchhaltung der Altherrenschaft "AH-Kassen"/`p4x`, das Archiv, die Inhalte der öffentlichen Website, Mail-Benachrichtigungen). Die Frontends `vb-intern` (Mitglieder und Funktionäre, Login nötig) und `vb-www` (öffentliche Website) sprechen mit ihm; mehr braucht es nicht, um die Daten zu nutzen.

**Namenskonvention:** Der Repository-Name ist die Subdomain. `vb-api` wird unter **`api.vindobona2.at`** ausgeliefert (und `vb-intern` unter `intern.vindobona2.at`, `vb-www` unter `www.vindobona2.at`). Auf anderen Stages kommen die Domains aus der Inventory-Datei der Stage im privaten Repository `vb-access` (siehe `vb-deploy` [1.3](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.de.md#13-die-github-organisation-und-die-repositories)).

Tech-Stack: Python 3.14, FastAPI, SQLAlchemy (synchron), Alembic, PostgreSQL 18, S3-kompatibler Speicher (AWS S3 in Produktion, Garage auf anderen Stages), ARQ mit Valkey für Jobs. Die interaktive API-Dokumentation gibt es unter `/docs` nur auf der Stage `development`.

## 2. Container dieses Repositories

Alles von `vb-api` läuft in **einem Pod, `vb-api-pod`**; seine Container teilen sich einen Netzwerk-Namespace und erreichen einander über `localhost`:

| Container | Image | Führt Code dieses Repositories aus? | Immer vorhanden? |
|---|---|---|---|
| `vb-api` | `ghcr.io/k-o-st-v-vindobona-ii/vb-api` | **Ja** | ja |
| `vb-api-worker` | gleiches Image wie `vb-api` | **Ja** | ja |
| `vb-api-pg` | `postgres:18` | nein | ja |
| `vb-api-valkey` | `valkey/valkey:8` | nein | ja |
| `vb-garage` | `dxflrs/garage` | nein | nein, **nur Nicht-Produktions-Stages** |

Quadlet-Definitionen, Ports, Härtung und der genaue Pod-Aufbau stehen in `vb-deploy` (Kapitel [1.4](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.de.md#14-container-und-pods)).

## 3. Was jeder Container tut

- **`vb-api`**: liefert die API aus (gunicorn mit zwei uvicorn-Workern, Container-Port 8000, Host-Port 21000 auf `127.0.0.1`, erreichbar über Caddy). Beim Start wendet sein Entrypoint ausstehende Datenbank-Migrationen an. Health-Check: Die Root-URL antwortet.
- **`vb-api-worker`**: führt die geplanten und Hintergrund-Jobs mit `arq app.worker.WorkerSettings` aus: die nächtliche Sicherung, die wöchentlichen Mails, Integritätsprüfungen, Aufräumen und, auf anderen Stages, den nächtlichen Downsync. Er liest die Job-Queue aus Valkey. Er migriert **nicht** (`SKIP_MIGRATIONS=true`).
- **`vb-api-pg`**: die PostgreSQL-Datenbank. Die Anwendung verbindet sich als die Nicht-Superuser-Rolle `vb_app`, der die Datenbank gehört; der Bootstrap-Superuser dient nur dazu, sie anzulegen.
- **`vb-api-valkey`**: die Queue, aus der der Worker seine Jobs nimmt. Persistenz ist absichtlich aus; ein verlorener Job ist eine Benachrichtigungs-Mail oder ein manuell wiederholbarer Downsync.
- **`vb-garage`**: ein S3-Ersatz auf Nicht-Produktions-Stages, der eine Kopie des Produktions-Buckets hält.

## 4. Wo der Code dieses Repositories läuft

Der Code steckt in **einem einzigen Container-Image**, gebaut aus dem Target `prod` des `Dockerfile` durch die CI und veröffentlicht als `ghcr.io/k-o-st-v-vindobona-ii/vb-api:latest` und `...:<voller Commit-SHA>` (das Image trägt den Commit zusätzlich als Label `org.opencontainers.image.revision`). Dieses eine Image wird zweimal gestartet: als `vb-api` (Standardbefehl: gunicorn) und als `vb-api-worker` (das Quadlet ersetzt den Befehl durch `arq app.worker.WorkerSettings`). Der Entrypoint `docker-entrypoint.sh` führt vor dem Start `alembic upgrade head` aus, außer bei `SKIP_MIGRATIONS=true`; steht die Datenbank auf einer Revision, die das Image nicht kennt (ein Rollback), schreibt er eine Warnung und startet ohne zu migrieren.

Das Target `dev` desselben `Dockerfile` (`localhost/vb-api:dev`) betreibt die lokale Entwicklungs-Stage: Der Quellordner wird in den Container eingehängt, und `uvicorn --reload` startet bei jeder Änderung neu.

Aufbau des Codes, von außen nach innen; jede Schicht ruft nur die nächste auf:

```
app/api/        Router und Dependencies: nur HTTP, keine Geschäftslogik, kein Datenbankzugriff
app/services/   Geschäftslogik und jede Datenbank-Abfrage
app/models/     SQLAlchemy-Modelle
app/schemas/    Pydantic-Modelle für Anfragen und Antworten
app/core/       Einstellungen, Speicher, Scheduler-Jobs und der deklarative Job-Zeitplan
app/worker.py   verpackt die Jobs für ARQ
alembic/        Datenbank-Migrationen
scripts/        Betriebs-Skripte (siehe scripts/README.de.md)
```

`scripts/check_router_soc.py` erzwingt die Regel "Router rufen nur Services auf".

---

## Entwicklung

### Einrichtung

- Podman mit laufendem `vb-api`-Container aus den lokalen Entwicklungs-Quadlets ([`vb-deploy` 5.7](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.de.md#57-lokale-entwicklungsumgebung)). Die Python-Entwicklungsabhängigkeiten sind im Container installiert (`requirements-dev.lock`).
- Führe jeden Befehl über `podman exec vb-api ...` aus; installiere nie Pakete auf dem Host.

```bash
# einmal pro Klon: die Git-Hooks installieren (sie spiegeln die CI-Prüfungen)
pre-commit install
```

Ohne die Hooks überspringen Commits ruff-Formatierung, Typprüfung und die Tests, und die CI schlägt später fehl. Drei Hooks (Router-Trennung, `pyright`, `pytest`) laufen im `vb-api`-Container, er muss beim Commit also laufen. Ein weiterer Hook verweigert `.env*`-Dateien (außer `.env.example`) und Shell-History-Dateien.

### Tests

Die Suite läuft nur gegen eine echte PostgreSQL-Datenbank, nie gegen eine leichtere eingebettete (siehe `tests/conftest.py`): Nur so lässt sich Datenbank-Verhalten wie die `ON DELETE`-/`ON UPDATE`-Regeln von Fremdschlüsseln prüfen. Das Schema wird in jeder Sitzung über die echten Alembic-Migrationen neu aufgebaut, ein Auseinanderdriften von Modellen und Migrationen fällt hier also ebenfalls auf.

**Einmalige Einrichtung:** Lege eine eigene Testdatenbank auf demselben Postgres-Server an (richte Tests nie auf die echte Datenbank `vb`; die Testsitzung löscht ihr Schema und baut es neu auf):

```bash
podman exec vb-api-pg psql -U vb -c "CREATE DATABASE vb_test OWNER vb;"
```

**Die Suite ausführen** (der Helfer leitet die Testdatenbank aus `DATABASE_URL` ab und führt danach das Coverage-Gate aus):

```bash
podman exec vb-api sh scripts/run_pytest.sh -q
```

oder direkt, mit ausdrücklich genannter Datenbank:

```bash
podman exec -e TEST_DATABASE_URL=postgresql+psycopg2://vb:<pw>@localhost:5432/vb_test vb-api python -m pytest
```

`<pw>` ist das Postgres-Passwort aus deiner `.env`; `localhost:5432` löst auf, weil `vb-api` und `vb-api-pg` einen Pod teilen. Ist `TEST_DATABASE_URL` nicht gesetzt, fällt `conftest.py` auf `DATABASE_URL` zurück, akzeptiert sie aber nur, wenn sie eine erlaubte Testdatenbank nennt (`vb_test` oder `test`); alles andere wird abgelehnt, damit eine falsch gesetzte URL nie eine echte Datenbank leeren kann. Führe gegen `vb_test` immer nur eine pytest-Sitzung gleichzeitig aus.

**Coverage-Gate.** Ein Lauf schreibt `coverage.json`. `pytest` erzwingt eine gemeinsame Untergrenze (`--cov-fail-under`), und `scripts/check_coverage_gate.py` misst danach Statement-, Branch- und Funktions-Abdeckung gegen getrennte Mindestwerte (`[tool.coverage_gate]` in `pyproject.toml`), damit ein Absinken einer Zahl sich nicht hinter einer anderen verstecken kann. Neue Geschäftslogik braucht Tests, und Endpunkte, die Listen oder tiefe Relationen liefern, brauchen einen Test, der die Anzahl der SQL-Statements begrenzt (N+1-Schutz).

### Linting, Formatierung und Typen

```bash
podman exec vb-api python -m ruff check .          # Lint
podman exec vb-api python -m ruff format --check . # Formatierung prüfen
podman exec vb-api python -m ruff check --fix . && podman exec vb-api python -m ruff format .   # direkt beheben
podman exec vb-api python -m pyright               # strenge Typprüfung (app, scripts, main.py)
podman exec vb-api python scripts/check_router_soc.py
```

### Umgebungsvariablen

Kopiere `.env.example` nach `.env` und trage die nötigen Werte ein. Echte Stage-Werte liegen im privaten Repository `vb-access`; die Variablen und wie man sie ändert, sind in `vb-deploy` beschrieben ([Anhang A](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.de.md#anhang-a-secrets-und-pflichtvariablen)). Alle Einstellungen sind in `app/core/config.py` typisiert; der Code liest die Umgebung nie direkt.

`APP_ENVIRONMENT` ist **Pflicht**; ohne sie startet die Anwendung nicht:

| Wert | Verwendung |
|---|---|
| `development` | Lokale Entwicklungs-Stage |
| `test` | Automatisierte Testläufe |
| `qa` | QA-Stage |
| `production` | Produktion |

### Datenbank-Migrationen

```bash
# ausstehende Migrationen anwenden
podman exec vb-api alembic upgrade head

# eine neue Migration aus den Modellen erzeugen
podman exec vb-api alembic revision --autogenerate -m "beschreibung"
```

Das Produktions-Image wendet ausstehende Migrationen bei jedem Start selbst an (auch nachdem `podman auto-update` ein neues Image geholt hat). Der manuelle Befehl ist für die Entwicklungs-Stage (ihr Image behält einen einfachen `uvicorn --reload`-Befehl ohne Auto-Migration) und zum Erzeugen von Migrationen.

**Rollbacks und Kompatibilität.** Der Entrypoint startet auch ein älteres Image gegen eine neuere Datenbank: Er schreibt eine Warnung und startet ohne zu migrieren. Das ist nur sicher, solange jede Migration mit dem vorherigen Release kompatibel bleibt. Eine Schema-Änderung folgt deshalb *expand first, contract later*: Die neue Spalte, Tabelle oder Einschränkung kommt in einem Release hinzu (nullable oder mit Standardwert, alter Code läuft weiter), der Code wird umgestellt, und die alte Struktur wird in einem späteren Release entfernt. Eine Änderung, die diese Regel nicht einhalten kann, braucht einen Hinweis im Docstring ihrer Migration und eine frische Sicherung (`scripts/backup_db.py`) vor dem Rollout. Nur der Web-Container migriert, so gibt es pro Rollout genau einen migrierenden Prozess. Um eine Migration zurückzunehmen: `podman exec vb-api alembic downgrade -1`.

Neue Tabellen folgen den Datenbank-Regeln des Projekts: UUID-Primärschlüssel, Fremdschlüssel mit ausdrücklichem `ON DELETE`/`ON UPDATE` und Index, `TIMESTAMPTZ`-Spalten, `created_at`/`updated_at` vom Datenbank-Trigger gepflegt.

### Abhängigkeiten

`requirements.lock` und `requirements-dev.lock` pinnen jedes Paket mit Hashes und werden aus `requirements.txt` und `requirements-dev.txt` erzeugt:

```bash
uv pip compile requirements.txt -o requirements.lock --python-version 3.14 --generate-hashes
uv pip compile requirements-dev.txt -o requirements-dev.lock --python-version 3.14 --generate-hashes
```

Ergänze einmal im Monat `--upgrade` bei beiden, um neue Versionen zu holen. Der CI-Job `dependency-audit` führt `pip-audit` über beide Lock-Dateien aus (bei jedem Lauf und wöchentlich): Schlägt er fehl, aktualisiere das betroffene Paket mit `--upgrade-package <name>` und erzeuge die Locks neu; gibt es noch keine behobene Version, akzeptiere die Meldung mit schriftlicher Begründung, indem du `--ignore-vuln <ID>` zum Befehl des Jobs hinzufügst. Der Dependency-Graph von GitHub listet nur die ungepinnten `requirements*.txt`-Dateien, nicht die Lock-Dateien, seine Warnungen sagen über die gepinnten Versionen daher wenig; dieser CI-Job ist es, der die Python-Pakete abdeckt.

### Scheduler

Geplante Jobs laufen im Container `vb-api-worker`. Die Job-Rümpfe liegen in `app/core/scheduler.py` (jeder öffnet seine eigene Datenbank-Sitzung und protokolliert sein eigenes Ergebnis); `app/worker.py` verpackt jeden in eine dünne asynchrone Task und baut ARQs Cron-Liste aus dem deklarativen Zeitplan in `app/core/job_schedule_registry.py`, der einzigen Quelle der Wahrheit, die Web- und Worker-Container lesen, sodass sie sich nie darüber uneinig sein können, was wo geplant ist. Alle Jobs teilen sich eine Zeitzone (`APP_TIMEZONE`, Standard `Europe/Vienna`).

Die Registrierung hängt von `APP_ENVIRONMENT` ab: `cleanup` läuft überall, die Geschäfts-Jobs und `db_backup` nur in `production`, `downsync` nur außerhalb von `production`.

| Job | Zeitplan | Stage | Zweck |
|---|---|---|---|
| `cleanup` | stündlich | alle | Löscht abgelaufene Sessions, Reset-Tokens, alte Aktivitätsprotokolle, versandte Mails, verwaiste User-Agents. |
| `refresh_category_filter_hits` | täglich 07:00 | production | Berechnet neu, welche Transaktionen zu jedem Kategorie-Filter passen. |
| `birthday_mails` | täglich 15:53 | production | Geburtstagsgrüße an Mitglieder, die morgen Geburtstag haben. |
| `debtor_reminder` | 25., 18:32 | production | Beitragsrückstands-Erinnerungen (über 300 EUR). |
| `standesdb_chronicles` | Dienstag 17:00 | production | Die wöchentliche Jubiläums-Mail. |
| `archive_health_check` | Dienstag 01:00 | production | Archivdateien gegen S3, Bericht an die Archiv-Administratoren. |
| `standesdb_health_check` | Dienstag 03:00 | production | Mitgliederbilder gegen S3, Bericht an die Administratoren. |
| `db_backup` | täglich, `BACKUP_HOUR` (03:00) | production | Dump nach S3, Dumps älter als `BACKUP_RETENTION_DAYS` löschen. |
| `downsync` | täglich, `BACKUP_HOUR` + 1 (04:00) | alle außer production | Den Produktions-Bucket spiegeln, den neuesten Dump einspielen, `alembic upgrade head`. |

**Downsync** frischt jede Nicht-Produktions-Stage einmal täglich mit echten Produktionsdaten auf: Er spiegelt den ganzen Produktions-Bucket in das Garage der Stage und stellt die lokale Datenbank aus dem neuesten Produktions-Dump wieder her. Mit `DOWNSYNC_LIGHT=true` spiegelt er nur die Mitgliederbilder, die öffentliche Galerie und den neuesten Dump (rund 50 MB statt des ganzen Buckets; Archivdateien fehlen dann auf der Stage). Er teilt seinen Code mit `scripts/downsync_prod.py` (`app/services/downsync_service.py`), wird in Produktion an zwei Stellen verweigert, und `POST /api/system/downsync/trigger` (Berechtigung `systemAdmin`) startet dieselbe Task auf Abruf.

`GET /api/system/scheduled-jobs` (Berechtigung `systemAdmin`) liefert den nächsten Lauf jedes Jobs, berechnet aus dem deklarativen Zeitplan mit ARQs eigenem `next_cron()`, und seinen zuletzt protokollierten Lauf. Die Log-Zeilen des Workers sind mit `[scheduled]` oder `[triggered]` markiert (`app/core/worker_logging.py`), ein Log-Viewer wie Dozzle zeigt also, ob ein Lauf vom Cron-Zeitplan oder von einer Anfrage kam.

### Skripte

Die Betriebs-Skripte (Sicherung, Restore, Integritätsprüfung, Downsync, Archiv-Wartung, Chronik-Mail) und die Entwickler-Helfer sind in [`scripts/README.de.md`](scripts/README.de.md) dokumentiert.

### Branching und Konventionen

- `main` ist geschützt und nimmt nur Pull Requests an. Gearbeitet wird auf `development`.
- Commit-Nachrichten folgen Conventional Commits (`feat:`, `fix:`, `refactor:`, `chore:`, `docs:`, `test:`) und sind auf Englisch geschrieben; ebenso Code-Kommentare und Docstrings.
- Neue Logik kommt mit Tests (pytest); die CI erzwingt das Coverage-Gate.

### CI/CD

`.github/workflows/ci-cd.yml` läuft bei Pushes und Pull Requests auf `main`, wöchentlich (Montag 06:00 UTC) und bei manuellem Start:

1. **Lint und Format:** `ruff check`, `ruff format --check`, Router-Trennungs-Prüfung.
2. **Typprüfung, Migrationen und Tests:** `pyright`, `alembic upgrade head`, `pytest` mit Coverage und Coverage-Gate.
3. **CodeQL**-Sicherheitsscan.
4. **Dependency-Audit:** `pip-audit` über beide Lock-Dateien.
5. **Read-only-Rootfs-Smoke-Test:** das Produktions-Image, gestartet mit der Produktions-Härtung.
6. **Image bauen und veröffentlichen:** nur von `main` (Push oder manueller Start) und nur nachdem jeder andere Job bestanden hat.

Der Rollout ist nicht Teil der Pipeline: Der `podman-auto-update.timer` des Hosts holt das neue `:latest`-Image über Nacht, oder ein Betreiber deployt sofort mit `--tags deploy-api` (siehe `vb-deploy` [1.7](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.de.md#17-wie-eine-änderung-in-produktion-kommt)).
