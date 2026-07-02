# Scripts

Operational and one-time-migration scripts for the backend. All scripts are
run manually (not part of the request/response path) and expect to be
executed from the `vb-api` project root.

They read their configuration from environment variables (`DATABASE_URL`,
`S3_*`, ...) — normally the same `.env` the backend container uses. Each
script below lists two ways to run it:
- **Inside the container** — a shell already opened inside the running `vb-api` backend container (e.g. via `podman exec -it vb-api bash`), where the working directory is `/app`.
- **Via `podman exec`** — directly from the host, without opening a shell first.

---

## `migrate_to_s3.py`

One-time migration that uploads all files from the local filesystem
(`/data/standesdb/images`, `/data/archive/store`, and optionally the cache/
thumbnail directories) to the configured S3 bucket. Content types are taken
from the corresponding DB rows (`StandesdbImage.type`, `ArchiveStoreItem.mime_type`).
Files already present in S3 (checked via `head_object`) are skipped, so the
script is safe to re-run. After uploading it always runs a verification pass
that confirms every non-deleted `StandesdbImage` / `ArchiveStoreItem` row has
a matching S3 object.

**Usage:**
```bash
# Inside the container
python scripts/migrate_to_s3.py [--verify-only] [--include-cache]

# Via podman exec
podman exec vb-api python scripts/migrate_to_s3.py [--verify-only] [--include-cache]
```

**Parameters:**
- `--verify-only` — skip the upload step, only run the DB → S3 verification and report missing objects (exit code 1 if any are missing).
- `--include-cache` — additionally migrate the thumbnail/cache directories (`STANDESDB_CACHE_PATH`, `ARCHIVE_CACHE_PATH`); these are omitted by default.

**Relevant env vars:** `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `DATABASE_URL`, `STANDESDB_IMAGES_PATH`, `ARCHIVE_STORE_PATH`, `STANDESDB_CACHE_PATH`, `ARCHIVE_CACHE_PATH`.

---

## `downsync_from_prod_aws.py`

Mirrors files from the production AWS S3 bucket down to the local Dev MinIO
instance, remapping the legacy AWS key prefixes (`standesdb-backup/`,
`archive-backup/`) to the new MinIO key structure (`standesdb/images/`,
`archive/store/`). Objects that exist locally but no longer exist in the AWS
source are deleted (mirror mode), and orphaned local thumbnails (whose
original file no longer exists) are cleaned up too. Refuses to run at all
when `APP_ENVIRONMENT=production`, since the sync direction (prod → dev) and
the delete/mirror behavior would be destructive against production data.
AWS credentials are read from `/run/secrets/aws-prod.env`, never from
environment variables, to keep them out of the normal container env.

**Usage:**
```bash
# Inside the container
python scripts/downsync_from_prod_aws.py
python scripts/downsync_from_prod_aws.py --dry-run
python scripts/downsync_from_prod_aws.py --no-delete
python scripts/downsync_from_prod_aws.py --verify-only

# Via podman exec
podman exec vb-api python scripts/downsync_from_prod_aws.py
podman exec vb-api python scripts/downsync_from_prod_aws.py --dry-run
podman exec vb-api python scripts/downsync_from_prod_aws.py --no-delete
podman exec vb-api python scripts/downsync_from_prod_aws.py --verify-only
```

**Parameters:**
- `--dry-run` — print what would be copied/deleted without performing the sync.
- `--no-delete` — sync new/changed files but skip deleting local orphans (safer, incremental sync).
- `--verify-only` — only count differences between AWS and MinIO (missing/orphaned objects per prefix) — does not sync or delete anything.

**Relevant env vars:** `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` (MinIO target), `APP_ENVIRONMENT` (must not be `production`). AWS source credentials come from the file `/run/secrets/aws-prod.env` (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_BUCKET`).

---

## `check_s3_integrity.py`

Read-only consistency check between the database and S3. Reports two things:
1. **Completeness** — every `sha256_hash` referenced by a `StandesdbImage` or `ArchiveStoreItem` row must exist as an object in S3; missing ones are printed and cause a non-zero exit code.
2. **Orphans** — S3 objects under the image/store prefixes that are referenced by no DB row at all (active or soft-deleted), listed with size/content-type/last-modified for manual review.

The script never deletes anything — cleanup of orphans, if desired, must be
done manually via the S3 web console.

**Usage:**
```bash
# Inside the container
python scripts/check_s3_integrity.py

# Via podman exec
podman exec vb-api python scripts/check_s3_integrity.py
```

**Parameters:** none (behavior is controlled entirely via env vars).

**Relevant env vars:** `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_PATH_STANDESDB_IMAGES`, `S3_PATH_ARCHIVE_STORE`, `DATABASE_URL`.

---

## `backup_db.py`

CLI wrapper around `backup_service.run_backup()` to manually trigger a
PostgreSQL backup to S3 — the same operation the scheduled `db_backup` job
(see `app/core/scheduler.py`) runs automatically every `BACKUP_INTERVAL_DAYS`
days. Useful before risky operations (e.g. a cutover or a restore rehearsal)
where you want an on-demand, up-to-date backup rather than waiting for the
next scheduled run. Retention cleanup (deleting backups older than
`BACKUP_RETENTION_DAYS`) is opt-in via `--cleanup`, so a manual backup never
deletes other backups as a side effect unless explicitly requested.

**Usage:**
```bash
# Inside the container
python scripts/backup_db.py [--list] [--cleanup]

# Via podman exec
podman exec vb-api python scripts/backup_db.py [--list] [--cleanup]
```

**Parameters:**
- `--list` — print all available backup keys in S3 and exit, without creating a backup.
- `--cleanup` — after a successful backup, also delete backups older than `BACKUP_RETENTION_DAYS` (same cleanup the scheduled job performs).

**Relevant env vars:** `DATABASE_URL` (must point to PostgreSQL), plus the `S3_*` vars used by `get_storage()`. `BACKUP_RETENTION_DAYS` only matters when `--cleanup` is passed.

---

## `restore_db.py`

CLI wrapper around `backup_service.run_restore()` to restore the PostgreSQL
database from a `pg_dump` backup stored in S3. Downloads the backup object,
writes it to a temp file, and restores it via `pg_restore`. If no specific
backup is named, the lexicographically latest key under the backup prefix is
used (backup filenames are timestamp-sortable). As a safety guard, restoring
while `APP_ENVIRONMENT=production` is refused unless `--force` is passed
explicitly, since a restore overwrites the live database.

**Usage:**
```bash
# Inside the container
python scripts/restore_db.py [--list] [--backup-name NAME] [--force]

# Via podman exec
podman exec vb-api python scripts/restore_db.py [--list] [--backup-name NAME] [--force]
```

**Parameters:**
- `--list` — print all available backup keys in S3 and exit, without restoring anything.
- `--backup-name NAME` — restore this specific backup filename instead of auto-selecting the latest.
- `--force` — required to proceed when `APP_ENVIRONMENT=production`; has no effect in other environments.

**Relevant env vars:** `DATABASE_URL` (must point to PostgreSQL), `APP_ENVIRONMENT`, plus the `S3_*` vars used by `get_storage()`.

---

## `sqlite2pg.py`

Idempotent one-time migration that copies all data from the legacy SQLite
database into PostgreSQL. It creates all tables in PostgreSQL (if they don't
exist yet, via SQLAlchemy metadata), truncates them (`TRUNCATE ... CASCADE`),
then copies every row table-by-table in batches of 1000, temporarily
disabling FK/trigger checks (`session_replication_role = 'replica'`) so
insertion order doesn't matter. After copying, auto-increment sequences for
integer primary keys are reset to `MAX(id) + 1` so future inserts don't
collide with migrated rows. Re-running the script is safe — it always starts
from a clean truncate.

**Usage:**
```bash
# Inside the container
python scripts/sqlite2pg.py

# Via podman exec
podman exec vb-api python scripts/sqlite2pg.py
```

**Parameters:** none — the script is non-interactive and takes no CLI flags.

**Relevant env vars:** `DATABASE_URL` (must be a PostgreSQL URL — the script aborts otherwise). The SQLite source path is hardcoded to `/database/legacy_db.sqlite3`.

---

# Scripts (Deutsch)

Betriebs- und einmalige Migrations-Scripts für das Backend. Alle Scripts
werden manuell ausgeführt (sind nicht Teil des Request/Response-Pfads) und
gehen davon aus, dass sie aus dem `vb-api`-Projekt-Root heraus gestartet
werden.

Sie beziehen ihre Konfiguration aus Umgebungsvariablen (`DATABASE_URL`,
`S3_*`, ...) — normalerweise dieselbe `.env`, die auch der Backend-Container
verwendet. Für jedes Script unten sind zwei Aufrufwege beschrieben:
- **Im Container** — eine bereits im laufenden `vb-api`-Backend-Container geöffnete Shell (z. B. via `podman exec -it vb-api bash`), Arbeitsverzeichnis ist `/app`.
- **Via `podman exec`** — direkter Aufruf vom Host aus, ohne vorher eine Shell zu öffnen.

---

## `migrate_to_s3.py`

Einmalige Migration, die alle Dateien vom lokalen Dateisystem
(`/data/standesdb/images`, `/data/archive/store`, optional auch die Cache-/
Thumbnail-Verzeichnisse) in den konfigurierten S3-Bucket hochlädt. Die
Content-Types werden aus den zugehörigen DB-Zeilen übernommen
(`StandesdbImage.type`, `ArchiveStoreItem.mime_type`). Bereits in S3
vorhandene Dateien (geprüft via `head_object`) werden übersprungen, das
Script kann also gefahrlos erneut ausgeführt werden. Nach dem Upload läuft
immer ein Verifikationsdurchgang, der prüft, ob jede nicht gelöschte
`StandesdbImage`- / `ArchiveStoreItem`-Zeile ein passendes S3-Objekt hat.

**Aufruf:**
```bash
# Im Container
python scripts/migrate_to_s3.py [--verify-only] [--include-cache]

# Via podman exec
podman exec vb-api python scripts/migrate_to_s3.py [--verify-only] [--include-cache]
```

**Parameter:**
- `--verify-only` — überspringt den Upload, führt nur die DB→S3-Verifikation aus und meldet fehlende Objekte (Exit-Code 1, falls welche fehlen).
- `--include-cache` — migriert zusätzlich die Thumbnail-/Cache-Verzeichnisse (`STANDESDB_CACHE_PATH`, `ARCHIVE_CACHE_PATH`); standardmäßig ausgelassen.

**Relevante Env-Vars:** `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `DATABASE_URL`, `STANDESDB_IMAGES_PATH`, `ARCHIVE_STORE_PATH`, `STANDESDB_CACHE_PATH`, `ARCHIVE_CACHE_PATH`.

---

## `downsync_from_prod_aws.py`

Spiegelt Dateien vom produktiven AWS-S3-Bucket auf die lokale Dev-MinIO-
Instanz herunter und bildet dabei die alten AWS-Key-Präfixe
(`standesdb-backup/`, `archive-backup/`) auf die neue MinIO-Key-Struktur
(`standesdb/images/`, `archive/store/`) ab. Objekte, die lokal existieren,
aber in der AWS-Quelle nicht mehr vorhanden sind, werden gelöscht
(Mirror-Modus); verwaiste lokale Thumbnails (deren Originaldatei nicht mehr
existiert) werden ebenfalls bereinigt. Verweigert den Start komplett, wenn
`APP_ENVIRONMENT=production` gesetzt ist, da sowohl die Sync-Richtung
(Prod → Dev) als auch das Lösch-/Mirror-Verhalten gegen Produktionsdaten
destruktiv wären. AWS-Credentials werden ausschließlich aus
`/run/secrets/aws-prod.env` gelesen, nie aus Umgebungsvariablen, um sie aus
der normalen Container-Env herauszuhalten.

**Aufruf:**
```bash
# Im Container
python scripts/downsync_from_prod_aws.py
python scripts/downsync_from_prod_aws.py --dry-run
python scripts/downsync_from_prod_aws.py --no-delete
python scripts/downsync_from_prod_aws.py --verify-only

# Via podman exec
podman exec vb-api python scripts/downsync_from_prod_aws.py
podman exec vb-api python scripts/downsync_from_prod_aws.py --dry-run
podman exec vb-api python scripts/downsync_from_prod_aws.py --no-delete
podman exec vb-api python scripts/downsync_from_prod_aws.py --verify-only
```

**Parameter:**
- `--dry-run` — zeigt an, was kopiert/gelöscht würde, ohne den Sync auszuführen.
- `--no-delete` — synct neue/geänderte Dateien, überspringt aber das Löschen lokaler Waisen (sichererer, inkrementeller Sync).
- `--verify-only` — zählt nur die Unterschiede zwischen AWS und MinIO (fehlende/verwaiste Objekte pro Präfix) — synct oder löscht nichts.

**Relevante Env-Vars:** `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` (MinIO-Ziel), `APP_ENVIRONMENT` (darf nicht `production` sein). Die AWS-Quell-Credentials kommen aus der Datei `/run/secrets/aws-prod.env` (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_BUCKET`).

---

## `check_s3_integrity.py`

Read-only-Konsistenzprüfung zwischen Datenbank und S3. Meldet zwei Dinge:
1. **Vollständigkeit** — jeder von einer `StandesdbImage`- oder `ArchiveStoreItem`-Zeile referenzierte `sha256_hash` muss als Objekt in S3 existieren; fehlende werden ausgegeben und führen zu einem Exit-Code ungleich 0.
2. **Waisen** — S3-Objekte unter den Image-/Store-Präfixen, die von keiner DB-Zeile referenziert werden (weder aktiv noch soft-deleted), aufgelistet mit Größe/Content-Type/Änderungsdatum zur manuellen Prüfung.

Das Script löscht niemals etwas — eine Bereinigung der Waisen muss, falls
gewünscht, manuell über die S3-Web-Konsole erfolgen.

**Aufruf:**
```bash
# Im Container
python scripts/check_s3_integrity.py

# Via podman exec
podman exec vb-api python scripts/check_s3_integrity.py
```

**Parameter:** keine (Verhalten wird vollständig über Env-Vars gesteuert).

**Relevante Env-Vars:** `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_PATH_STANDESDB_IMAGES`, `S3_PATH_ARCHIVE_STORE`, `DATABASE_URL`.

---

## `backup_db.py`

CLI-Wrapper um `backup_service.run_backup()`, um manuell ein PostgreSQL-
Backup nach S3 anzustoßen — dieselbe Operation, die der geplante
`db_backup`-Job (siehe `app/core/scheduler.py`) automatisch alle
`BACKUP_INTERVAL_DAYS` Tage ausführt. Nützlich vor riskanten Operationen
(z. B. einem Cutover oder einer Restore-Generalprobe), wenn man ein
aktuelles Backup auf Abruf braucht, statt auf den nächsten geplanten Lauf zu
warten. Die Retention-Bereinigung (Löschen von Backups älter als
`BACKUP_RETENTION_DAYS`) ist über `--cleanup` opt-in, damit ein manuelles
Backup nie ungefragt andere Backups als Nebeneffekt löscht.

**Aufruf:**
```bash
# Im Container
python scripts/backup_db.py [--list] [--cleanup]

# Via podman exec
podman exec vb-api python scripts/backup_db.py [--list] [--cleanup]
```

**Parameter:**
- `--list` — listet alle verfügbaren Backup-Keys in S3 auf und beendet sich, ohne ein Backup zu erstellen.
- `--cleanup` — löscht nach einem erfolgreichen Backup zusätzlich Backups, die älter als `BACKUP_RETENTION_DAYS` sind (dieselbe Bereinigung wie im geplanten Job).

**Relevante Env-Vars:** `DATABASE_URL` (muss auf PostgreSQL zeigen), sowie die von `get_storage()` verwendeten `S3_*`-Vars. `BACKUP_RETENTION_DAYS` ist nur relevant, wenn `--cleanup` übergeben wird.

---

## `restore_db.py`

CLI-Wrapper um `backup_service.run_restore()`, um die PostgreSQL-Datenbank
aus einem in S3 abgelegten `pg_dump`-Backup wiederherzustellen. Lädt das
Backup-Objekt herunter, schreibt es in eine temporäre Datei und stellt es
via `pg_restore` wieder her. Wird kein konkretes Backup angegeben, wird der
alphabetisch letzte Key unter dem Backup-Präfix verwendet (Backup-Dateinamen
sind zeitstempel-sortierbar). Als Sicherheitsmaßnahme wird eine
Wiederherstellung bei `APP_ENVIRONMENT=production` verweigert, sofern nicht
explizit `--force` übergeben wird — da eine Restore die Live-Datenbank
überschreibt.

**Aufruf:**
```bash
# Im Container
python scripts/restore_db.py [--list] [--backup-name NAME] [--force]

# Via podman exec
podman exec vb-api python scripts/restore_db.py [--list] [--backup-name NAME] [--force]
```

**Parameter:**
- `--list` — listet alle verfügbaren Backup-Keys in S3 auf und beendet sich, ohne etwas wiederherzustellen.
- `--backup-name NAME` — stellt dieses konkrete Backup wieder her, statt automatisch das neueste zu wählen.
- `--force` — erforderlich, um bei `APP_ENVIRONMENT=production` fortzufahren; hat in anderen Umgebungen keine Wirkung.

**Relevante Env-Vars:** `DATABASE_URL` (muss auf PostgreSQL zeigen), `APP_ENVIRONMENT`, sowie die von `get_storage()` verwendeten `S3_*`-Vars.

---

## `sqlite2pg.py`

Idempotente einmalige Migration, die alle Daten aus der alten SQLite-
Datenbank nach PostgreSQL kopiert. Legt zunächst alle Tabellen in
PostgreSQL an (falls noch nicht vorhanden, via SQLAlchemy-Metadata), leert
sie (`TRUNCATE ... CASCADE`), kopiert dann jede Zeile tabellenweise in
Batches von 1000, wobei FK-/Trigger-Prüfungen vorübergehend deaktiviert
werden (`session_replication_role = 'replica'`), sodass die Reihenfolge der
Einfügungen keine Rolle spielt. Nach dem Kopieren werden Auto-Increment-
Sequenzen für Integer-Primärschlüssel auf `MAX(id) + 1` zurückgesetzt,
damit künftige Inserts nicht mit migrierten Zeilen kollidieren. Ein erneuter
Lauf ist gefahrlos möglich — das Script beginnt immer mit einem sauberen
Truncate.

**Aufruf:**
```bash
# Im Container
python scripts/sqlite2pg.py

# Via podman exec
podman exec vb-api python scripts/sqlite2pg.py
```

**Parameter:** keine — das Script ist nicht-interaktiv und kennt keine CLI-Flags.

**Relevante Env-Vars:** `DATABASE_URL` (muss eine PostgreSQL-URL sein — sonst bricht das Script ab). Der SQLite-Quellpfad ist fest auf `/database/legacy_db.sqlite3` gesetzt.
