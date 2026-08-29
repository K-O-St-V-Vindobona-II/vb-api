# Scripts

Operational scripts for the backend. All scripts are run manually (not part
of the request/response path) and expect to be executed from the `vb-api`
project root.

They read their configuration from environment variables (`DATABASE_URL`,
`S3_*`, ...) — normally the same `.env` the backend container uses. Each
script below lists two ways to run it:
- **Inside the container** — a shell already opened inside the running `vb-api` backend container (e.g. via `podman exec -it vb-api bash`), where the working directory is `/app`.
- **Via `podman exec`** — directly from the host, without opening a shell first.

---

## `check_s3_integrity.py`

Read-only consistency check between the database and S3. Reports two things:
1. **Completeness** — every `sha256_hash` referenced by a `StandesdbImage` or `ArchiveStoreItem` row must exist as an object in S3; missing ones are printed and cause a non-zero exit code.
2. **Orphans** — S3 objects under the image/store prefixes that are referenced by no DB row at all (active or soft-deleted), listed with size/content-type/last-modified for manual review.

Both checks compare against a bulk `list_objects_v2` listing of each prefix
(done once, ~1 request per 1000 objects) rather than issuing one
`head_object()` call per DB row — with tens of thousands of rows, per-row
HEAD requests took tens of minutes over the network; the bulk-listing
comparison takes seconds. Only metadata is read either way — it never
downloads file contents, so S3 cost is negligible regardless.

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

## `downsync_prod.py`

Self-contained, two-step downsync onto the local non-prod stage — no
delegation to another script. Step 1 mirrors the **entire** production
`vindobona2-at` AWS S3 bucket into local MinIO: an exact 1:1 clone, since
source and dest already share the same key structure (no legacy prefix
remapping needed, unlike the retired `downsync_from_prod_aws.py`). Objects
that exist locally but not in the prod source are deleted (mirror mode)
unless `--no-delete` is passed. Step 2 restores the local PostgreSQL
database from local MinIO's now-current `db-backups/` prefix — i.e. from
whatever step 1 just brought down from prod — reusing
`backup_service.run_restore()` exactly like `restore_db.py` does, then runs
`alembic upgrade head`. The DB step therefore never talks to prod directly;
it only ever reads local storage, which is why step 1 must run before step
2 whenever both are enabled. Refuses to run at all when
`APP_ENVIRONMENT=production` (hard guard, no override), since this combines
two operations that are each individually destructive against whichever
stage they target. Asks for an interactive "yes" confirmation before doing
anything, unless `--yes` is passed. `podman exec` without `-it` has no TTY
attached, so the prompt has no stdin to read from — it now fails fast with
a clear error telling you to add `-it` or pass `--yes`, instead of hanging.

Must run **inside the container** — `pg_restore` and `alembic` are only
installed there, not on the host.

**Usage:**
```bash
# Inside the container (an interactive shell already has a TTY)
python scripts/downsync_prod.py
python scripts/downsync_prod.py --dry-run
python scripts/downsync_prod.py --yes
python scripts/downsync_prod.py --skip-db
python scripts/downsync_prod.py --skip-s3 --no-delete

# Via podman exec - no TTY attached by default, so pick one:
podman exec -it vb-api python scripts/downsync_prod.py   # interactive prompt
podman exec vb-api python scripts/downsync_prod.py --yes  # non-interactive
```

**Parameters:**
- `--dry-run` — S3 step: print what would be copied/deleted without performing the sync. DB step: only print the backup that's currently newest in local MinIO (i.e. what a real run would restore), without downloading/restoring it.
- `--yes` — skip the interactive confirmation prompt.
- `--skip-db` — skip the DB restore step entirely.
- `--skip-s3` — skip the S3 mirror step entirely (prod AWS credentials are then not loaded at all, since the DB step only needs local storage).
- `--no-delete` — S3 step only: sync new/changed files but do not delete local orphans.

**Relevant env vars:** `DATABASE_URL` (restore target, must be PostgreSQL), `APP_ENVIRONMENT` (must not be `production`), `S3_ENDPOINT_URL`/`S3_ACCESS_KEY`/`S3_SECRET_KEY`/`S3_BUCKET` (local MinIO, used by both the mirror destination and the DB restore source). Prod AWS source credentials for the S3 step are `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`/`AWS_BUCKET=vindobona2-at` (a read-only IAM user scoped to the prod bucket only) — this script only reads them via `get_settings()`, agnostic to how they reach the process environment; see `vb-deploy/README.md` for how each environment wires them up.

---

## `maintain_deleted_archive_files.py`

Inspects, restores, or hard-deletes soft-deleted archive files. Hard-delete
removes data from **both** the database and S3 — the only way to
permanently remove an archive file's data, since the regular API only ever
soft-deletes. Deliberately CLI-only by design: none of this must ever be
exposed via the API or frontend (see
`app/services/archive_maintenance_service.py`'s module docstring, which
enforces that as an explicit rule, not just a convention).

Called without a subcommand, prints a short usage text — nothing is read
from the database and nothing is changed.

**`list [dir_id] [--short]`** — read-only. Groups every currently
soft-deleted archive file by directory: one paragraph per directory — its
path and dir_id (the same id `purge-duplicates` and `list-duplicates --dir`
expect) — followed by that directory's files (id, content hash, S3 impact,
file name/extension, size, description, deletion timestamp). Long file
names/descriptions are truncated so the columns never shift. With `dir_id`,
only that one directory's paragraph is shown. `--short` collapses each
paragraph to a single `path (dir_id: N): <count>` line — a fast triage
overview when hundreds of files are scattered across many directories. No
retention/grace period — every currently soft-deleted file appears in that
list, so the operator can judge for themselves whether anything looks too
recent to touch.

`archive_store_item_id` is not unique on `ArchiveFile` — several files
(active and/or soft-deleted) can reference the same underlying S3 object
(content dedup inherited from the legacy migration). Every listing therefore
classifies each file's **S3 impact**:
- **duplicate** — an active file still references the same content; purging
  this file never touches S3.
- **shared** — no active file, but another soft-deleted file still
  references the same content; S3 stays untouched for now, but the last
  remaining reference in that group will remove it.
- **sole** (shown as `SOLE`) — no other reference at all; purging this file
  deletes the underlying S3 object immediately.

**`purge <id>`** — **destructive, S3-relevant.** First re-checks that the
given id is currently soft-deleted (i.e. actually purgeable — rejected
otherwise, before any prompt is shown, nothing deleted). Only after that
check passes does it ask for interactive confirmation (with an
impact-specific note or warning, depending on the category above, and the
confirmation prompt itself states whether this purge affects "DB only" or
"DB AND S3"); there is no flag to skip it. The database row is then
hard-deleted first (cascading its comments) and committed — only *after*
that commit succeeds does the script attempt to delete the underlying S3
object(s), and only if no other file anywhere still references the same
content hash (`ArchiveStoreItem.sha256_hash`). Cached thumbnail variants are
cleaned up via a prefix listing, not hardcoded sizes, so a future
thumbnail-cache version bump can't leave orphans behind. This DB-first
ordering is deliberate: a failed S3 delete after a committed DB delete
leaves at worst a harmless, self-diagnosable orphan (the kind
`check_s3_integrity.py` already detects) — the reverse order risks silent
data loss for other files still sharing that content.

**`purge-duplicates <dir_id>`** — **destructive to the DB (hard-deletes
rows, irreversibly), but S3-safe by construction.** Batch-purges only the
`duplicate` files directly in that directory (direct children only, not
recursive — subdirectories need their own run) after **one** confirmation
for the whole batch — files where an active copy guarantees the S3 object
survives; since purging deleted files never touches active ones, a file's
`duplicate` status can't flip mid-batch — but each file is still freshly
re-checked immediately before its own purge, so a concurrent soft-delete of
its active sibling during the batch (a real possibility in production) is
caught and the file is skipped and reported instead of unexpectedly
deleting S3 data. Any remaining `shared`/`sole` files in that directory
(which WOULD be S3-relevant) are only *reported* (a count, pointing at
`list-duplicates`/`purge <id>`) — they are **never** processed
automatically.

**`restore <file_id>`** — reversible, non-destructive. Clears `deleted_at`,
undoing a soft-delete. Runs **immediately, without a confirmation prompt** —
unlike purge, restoring is safe and trivially undoable again via the GUI.
Errors (id not found, or not currently soft-deleted) go to stderr with exit
code 1.

**`list-duplicates --file FILE_ID [--file FILE_ID ...] --dir DIR_ID [--dir
DIR_ID ...]`** — read-only. `--file` shows the active files sharing content
with the given file (any status — active or soft-deleted itself); `--dir`
does the same for every currently soft-deleted file directly in that
directory. Both flags are repeatable and combinable in one call, e.g.
`list-duplicates --file 1 --file 2 --dir 10 --dir 11`. Each file gets a
two-part block, both using the same ID/PATH/FILENAME table: `DELETED FILE:`
or `ACTIVE FILE:` (whichever the file's actual current state is) for the
file itself, then `DUPLICATES:` for its active duplicates (or `(none)`).
Multiple blocks in one run are separated by a `=====` delimiter. A file
with zero active duplicates is a normal result, not an error — an error is
only reported (and only skips that one entry) for a `--file` id that
doesn't exist at all. At least one `--file`/`--dir` is required.

**`urlpath <id>`** — read-only convenience lookup. Looks the given id up as
**both** a file id and a directory id and, for whichever match(es), prints
its archive path and the frontend's relative URL path
(`/archive/files/<id>` or `/archive/dirs/<id>`) — just the path, not a full
URL, since the frontend domain differs per environment and isn't reliably
known to this script. Error only if neither a file nor a directory with
that id exists.

Runs in **every** environment, including production — unlike
`downsync_prod.py`, this is not dev-only tooling but the actual production
maintenance mechanism for accumulated soft-deleted files, so there is no
environment guard.

**Usage:**
```bash
# Inside the container
python scripts/maintain_deleted_archive_files.py
python scripts/maintain_deleted_archive_files.py list
python scripts/maintain_deleted_archive_files.py list --short
python scripts/maintain_deleted_archive_files.py list 7
python scripts/maintain_deleted_archive_files.py purge 42
python scripts/maintain_deleted_archive_files.py purge-duplicates 7
python scripts/maintain_deleted_archive_files.py restore 42
python scripts/maintain_deleted_archive_files.py list-duplicates --file 42 --dir 7
python scripts/maintain_deleted_archive_files.py urlpath 42

# Via podman exec
podman exec vb-api python scripts/maintain_deleted_archive_files.py list
podman exec -it vb-api python scripts/maintain_deleted_archive_files.py purge 42
podman exec -it vb-api python scripts/maintain_deleted_archive_files.py purge-duplicates 7
podman exec -it vb-api python scripts/maintain_deleted_archive_files.py restore 42
```

**Subcommands:**
- *(none)* — prints a short usage text, no DB/S3 access.
- `list [dir_id] [--short]` — read-only, see above.
- `purge <id>` — destructive/S3-relevant, permanently deletes exactly one file from DB and S3, after verifying it appears in `list` and after an interactive `yes` confirmation. Not purgeable (not currently soft-deleted) → error message, exit code 1, nothing is touched.
- `purge-duplicates <dir_id>` — destructive to the DB, S3-safe by construction, scoped to the `duplicate` category only, see above.
- `restore <file_id>` — reversible/non-destructive, no confirmation, see above.
- `list-duplicates --file ... --dir ...` — read-only, see above.
- `urlpath <id>` — read-only, see above.

**Relevant env vars:** `DATABASE_URL` (must point to PostgreSQL), plus the `S3_*` vars used by `get_storage()`.

---

# Scripts (Deutsch)

Betriebs-Scripts für das Backend. Alle Scripts werden manuell ausgeführt
(sind nicht Teil des Request/Response-Pfads) und gehen davon aus, dass sie
aus dem `vb-api`-Projekt-Root heraus gestartet werden.

Sie beziehen ihre Konfiguration aus Umgebungsvariablen (`DATABASE_URL`,
`S3_*`, ...) — normalerweise dieselbe `.env`, die auch der Backend-Container
verwendet. Für jedes Script unten sind zwei Aufrufwege beschrieben:
- **Im Container** — eine bereits im laufenden `vb-api`-Backend-Container geöffnete Shell (z. B. via `podman exec -it vb-api bash`), Arbeitsverzeichnis ist `/app`.
- **Via `podman exec`** — direkter Aufruf vom Host aus, ohne vorher eine Shell zu öffnen.

---

## `check_s3_integrity.py`

Read-only-Konsistenzprüfung zwischen Datenbank und S3. Meldet zwei Dinge:
1. **Vollständigkeit** — jeder von einer `StandesdbImage`- oder `ArchiveStoreItem`-Zeile referenzierte `sha256_hash` muss als Objekt in S3 existieren; fehlende werden ausgegeben und führen zu einem Exit-Code ungleich 0.
2. **Waisen** — S3-Objekte unter den Image-/Store-Präfixen, die von keiner DB-Zeile referenziert werden (weder aktiv noch soft-deleted), aufgelistet mit Größe/Content-Type/Änderungsdatum zur manuellen Prüfung.

Beide Prüfungen vergleichen gegen ein gebündeltes `list_objects_v2`-Listing
je Präfix (einmalig, ~1 Request pro 1000 Objekte) statt für jede DB-Zeile
einen eigenen `head_object()`-Aufruf zu machen — bei Zehntausenden Zeilen
dauerten Einzel-Requests über das Netzwerk mehrere zehn Minuten, der
gebündelte Vergleich braucht Sekunden. In beiden Fällen werden nur
Metadaten gelesen, nie Dateiinhalte heruntergeladen — die S3-Kosten sind
also so oder so vernachlässigbar.

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

## `downsync_prod.py`

Autarker, zweistufiger Downsync auf die lokale Non-Prod-Stage — keine
Delegation an ein anderes Skript. Schritt 1 spiegelt den **kompletten**
produktiven AWS-S3-Bucket `vindobona2-at` 1:1 in das lokale MinIO: ein
exakter Klon, da Quelle und Ziel bereits dieselbe Key-Struktur nutzen (kein
Legacy-Prefix-Remapping mehr nötig, anders als beim entfernten
`downsync_from_prod_aws.py`). Objekte, die lokal existieren, aber nicht in
der Prod-Quelle, werden gelöscht (Mirror-Modus), außer `--no-delete` wird
übergeben. Schritt 2 stellt die lokale PostgreSQL-Datenbank aus dem jetzt
aktuellen `db-backups/`-Prefix des lokalen MinIO wieder her — also aus dem,
was Schritt 1 gerade erst von Prod heruntergebracht hat —, nutzt dafür
exakt `backup_service.run_restore()` wie `restore_db.py`, führt danach
`alembic upgrade head` aus. Der DB-Schritt spricht daher nie direkt mit
Prod, sondern liest ausschließlich lokalen Storage — deshalb muss Schritt 1
vor Schritt 2 laufen, sofern beide aktiv sind. Verweigert den Start
komplett, wenn `APP_ENVIRONMENT=production` gesetzt ist (harter Guard, kein
Override), da hier zwei Operationen kombiniert werden, die jede für sich
bereits destruktiv gegen die jeweils angezielte Stage sind. Fragt vor jeder
Aktion interaktiv per "yes"-Bestätigung nach, außer `--yes` wird übergeben.
`podman exec` ohne `-it` hat kein angebundenes TTY, die Abfrage findet also
kein stdin zum Lesen — sie bricht dann sofort mit einer klaren Fehlermeldung
ab (statt zu hängen) und verweist auf `-it` bzw. `--yes`.

Muss **im Container** laufen — `pg_restore` und `alembic` sind nur dort
installiert, nicht auf dem Host.

**Aufruf:**
```bash
# Im Container (eine interaktive Shell hat bereits ein TTY)
python scripts/downsync_prod.py
python scripts/downsync_prod.py --dry-run
python scripts/downsync_prod.py --yes
python scripts/downsync_prod.py --skip-db
python scripts/downsync_prod.py --skip-s3 --no-delete

# Via podman exec - standardmäßig kein TTY angebunden, also eine Variante wählen:
podman exec -it vb-api python scripts/downsync_prod.py    # interaktive Abfrage
podman exec vb-api python scripts/downsync_prod.py --yes  # non-interaktiv
```

**Parameter:**
- `--dry-run` — S3-Schritt: zeigt an, was kopiert/gelöscht würde, ohne den Sync auszuführen. DB-Schritt: gibt nur das im lokalen MinIO aktuell neueste Backup aus (also das, was ein echter Lauf wiederherstellen würde), ohne es herunterzuladen/wiederherzustellen.
- `--yes` — überspringt die interaktive Bestätigungsabfrage.
- `--skip-db` — überspringt den DB-Wiederherstellungsschritt komplett.
- `--skip-s3` — überspringt den S3-Mirror-Schritt komplett (Prod-AWS-Credentials werden dann gar nicht erst geladen, da der DB-Schritt nur lokalen Storage braucht).
- `--no-delete` — nur S3-Schritt: synct neue/geänderte Dateien, überspringt aber das Löschen lokaler Waisen.

**Relevante Env-Vars:** `DATABASE_URL` (Restore-Ziel, muss PostgreSQL sein), `APP_ENVIRONMENT` (darf nicht `production` sein), `S3_ENDPOINT_URL`/`S3_ACCESS_KEY`/`S3_SECRET_KEY`/`S3_BUCKET` (lokales MinIO, sowohl Mirror-Ziel als auch DB-Restore-Quelle). Die Prod-AWS-Quell-Credentials für den S3-Schritt sind `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`/`AWS_BUCKET=vindobona2-at` (ein rein lesender IAM-User, nur auf den Prod-Bucket beschränkt) — dieses Skript liest sie nur über `get_settings()`, unabhängig davon, wie sie in die Prozessumgebung gelangen; siehe `vb-deploy/README.md` für die Verdrahtung je Umgebung.

---

## `maintain_deleted_archive_files.py`

Inspiziert, stellt wieder her oder löscht soft-gelöschte Archiv-Dateien hart.
Hard-Delete entfernt Daten aus **sowohl** Datenbank als auch S3 — der
einzige Weg, die Daten einer Archiv-Datei endgültig zu entfernen, da die
reguläre API ausschließlich soft-deleted. Bewusst per Design nur per
Kommandozeile: nichts davon darf jemals über die API oder das Frontend
angeboten werden (siehe der Modul-Docstring von
`app/services/archive_maintenance_service.py`, der das als explizite Regel
festhält, nicht nur als Konvention).

Ohne Subcommand aufgerufen, gibt es nur einen kurzen Hilfetext aus — es wird
weder die Datenbank angefragt noch irgendetwas verändert.

**`list [dir_id] [--short]`** — rein lesend. Gruppiert jede aktuell
soft-gelöschte Archiv-Datei nach Verzeichnis: ein Absatz pro Verzeichnis —
Pfad und Verzeichnis-ID (dieselbe ID, die `purge-duplicates` und
`list-duplicates --dir` erwarten) — gefolgt von dessen Dateien (ID,
Content-Hash, S3-Auswirkung, Dateiname/-endung, Größe, Beschreibung,
Löschzeitpunkt). Lange Dateinamen/Beschreibungen werden gekürzt, damit die
Spalten nie verrutschen. Mit `dir_id` wird nur der Absatz dieses einen
Verzeichnisses gezeigt. `--short` reduziert jeden Absatz auf eine einzelne
Zeile `Pfad (dir_id: N): <Anzahl>` — eine schnelle Triage-Übersicht, wenn
hunderte Dateien über viele Verzeichnisse verstreut sind. Keine Karenzzeit —
jede aktuell soft-gelöschte Datei erscheint in dieser Liste, damit der
Operator selbst beurteilen kann, ob etwas zu frisch aussieht, um es
anzufassen.

`archive_store_item_id` ist auf `ArchiveFile` nicht eindeutig — mehrere
Dateien (aktiv und/oder soft-gelöscht) können dasselbe S3-Objekt
referenzieren (Content-Dedup, ein Erbe der Legacy-Migration). Jede Auflistung
klassifiziert deshalb die **S3-Auswirkung** pro Datei:
- **duplicate** — mindestens eine aktive Datei referenziert denselben Inhalt
  noch; das Purgen dieser Datei berührt S3 nie.
- **shared** — keine aktive Datei, aber mindestens eine weitere
  soft-gelöschte Datei referenziert denselben Inhalt noch; S3 bleibt jetzt
  unangetastet, aber die letzte verbleibende Referenz dieser Gruppe entfernt
  es.
- **sole** (angezeigt als `SOLE`) — keine andere Referenz mehr vorhanden;
  das Purgen dieser Datei löscht das zugehörige S3-Objekt sofort.

**`purge <id>`** — **destruktiv, S3-relevant.** Prüft zuerst erneut, ob die
angegebene ID aktuell soft-gelöscht (also tatsächlich purgeable) ist —
alles andere wird abgelehnt, bevor irgendeine Abfrage erscheint, und nichts
wird gelöscht. Erst nach dieser Prüfung fragt das Script interaktiv nach
Bestätigung (mit einem kategorieabhängigen Hinweis bzw. einer Warnung,
siehe oben; die Bestätigungszeile selbst nennt außerdem, ob der Purge nur
die DB oder DB UND S3 betrifft); einen Parameter zum Überspringen gibt es
nicht. Danach wird zuerst die DB-Zeile hart gelöscht (kaskadiert auf ihre
Kommentare) und committet — **erst danach** versucht das Script, das
zugehörige S3-Objekt zu löschen, und auch nur dann, wenn keine andere Datei
im System noch denselben Content-Hash (`ArchiveStoreItem.sha256_hash`)
referenziert. Gecachte Thumbnail-Varianten werden über eine
Prefix-Auflistung bereinigt, nicht über hartkodierte Größen — ein künftiges
Thumbnail-Cache-Versions-Update kann so keine Leichen hinterlassen. Diese
DB-zuerst-Reihenfolge ist bewusst: Ein fehlgeschlagenes S3-Löschen nach
bereits committetem DB-Löschen hinterlässt bestenfalls ein harmloses,
selbst-diagnostizierbares Waisenobjekt (genau die Art, die
`check_s3_integrity.py` bereits erkennt) — die umgekehrte Reihenfolge
riskiert stillen Datenverlust für andere Dateien, die denselben Inhalt noch
teilen.

**`purge-duplicates <dir_id>`** — **destruktiv für die DB (löscht Zeilen
unwiderruflich hart), aber S3-sicher per Konstruktion.** Purged im Batch
ausschließlich die `duplicate`-Dateien direkt in diesem Verzeichnis (nur
direkte Kinder, nicht rekursiv — Unterverzeichnisse brauchen einen eigenen
Aufruf) nach **einer** Bestätigung für den gesamten Batch — Dateien, bei
denen eine aktive Kopie das Überleben des S3-Objekts garantiert; da das
Purgen gelöschter Dateien nie aktive Dateien anfasst, kann sich der
`duplicate`-Status einer Datei während des Batches nicht ändern — trotzdem
wird jede Datei unmittelbar vor ihrem eigenen Purge nochmal frisch geprüft,
damit ein gleichzeitiges Soft-Delete ihrer aktiven Zwillingsdatei während
des Batch-Laufs (im Produktivbetrieb real möglich) abgefangen wird: die
Datei wird übersprungen und gemeldet, statt unerwartet S3-Daten zu löschen.
Verbleibende `shared`/`sole`-Dateien desselben Verzeichnisses (die SEHR WOHL
S3-relevant wären) werden nur noch *gemeldet* (Anzahl, Verweis auf
`list-duplicates`/`purge <id>`) — sie werden **nicht** mehr automatisch
bearbeitet.

**`restore <file_id>`** — reversibel, nicht-destruktiv. Setzt `deleted_at`
zurück, macht ein Soft-Delete rückgängig. Läuft **sofort, ohne
Bestätigungs-Prompt** — anders als Purge ist Restore ungefährlich und
jederzeit über die GUI wieder rückgängig zu machen. Fehler (ID nicht
gefunden, oder nicht aktuell soft-gelöscht) gehen auf stderr, Exit-Code 1.

**`list-duplicates --file FILE_ID [--file FILE_ID ...] --dir DIR_ID [--dir
DIR_ID ...]`** — rein lesend. `--file` zeigt die aktiven Dateien, die
denselben Inhalt wie die angegebene Datei teilen (beliebiger Status — aktiv
oder selbst soft-gelöscht); `--dir` macht dasselbe für jede aktuell
soft-gelöschte Datei direkt in diesem Verzeichnis. Beide Flags sind
wiederholbar und in einem Aufruf kombinierbar, z. B. `list-duplicates
--file 1 --file 2 --dir 10 --dir 11`. Jede Datei bekommt einen zweiteiligen
Block, beide mit derselben ID/PATH/FILENAME-Tabelle: `DELETED FILE:` oder
`ACTIVE FILE:` (je nach tatsächlichem aktuellem Zustand der Datei) für die
Datei selbst, danach `DUPLICATES:` für ihre aktiven Duplikate (oder
`(none)`). Mehrere Blöcke in einem Lauf werden durch einen `=====`-Delimiter
getrennt. Eine Datei mit null aktiven Duplikaten ist ein normales Ergebnis,
kein Fehler — ein Fehler wird nur (und überspringt auch nur diesen einen
Eintrag) für eine `--file`-ID gemeldet, die gar nicht existiert. Mindestens
ein `--file`/`--dir` ist Pflicht.

**`urlpath <id>`** — rein lesende Komfort-Abfrage. Sucht die angegebene ID
**sowohl** als file_id als auch als dir_id und gibt für jeden Treffer den
Archiv-Pfad sowie den relativen Frontend-URL-Pfad aus (`/archive/files/<id>`
bzw. `/archive/dirs/<id>`) — nur den Pfad, keine vollständige URL, da die
Frontend-Domain je nach Umgebung unterschiedlich und für dieses Skript nicht
zuverlässig bekannt ist. Fehler nur, wenn weder eine Datei noch ein
Verzeichnis mit dieser ID existiert.

Läuft in **jeder** Umgebung, auch in Produktion — anders als
`downsync_prod.py` ist das kein reines Dev-Tooling, sondern der tatsächliche
Produktions-Wartungsmechanismus für angesammelte soft-gelöschte Dateien,
daher gibt es keinen Umgebungs-Guard.

**Aufruf:**
```bash
# Im Container
python scripts/maintain_deleted_archive_files.py
python scripts/maintain_deleted_archive_files.py list
python scripts/maintain_deleted_archive_files.py list --short
python scripts/maintain_deleted_archive_files.py list 7
python scripts/maintain_deleted_archive_files.py purge 42
python scripts/maintain_deleted_archive_files.py purge-duplicates 7
python scripts/maintain_deleted_archive_files.py restore 42
python scripts/maintain_deleted_archive_files.py list-duplicates --file 42 --dir 7
python scripts/maintain_deleted_archive_files.py urlpath 42

# Via podman exec
podman exec vb-api python scripts/maintain_deleted_archive_files.py list
podman exec -it vb-api python scripts/maintain_deleted_archive_files.py purge 42
podman exec -it vb-api python scripts/maintain_deleted_archive_files.py purge-duplicates 7
podman exec -it vb-api python scripts/maintain_deleted_archive_files.py restore 42
```

**Subcommands:**
- *(keins)* — gibt nur einen kurzen Hilfetext aus, kein DB-/S3-Zugriff.
- `list [dir_id] [--short]` — rein lesend, siehe oben.
- `purge <id>` — destruktiv/S3-relevant, löscht genau eine Datei dauerhaft aus DB und S3, nachdem geprüft wurde, dass sie in `list` erscheint, und nach interaktiver `yes`-Bestätigung. Nicht purgeable (nicht aktuell soft-gelöscht) → Fehlermeldung, Exit-Code 1, nichts wird angefasst.
- `purge-duplicates <dir_id>` — destruktiv für die DB, S3-sicher per Konstruktion, auf die `duplicate`-Kategorie begrenzt, siehe oben.
- `restore <file_id>` — reversibel/nicht-destruktiv, keine Bestätigung, siehe oben.
- `list-duplicates --file ... --dir ...` — rein lesend, siehe oben.
- `urlpath <id>` — rein lesend, siehe oben.

**Relevante Env-Vars:** `DATABASE_URL` (muss auf PostgreSQL zeigen), sowie die von `get_storage()` verwendeten `S3_*`-Vars.
