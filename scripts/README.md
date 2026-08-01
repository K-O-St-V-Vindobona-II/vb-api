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
anything, unless `--yes` is passed.

Must run **inside the container** — `pg_restore` and `alembic` are only
installed there, not on the host.

**Usage:**
```bash
# Inside the container
python scripts/downsync_prod.py
python scripts/downsync_prod.py --dry-run
python scripts/downsync_prod.py --yes
python scripts/downsync_prod.py --skip-db
python scripts/downsync_prod.py --skip-s3 --no-delete

# Via podman exec
podman exec vb-api python scripts/downsync_prod.py
podman exec -it vb-api python scripts/downsync_prod.py
```

**Parameters:**
- `--dry-run` — S3 step: print what would be copied/deleted without performing the sync. DB step: only print the backup that's currently newest in local MinIO (i.e. what a real run would restore), without downloading/restoring it.
- `--yes` — skip the interactive confirmation prompt.
- `--skip-db` — skip the DB restore step entirely.
- `--skip-s3` — skip the S3 mirror step entirely (prod AWS credentials are then not loaded at all, since the DB step only needs local storage).
- `--no-delete` — S3 step only: sync new/changed files but do not delete local orphans.

**Relevant env vars:** `DATABASE_URL` (restore target, must be PostgreSQL), `APP_ENVIRONMENT` (must not be `production`), `S3_ENDPOINT_URL`/`S3_ACCESS_KEY`/`S3_SECRET_KEY`/`S3_BUCKET` (local MinIO, used by both the mirror destination and the DB restore source). Prod AWS source credentials for the S3 step come from `/run/secrets/aws-prod.env` (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_BUCKET=vindobona2-at`).

---

## `purge_deleted_archive_files.py`

Hard-deletes soft-deleted archive files from **both** the database and S3 —
the only way to permanently remove an archive file's data, since the
regular API only ever soft-deletes. Deliberately CLI-only by design: this
functionality must never be exposed via the API or frontend (see
`app/services/archive_purge_service.py`'s module docstring, which enforces
that as an explicit rule, not just a convention).

Called without a subcommand, prints a short usage text — nothing is read
from the database and nothing is deleted. `list` shows every currently
soft-deleted archive file (id, deletion timestamp, size, content hash,
path, description, uploader). No retention/grace period — every currently
soft-deleted file appears in that list, so the operator can judge for
themselves whether anything looks too recent to touch.

`purge <id>` first re-checks that the given id is still present in the
`list` output (i.e. actually purgeable) — anything else is rejected before
any prompt is shown, and nothing is deleted. Only after that check passes
does it ask for interactive confirmation; there is no flag to skip it. The
database row is then hard-deleted first (cascading its comments) and
committed — only *after* that commit succeeds does the script attempt to
delete the underlying S3 object(s), and only if no other file anywhere
still references the same content hash (`ArchiveStoreItem.sha256_hash`).
Cached thumbnail variants are cleaned up via a prefix listing, not
hardcoded sizes, so a future thumbnail-cache version bump can't leave
orphans behind. This DB-first ordering is deliberate: a failed S3 delete
after a committed DB delete leaves at worst a harmless, self-diagnosable
orphan (the kind `check_s3_integrity.py` already detects) — the reverse
order risks silent data loss for other files still sharing that content.

Runs in **every** environment, including production — unlike
`downsync_prod.py`, this is not dev-only tooling but the actual production
cleanup mechanism for accumulated soft-deleted files, so there is no
environment guard.

**Usage:**
```bash
# Inside the container
python scripts/purge_deleted_archive_files.py
python scripts/purge_deleted_archive_files.py list
python scripts/purge_deleted_archive_files.py purge 42

# Via podman exec
podman exec vb-api python scripts/purge_deleted_archive_files.py list
podman exec -it vb-api python scripts/purge_deleted_archive_files.py purge 42
```

**Subcommands:**
- *(none)* — prints a short usage text, no DB/S3 access.
- `list` — prints every currently soft-deleted (purgeable) archive file.
- `purge <id>` — permanently deletes exactly one file from DB and S3, after verifying it appears in `list` and after an interactive `yes` confirmation. Not purgeable (not currently soft-deleted) → error message, exit code 1, nothing is touched.

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

Muss **im Container** laufen — `pg_restore` und `alembic` sind nur dort
installiert, nicht auf dem Host.

**Aufruf:**
```bash
# Im Container
python scripts/downsync_prod.py
python scripts/downsync_prod.py --dry-run
python scripts/downsync_prod.py --yes
python scripts/downsync_prod.py --skip-db
python scripts/downsync_prod.py --skip-s3 --no-delete

# Via podman exec
podman exec vb-api python scripts/downsync_prod.py
podman exec -it vb-api python scripts/downsync_prod.py
```

**Parameter:**
- `--dry-run` — S3-Schritt: zeigt an, was kopiert/gelöscht würde, ohne den Sync auszuführen. DB-Schritt: gibt nur das im lokalen MinIO aktuell neueste Backup aus (also das, was ein echter Lauf wiederherstellen würde), ohne es herunterzuladen/wiederherzustellen.
- `--yes` — überspringt die interaktive Bestätigungsabfrage.
- `--skip-db` — überspringt den DB-Wiederherstellungsschritt komplett.
- `--skip-s3` — überspringt den S3-Mirror-Schritt komplett (Prod-AWS-Credentials werden dann gar nicht erst geladen, da der DB-Schritt nur lokalen Storage braucht).
- `--no-delete` — nur S3-Schritt: synct neue/geänderte Dateien, überspringt aber das Löschen lokaler Waisen.

**Relevante Env-Vars:** `DATABASE_URL` (Restore-Ziel, muss PostgreSQL sein), `APP_ENVIRONMENT` (darf nicht `production` sein), `S3_ENDPOINT_URL`/`S3_ACCESS_KEY`/`S3_SECRET_KEY`/`S3_BUCKET` (lokales MinIO, sowohl Mirror-Ziel als auch DB-Restore-Quelle). Die Prod-AWS-Quell-Credentials für den S3-Schritt kommen aus `/run/secrets/aws-prod.env` (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_BUCKET=vindobona2-at`).

---

## `purge_deleted_archive_files.py`

Löscht soft-gelöschte Archiv-Dateien hart aus **sowohl** Datenbank als auch
S3 — der einzige Weg, die Daten einer Archiv-Datei endgültig zu entfernen,
da die reguläre API ausschließlich soft-deleted. Bewusst per Design nur per
Kommandozeile: diese Funktion darf niemals über die API oder das Frontend
angeboten werden (siehe der Modul-Docstring von
`app/services/archive_purge_service.py`, der das als explizite Regel
festhält, nicht nur als Konvention).

Ohne Subcommand aufgerufen, gibt es nur einen kurzen Hilfetext aus — es wird
weder die Datenbank angefragt noch irgendetwas gelöscht. `list` zeigt jede
aktuell soft-gelöschte Archiv-Datei (ID, Löschzeitpunkt, Größe, Content-Hash,
Pfad, Beschreibung, Hochlader). Keine Karenzzeit — jede aktuell
soft-gelöschte Datei erscheint in dieser Liste, damit der Operator selbst
beurteilen kann, ob etwas zu frisch aussieht, um es anzufassen.

`purge <id>` prüft zuerst erneut, ob die angegebene ID noch in der
`list`-Ausgabe vorkommt (also tatsächlich purgeable ist) — alles andere wird
abgelehnt, bevor irgendeine Abfrage erscheint, und nichts wird gelöscht.
Erst nach dieser Prüfung fragt das Script interaktiv nach Bestätigung; einen
Parameter zum Überspringen gibt es nicht. Danach wird zuerst die DB-Zeile
hart gelöscht (kaskadiert auf ihre Kommentare) und committet — **erst
danach** versucht das Script, das zugehörige S3-Objekt zu löschen, und auch
nur dann, wenn keine andere Datei im System noch denselben Content-Hash
(`ArchiveStoreItem.sha256_hash`) referenziert. Gecachte Thumbnail-Varianten
werden über eine Prefix-Auflistung bereinigt, nicht über hartkodierte
Größen — ein künftiges Thumbnail-Cache-Versions-Update kann so keine
Leichen hinterlassen. Diese DB-zuerst-Reihenfolge ist bewusst: Ein
fehlgeschlagenes S3-Löschen nach bereits committetem DB-Löschen hinterlässt
bestenfalls ein harmloses, selbst-diagnostizierbares Waisenobjekt (genau
die Art, die `check_s3_integrity.py` bereits erkennt) — die umgekehrte
Reihenfolge riskiert stillen Datenverlust für andere Dateien, die denselben
Inhalt noch teilen.

Läuft in **jeder** Umgebung, auch in Produktion — anders als
`downsync_prod.py` ist das kein reines Dev-Tooling, sondern der tatsächliche
Produktions-Bereinigungsmechanismus für angesammelte soft-gelöschte
Dateien, daher gibt es keinen Umgebungs-Guard.

**Aufruf:**
```bash
# Im Container
python scripts/purge_deleted_archive_files.py
python scripts/purge_deleted_archive_files.py list
python scripts/purge_deleted_archive_files.py purge 42

# Via podman exec
podman exec vb-api python scripts/purge_deleted_archive_files.py list
podman exec -it vb-api python scripts/purge_deleted_archive_files.py purge 42
```

**Subcommands:**
- *(keins)* — gibt nur einen kurzen Hilfetext aus, kein DB-/S3-Zugriff.
- `list` — listet jede aktuell soft-gelöschte (purgeable) Archiv-Datei auf.
- `purge <id>` — löscht genau eine Datei dauerhaft aus DB und S3, nachdem geprüft wurde, dass sie in `list` erscheint, und nach interaktiver `yes`-Bestätigung. Nicht purgeable (nicht aktuell soft-gelöscht) → Fehlermeldung, Exit-Code 1, nichts wird angefasst.

**Relevante Env-Vars:** `DATABASE_URL` (muss auf PostgreSQL zeigen), sowie die von `get_storage()` verwendeten `S3_*`-Vars.
