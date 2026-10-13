# Scripts

[English](README.md) | [Deutsch](README.de.md)

Reference for the operational scripts of `vb-api`. The big picture (what runs where, what to do in an incident, how to rebuild the system) is in the [`vb-deploy` README](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.md), which this document assumes you know; chapter [4.2](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.md#42-operational-scripts-in-vb-api) there lists the scripts in one table.

All scripts are run by hand, are not part of the request path, and expect the `vb-api` project root as working directory. They read their configuration from environment variables (`DATABASE_URL`, `S3_*`, ...), normally the same file the backend container uses. Every script has `--help`.

**Two ways to run a script:**

- **Inside the container**: in a shell opened in the running `vb-api` container (`podman exec -it vb-api bash`), where the working directory is `/app`.
- **Via `podman exec`**: directly from the host, without opening a shell. Without `-it` no terminal is attached, so a script that asks a question needs either `-it` or its `--yes` option (it stops with a clear error instead of hanging).

**Scripts always run in the container**, because `pg_dump`, `pg_restore` and `alembic` are installed only there, never on your own computer.

| Script | Purpose | Changes data? |
|---|---|---|
| [`check_s3_integrity.py`](#check_s3_integritypy) | Database against S3, both directions | No |
| [`backup_db.py`](#backup_dbpy) | PostgreSQL dump to S3 | Adds a dump |
| [`restore_db.py`](#restore_dbpy) | Restore the database from an S3 dump | **Replaces the database** |
| [`downsync_prod.py`](#downsync_prodpy) | Non-production only: mirror production, restore its newest dump | **Replaces data of the stage** |
| [`maintain_deleted_archive_files.py`](#maintain_deleted_archive_filespy) | List, restore and permanently delete soft-deleted archive files | `purge*` deletes for good |
| [`trigger_chronicles.py`](#trigger_chroniclespy) | Dry run or manual send of the chronicle mail | Sends mail with `--send` |
| [Developer helpers](#developer-helpers) | Test run with coverage gate, structure checks | No |

---

## `check_s3_integrity.py`

Read-only consistency check between the database and S3. It reports two things:

1. **Completeness:** every `sha256_hash` referenced by a `StandesdbImage` or `ArchiveStoreItem` row must exist as an object in S3. Missing ones are printed and the exit code is 1. If the check itself cannot finish (database or storage error), the error goes to stderr and the exit code is 2, so a broken run is never read as "missing files".
2. **Orphans:** S3 objects under the image and store prefixes that no database row references at all (active or soft-deleted), listed with size, content type and last-modified time for manual review.

Both checks compare against one bulk listing per prefix (about one request per 1000 objects) instead of one `head_object()` call per row, which takes seconds instead of many minutes. Only metadata is read, never file contents. The script never deletes anything; clean up orphans by hand in the S3 console.

```bash
# Inside the container
python scripts/check_s3_integrity.py

# Via podman exec
podman exec vb-api python scripts/check_s3_integrity.py
```

Expected on a healthy system: `0 missing file(s).` and `No orphaned files found.` for both the archive and the member database. Parameters: none. Environment: `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_PATH_STANDESDB_IMAGES`, `S3_PATH_ARCHIVE_STORE`, `DATABASE_URL`.

---

## `backup_db.py`

Writes a PostgreSQL dump (`pg_dump`, custom format) to S3, the same operation the scheduled job `db_backup` performs every night at `BACKUP_HOUR` (production only). Use it before risky changes, a cutover or a restore rehearsal. The file is named `<stage>-YYYY-MM-DD_HH-MM-SS-manual.dump` (Vienna time) under the prefix `db-backups/`. The data of the table `scheduled_task_runs` is deliberately left out: every stage keeps its own job history.

Deleting backups older than `BACKUP_RETENTION_DAYS` (default 29) is opt-in with `--cleanup`, so a manual backup never deletes other backups as a side effect.

```bash
# Via podman exec
podman exec vb-api python scripts/backup_db.py            # create a backup
podman exec vb-api python scripts/backup_db.py --list     # list the backups, create none
podman exec vb-api python scripts/backup_db.py --cleanup  # create one, then delete expired ones
```

Expected: `DB backup complete: production-<timestamp>-manual.dump`. Environment: `DATABASE_URL` (must be PostgreSQL) and the `S3_*` variables. `BACKUP_RETENTION_DAYS` matters only with `--cleanup`.

---

## `restore_db.py`

Restores the database from a dump in S3: downloads the dump, drops and recreates the `public` schema of the live database (terminating its other connections), loads the dump with `pg_restore`, checks that rows actually arrived and keeps this stage's own job history. Without `--backup-name` the newest dump is used (names sort by timestamp). On `APP_ENVIRONMENT=production` the restore is refused unless `--force` is given, because it replaces the live database.

```bash
podman exec vb-api python scripts/restore_db.py --list
podman exec vb-api python scripts/restore_db.py --force
podman exec vb-api python scripts/restore_db.py --force --backup-name production-2026-10-11_03-00-00.dump
```

Parameters: `--list` (print the backups and exit), `--backup-name NAME`, `--force` (required on production, no effect elsewhere). Expected: `Restore complete. Backup: <name>`.

**After a restore** the script does not migrate. A dump older than the code needs the newer migrations; then restart the pod, which also clears the connections the restore cut:

```bash
podman exec vb-api alembic upgrade head
systemctl --user restart vb-api-pod.service     # on the host
podman exec vb-api-pg psql -U vb -d vb -c 'SELECT count(*) FROM members;'   # expect a number > 0
```

The full incident procedure is in the `vb-deploy` README, [2.4](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.md#24-the-database-is-damaged-backup-and-restore). Environment: `DATABASE_URL`, `APP_ENVIRONMENT`, the `S3_*` variables.

---

## `downsync_prod.py`

Refreshes a **non-production** stage with real production data in two steps. Step 1 mirrors the entire production AWS S3 bucket into the stage's own Garage (an exact 1:1 copy; objects that exist locally but not in production are deleted, unless `--no-delete`). With `--light` it mirrors only the member images with their thumbnail cache, the public gallery and the newest database dump, in total a few dozen MB instead of the whole bucket (about 43 GB, almost all of it the archive); archive files are then missing on the stage, and objects outside these prefixes, including older dumps, are left alone. Step 2 restores the local database from the now current `db-backups/` prefix of the local Garage and runs `alembic upgrade head`. The database step never talks to production, it reads local storage only, which is why step 1 must run first when both are enabled.

The script refuses to run when `APP_ENVIRONMENT=production` (hard guard, no override). It asks for an interactive `yes` before doing anything unless `--yes` is passed. The same logic runs nightly as the scheduled job `downsync`.

```bash
# Inside the container (an interactive shell has a terminal)
python scripts/downsync_prod.py
python scripts/downsync_prod.py --dry-run
python scripts/downsync_prod.py --skip-db
python scripts/downsync_prod.py --skip-s3 --no-delete
python scripts/downsync_prod.py --light --dry-run --skip-db   # lists what the light mirror would copy

# Via podman exec: pick one of the two
podman exec -it vb-api python scripts/downsync_prod.py     # interactive confirmation
podman exec vb-api python scripts/downsync_prod.py --yes   # non-interactive
```

Parameters:

- `--dry-run`: S3 step: print what would be copied and deleted. DB step: print the dump that would be restored, restore nothing.
- `--yes`: skip the confirmation.
- `--skip-db`: skip the database step.
- `--skip-s3`: skip the S3 step (the production credentials are then not loaded at all).
- `--no-delete`: S3 step only: copy new and changed files but do not delete local orphans.
- `--light`: S3 step only: mirror only the member images, the public gallery and the newest database dump, not the archive. The environment variable `DOWNSYNC_LIGHT=true` has the same effect, also for the nightly job.

Environment: `DATABASE_URL`, `APP_ENVIRONMENT` (not `production`), `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` (the local Garage, destination and restore source). The production source credentials are `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` and `AWS_BUCKET`, a read-only IAM user for the production bucket only; how each stage receives them is in the `vb-deploy` README, [Appendix A](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.md#appendix-a-secrets-and-required-variables).

---

## `maintain_deleted_archive_files.py`

Inspects, restores and permanently deletes soft-deleted archive files. The regular API only ever soft-deletes; this script is the way to remove an archive file's data from **both** the database and S3. The one exception is a member deleting their own, still unfiled upload on the upload page (`DELETE /archive/upload/unfiled/{file_id}`); both paths share `archive_store_cleanup.hard_delete_file`. Everything else here is CLI-only by design and must never be exposed through the API or the frontend (the module docstring of `app/services/archive_maintenance_service.py` makes that an explicit rule). The script runs in **every** environment, production included, because it is the actual maintenance mechanism for accumulated soft-deleted files.

Called without a subcommand it prints a short usage text and touches nothing. **All ids are UUIDs.**

**S3 impact.** Several files (active or soft-deleted) can reference the same S3 object (content deduplication). Every listing classifies each file in its `IMPACT` column:

- **duplicate:** an active file still references the same content; purging this file never touches S3.
- **shared:** no active file, but another soft-deleted file references the same content; S3 stays untouched for now, the last remaining reference removes it.
- **sole** (shown as `SOLE`): no other reference; purging deletes the S3 object immediately.

**Subcommands**

| Subcommand | Effect |
|---|---|
| `list [dir_uuid] [--short]` | Read-only. One paragraph per directory (path and `dir_id`), then its files with id, content hash, S3 impact, name, size, description and deletion time. `dir_uuid` shows one directory only. `--short` prints one line per directory (`path (dir_id: <uuid>): <count>`) for a fast overview. There is no grace period: every currently soft-deleted file is listed. |
| `purge <file_uuid>` | **Destructive, S3-relevant.** Checks that the file is currently soft-deleted (otherwise error, nothing deleted, no question asked), then asks for a typed confirmation that states whether the purge affects "DB only" or "DB AND S3". There is no flag to skip it. It hard-deletes the database row first (with its comments), commits, and only then deletes the S3 object and its cached thumbnails, and only if no other file references the same content. This order is deliberate: a failed S3 delete leaves at worst a harmless orphan that `check_s3_integrity.py` finds, the reverse order could lose data other files still share. |
| `purge-duplicates <dir_uuid>` | **Destructive to the database, S3-safe by construction.** Batch-purges only the `duplicate` files directly in that directory (not recursive) after one confirmation. Each file is re-checked right before its own purge; if its active sibling was deleted meanwhile, the file is skipped and reported. `shared` and `sole` files are only counted and never processed. |
| `restore <file_uuid>...` | Reversible. Clears `deleted_at` of one or more soft-deleted files, undoing the soft-delete. Only `SOLE` files qualify (no other file may reference the same content); any other id is skipped with a warning. Runs immediately without a question. |
| `list-duplicates --file <file_uuid> --dir <dir_uuid>` | Read-only. `--file` shows the active files sharing content with a file, `--dir` does the same for every soft-deleted file directly in a directory. Both flags repeat and combine; at least one is required. A file without duplicates is a normal result. |
| `urlpath <uuid>` | Read-only. Looks the id up as a file and as a directory and prints the archive path and the frontend path (`/archive/files/<uuid>` or `/archive/dirs/<uuid>`). Error only if neither exists. |

```bash
# Inside the container
python scripts/maintain_deleted_archive_files.py
python scripts/maintain_deleted_archive_files.py list --short
python scripts/maintain_deleted_archive_files.py list <dir-uuid>
python scripts/maintain_deleted_archive_files.py purge <file-uuid>
python scripts/maintain_deleted_archive_files.py purge-duplicates <dir-uuid>
python scripts/maintain_deleted_archive_files.py restore <file-uuid>
python scripts/maintain_deleted_archive_files.py list-duplicates --file <file-uuid> --dir <dir-uuid>
python scripts/maintain_deleted_archive_files.py urlpath <uuid>

# Via podman exec (-it only where a question is asked)
podman exec vb-api python scripts/maintain_deleted_archive_files.py list --short
podman exec -it vb-api python scripts/maintain_deleted_archive_files.py purge <file-uuid>
podman exec -it vb-api python scripts/maintain_deleted_archive_files.py purge-duplicates <dir-uuid>
podman exec vb-api python scripts/maintain_deleted_archive_files.py restore <file-uuid>
```

An old integer id (`purge 42`) is refused with `invalid UUID value`. Environment: `DATABASE_URL` (PostgreSQL) and the `S3_*` variables.

### Runbook: permanently delete archive files on production

1. **Soft-delete in the web UI first.** Delete the file in the archive of `vb-intern`. This is the reversible step; only soft-deleted files can be purged.
2. **Log in to the host:** `ssh service@intern.vindobona2.at`.
3. **Look at what is there** (read-only):
   ```bash
   # [host] a short overview per directory
   podman exec vb-api python scripts/maintain_deleted_archive_files.py list --short
   # [host] the files of one directory (copy the dir uuid from the overview)
   podman exec vb-api python scripts/maintain_deleted_archive_files.py list <dir-uuid>
   ```
   The file id also appears in the URL of the file in the browser, `/archive/files/<uuid>`; `urlpath <uuid>` turns an id back into its archive path.
4. **Check the S3 impact** in the `IMPACT` column. `SOLE` means the S3 object disappears with the purge.
5. **Purge one file.** Take a backup first (`podman exec vb-api python scripts/backup_db.py`), because the database row cannot be brought back otherwise. `-it` is required for the confirmation:
   ```bash
   # [host]
   podman exec -it vb-api python scripts/maintain_deleted_archive_files.py purge <file-uuid>
   ```
   Read the question: it names whether the purge affects "DB only" or "DB AND S3". Type `yes` only if that is what you want. For several duplicates in one directory use `purge-duplicates <dir-uuid>` instead.
6. **Check afterwards:** run `list <dir-uuid>` again; the file must be gone.
7. **If you purged the wrong file** from the S3 side, the bucket is versioned: an earlier version of the object can be recovered in the AWS console, but the database row is gone for good.

Files that are not soft-deleted cannot be purged by this script. To permanently delete a **member's own unfiled upload**, the member uses the trash button on the upload page.

---

## `trigger_chronicles.py`

Triggers the chronicle mail (the weekly anniversaries digest) by hand for any reference date, for end-to-end validation on test or development before trusting the real Tuesday job. Safe by default: without `--send` only a dry-run summary (recipient count and entries per organization, status and field) is printed and no SMTP connection is made.

```bash
# Via podman exec
podman exec vb-api python scripts/trigger_chronicles.py --date 2026-03-31
podman exec vb-api python scripts/trigger_chronicles.py --date 2026-03-31 --send --to test@example.org
podman exec -it vb-api python scripts/trigger_chronicles.py --date 2026-03-31 --send --all-members
```

Parameters:

- `--date`: the reference date (`YYYY-MM-DD`) the anniversary week is computed from; default is today in the configured timezone.
- `--send`: actually deliver by SMTP. Needs `--to` or `--all-members`.
- `--to ADDRESS`: send to this one test address only (recommended on shared test and development stages).
- `--all-members`: with `--send`: send to **every opted-in member**. These are real recipients: the script asks for a typed `yes` that names the stage and the recipient count and needs a terminal (`podman exec -it`), otherwise it aborts.

Environment: `DATABASE_URL` (PostgreSQL), `APP_ENVIRONMENT` (shown in the confirmation) and the `SMTP_*` variables when `--send` is used.

---

## Developer helpers

| Script | Purpose |
|---|---|
| `run_pytest.sh` | Runs the test suite against the test database (derived from `DATABASE_URL`, database `vb_test`), then the coverage gate. Used by the pre-commit hook. `podman exec vb-api sh scripts/run_pytest.sh -q` |
| `check_coverage_gate.py` | Reads `coverage.json` of the last run and holds statement, branch and function coverage to separate minimums from `pyproject.toml` (`[tool.coverage_gate]`), so a drop in one figure cannot hide behind another. Exit 1: below a minimum, exit 2: report unusable. |
| `check_router_soc.py` | Fails when a router or dependency under `app/api/` contains business logic: direct session calls (`db.query()`, `db.add()`, `db.commit()`, ...) or imports of ORM models. Everything of that kind belongs in `app/services/`. |
