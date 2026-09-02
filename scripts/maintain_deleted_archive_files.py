#!/usr/bin/env python3
"""Inspect, restore, or permanently hard-delete soft-deleted archive files.

Hard-delete is the ONLY way to permanently remove an archive file's data —
the regular API only ever soft-deletes (see app/services/archive_service.py).
Deliberately CLI-only: none of this must ever be exposed via the API/frontend
(see app/services/archive_maintenance_service.py's module docstring).

Without a subcommand, prints a short usage text — nothing is read from the
database and nothing is changed.

`list` (optionally `list <dir_id>` to only show one directory) is read-only.
It groups every currently soft-deleted archive file by directory — one
paragraph per directory (path + its dir_id, the same id `purge-duplicates`
and `list-duplicates --dir` expect), each followed by its files (id, content
hash, S3 impact, filename, size, description, deletion time). Long file
names/descriptions are truncated to keep the table columns aligned.
`list --short` collapses each directory's paragraph down to a single "path
(dir_id: N): <file count>" line — a quick triage overview when hundreds of
files are scattered across many directories.

`archive_store_item_id` is not unique on `ArchiveFile` — several files
(active and/or soft-deleted) can reference the same underlying S3 object
(content dedup inherited from the legacy migration). Every listing therefore
also classifies each file's S3 impact:
  - duplicate: an active file still references the same content — purging
    this file never touches S3.
  - shared: no active file, but another soft-deleted file still references
    the same content — S3 stays untouched for now, but the last remaining
    reference in the group will remove it.
  - sole (SOLE in the listing): no other reference at all — purging this
    file deletes the underlying S3 object immediately.

`purge <file_id>` is DESTRUCTIVE and S3-relevant. The given file id (a UUID)
must currently be soft-deleted (i.e. actually purgeable — appear in `list`) —
anything else is
rejected before any prompt is shown, and nothing is deleted. Only after that
check passes does the script ask for interactive confirmation; there is no
flag to skip it. The confirmation prompt itself states whether this
particular purge affects "DB only" or "DB AND S3" — the underlying S3
object (and any cached thumbnails) is only removed once no other file
version anywhere still references the same content hash — see
app/services/archive_maintenance_service.py::purge_file() for the full
reference-counting logic.

`purge-duplicates <dir_id>` is DESTRUCTIVE to the database (hard-deletes
file rows, irreversibly) but S3-SAFE BY CONSTRUCTION: it only ever
batch-purges the "duplicate" files directly in that directory (direct
children only, not recursive) after one confirmation for the whole batch —
files where an active copy guarantees the S3 object survives. Each file is
freshly re-checked immediately before its own purge, so a concurrent
soft-delete of its active sibling during the batch can never turn into an
unexpected S3 deletion — it is skipped and reported instead.
Any remaining "shared"/"sole" files in that directory are only *reported*
(count + pointer to `list-duplicates`/`purge <id>`) — they are never
processed automatically; use `purge <id>` for those individually.

`restore <file_id> [<file_id> ...]` is the reverse of a purge: it clears
deleted_at, undoing a soft-delete. Non-destructive and reversible (the file
can simply be soft-deleted again via the GUI), so — unlike
`purge`/`purge-duplicates` — it runs immediately without a confirmation
prompt. Only SOLE files (see the IMPACT column in `list` — no other active
or soft-deleted file references the same content) may be restored this way;
anything else is rejected. Takes any number of ids and works through them
one after another: an id that can't be restored (not found, not
soft-deleted, or not SOLE) only prints a warning and does not stop the rest.

`list-duplicates` is read-only. `--file FILE_ID` shows the active files that
share content with the given file (any status — active or soft-deleted);
`--dir DIR_ID` does the same for every currently soft-deleted file directly
in that directory. Both are repeatable and combinable in one call, e.g.
`list-duplicates --file <uuid1> --file <uuid2> --dir 10 --dir 11`. Each
file gets its own two-part block — "DELETED FILE:"/"ACTIVE FILE:"
(whichever the file's actual current state is) and "DUPLICATES:" (its
active duplicates, or "(none)"),
both using the same ID/PATH/FILENAME table — separated by a "====="
delimiter when more than one block is printed. A file with no active
duplicates is a normal result, not an error — an error is only reported for
a `--file` id that doesn't exist at all, and only skips that one entry.

`urlpath <id>` is a read-only convenience lookup: it looks the given id up
as BOTH a file id (UUID) and a directory id (integer) and, for whichever
match(es), prints its archive path and the frontend's relative URL path
("/archive/files/<id>" or "/archive/dirs/<id>") — just the path, not a full
URL, since the frontend domain differs per environment and isn't reliably
known to this script.
Error only if neither a file nor a directory with that id exists.

Runs in every environment, including production — unlike downsync_prod.py,
this is not dev-only tooling but the actual production maintenance
mechanism for accumulated soft-deleted files.

Usage (file ids are UUIDs, directory ids are still plain integers):
    python scripts/maintain_deleted_archive_files.py
    python scripts/maintain_deleted_archive_files.py list
    python scripts/maintain_deleted_archive_files.py list --short
    python scripts/maintain_deleted_archive_files.py list 7
    python scripts/maintain_deleted_archive_files.py purge <file-uuid>
    python scripts/maintain_deleted_archive_files.py purge-duplicates 7
    python scripts/maintain_deleted_archive_files.py restore <file-uuid>
    python scripts/maintain_deleted_archive_files.py restore <file-uuid-1> <file-uuid-2>
    python scripts/maintain_deleted_archive_files.py urlpath <file-uuid>
    python scripts/maintain_deleted_archive_files.py urlpath 7
    python scripts/maintain_deleted_archive_files.py list-duplicates \
        --file <file-uuid> --dir 7
"""

import argparse
import contextlib
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import app.db.base  # noqa: F401 — registers all models  # pyright: ignore[reportUnusedImport]
from app.core.storage import StorageClient, get_storage
from app.db.database import SessionLocal
from app.services.archive_maintenance_service import (
    ActiveDuplicate,
    ArchiveMaintenanceError,
    PurgeCandidate,
    PurgeImpact,
    active_duplicates_in_dir,
    active_duplicates_of_file,
    find_dir_location,
    find_file_location,
    is_still_duplicate,
    list_deleted_files,
    list_deleted_files_in_dir,
    purge_file,
    restore_file,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_IMPACT_LABELS = {
    PurgeImpact.DUPLICATE: "duplicate",
    PurgeImpact.SHARED: "shared",
    PurgeImpact.SOLE: "SOLE",
}

_FILENAME_WIDTH = 24
_FILEEXT_WIDTH = 8
_DESCRIPTION_WIDTH = 24
_DUPLICATE_PATH_WIDTH = 40
_FILE_ID_WIDTH = 36  # exact length of a UUID's string form


def _human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def _truncate(text: str, width: int) -> str:
    """Ellipsis-truncates `text` to `width` characters so a fixed-width
    console column can never push the columns after it out of alignment."""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def _print_impact_summary(candidates: list[PurgeCandidate]) -> None:
    counts = Counter(c.impact for c in candidates)
    print(
        f"  {counts[PurgeImpact.DUPLICATE]} duplicate (active copy exists — "
        f"S3 safe), {counts[PurgeImpact.SHARED]} shared (other deleted "
        f"copies exist — S3 safe for now), {counts[PurgeImpact.SOLE]} sole "
        "reference (purge deletes the S3 object)."
    )


def _group_by_directory(
    candidates: list[PurgeCandidate],
) -> list[tuple[str, int | None, list[PurgeCandidate]]]:
    """Groups candidates by archive_dir_id, preserving each group's existing
    (deleted_at-ascending) relative order, and returns the groups sorted
    alphabetically by path. `path` is derived purely from `archive_dir_id`,
    so every candidate in a group shares the same path.
    """
    groups: dict[int | None, list[PurgeCandidate]] = {}
    for c in candidates:
        groups.setdefault(c.archive_dir_id, []).append(c)
    return sorted(
        ((files[0].path, dir_id, files) for dir_id, files in groups.items()),
        key=lambda group: group[0].casefold(),
    )


def _print_candidates(candidates: list[PurgeCandidate], *, short: bool = False) -> None:
    if not candidates:
        print("No soft-deleted archive files found.")
        return

    total_size = 0
    for path, dir_id, files in _group_by_directory(candidates):
        dir_label = dir_id if dir_id is not None else "-"
        total_size += sum(c.size for c in files)

        if short:
            print(f"{path} (dir_id: {dir_label}): {len(files)}")
            continue

        print(f"\n{path} (dir_id: {dir_label})")
        print(
            f"   {'ID':<{_FILE_ID_WIDTH}} {'HASH':<16}  {'IMPACT':<10} "
            f"{'FILENAME':<{_FILENAME_WIDTH}} {'FILEEXT':<{_FILEEXT_WIDTH}} "
            f"{'SIZE':>9}  {'DESCRIPTION':<{_DESCRIPTION_WIDTH}} DELETED_AT"
        )
        for c in files:
            hash_short = (c.sha256_hash or "")[:16]
            deleted_at = c.deleted_at.strftime("%Y-%m-%d %H:%M:%S")
            name = _truncate(c.name, _FILENAME_WIDTH)
            extension = _truncate(c.extension, _FILEEXT_WIDTH)
            description = _truncate(c.description or "", _DESCRIPTION_WIDTH)
            print(
                f"   {c.file_id!s:<{_FILE_ID_WIDTH}} {hash_short:<16}  "
                f"{_IMPACT_LABELS[c.impact]:<10} "
                f"{name:<{_FILENAME_WIDTH}} {extension:<{_FILEEXT_WIDTH}} "
                f"{_human_size(c.size):>9}  {description:<{_DESCRIPTION_WIDTH}} "
                f"{deleted_at}"
            )

    print(
        f"\n{len(candidates)} soft-deleted file(s). "
        f"Total size: {_human_size(total_size)}."
    )
    if len(candidates) > 1:
        _print_impact_summary(candidates)


def _print_impact_note(candidate: PurgeCandidate) -> None:
    if candidate.impact is PurgeImpact.DUPLICATE:
        print(
            f"Note: {candidate.active_sibling_count} active file(s) still "
            "reference this content — the S3 object will NOT be deleted."
        )
    elif candidate.impact is PurgeImpact.SHARED:
        print(
            f"Note: {candidate.other_deleted_sibling_count} other "
            "soft-deleted file(s) still reference this content — the S3 "
            "object will NOT be deleted yet; it is removed once the last "
            "referencing file is purged."
        )
    else:
        print(
            "WARNING: this is the ONLY reference to this content — purging "
            "PERMANENTLY DELETES the underlying S3 object and its cached "
            "thumbnails."
        )


def _ask_yes(prompt: str) -> bool:
    try:
        answer = input(prompt)
    except EOFError:
        print(
            "ERROR: no interactive terminal attached to confirm "
            "(rerun with 'podman exec -it ...')."
        )
        sys.exit(1)
    return answer.strip().lower() == "yes"


def _confirm(candidate: PurgeCandidate) -> None:
    _print_impact_note(candidate)
    scope = (
        "DB AND S3"
        if candidate.impact is PurgeImpact.SOLE
        else "DB only (S3 object stays)"
    )
    prompt = (
        f'Type "yes" to permanently delete file {candidate.file_id} '
        f'("{candidate.filename}" in "{candidate.path}") from {scope}: '
    )
    if not _ask_yes(prompt):
        print("Aborted.")
        sys.exit(0)


def _purge_one(
    db: Session,
    storage: StorageClient,
    candidate: PurgeCandidate,
    *,
    expect_no_s3_impact: bool = False,
) -> bool:
    """Purges the given candidate. Returns True if an error occurred."""
    try:
        result = purge_file(db, storage, candidate.file_id)
    except ArchiveMaintenanceError as exc:
        print(f"ERROR: file {candidate.file_id}: {exc}", file=sys.stderr)
        return True

    print(
        f"Purged file {candidate.file_id} "
        f'("{candidate.filename}" in "{candidate.path}").'
    )
    had_error = False
    if expect_no_s3_impact and result.store_item_deleted:
        print(
            f"  WARNING: file {candidate.file_id}'s S3 object was deleted "
            "unexpectedly — its active sibling must have been removed "
            "between the live re-check and this purge.",
            file=sys.stderr,
        )
        had_error = True
    for err in result.s3_errors:
        print(f"  WARNING: S3 cleanup failed: {err}", file=sys.stderr)
        had_error = True
    return had_error


def _purge_duplicates_batch(
    db: Session, storage: StorageClient, duplicates: list[PurgeCandidate]
) -> tuple[int, int, bool]:
    """Purges every candidate in `duplicates` after a single batch
    confirmation. Each one is freshly re-checked immediately before its own
    purge — a concurrent soft-delete of its active sibling between listing
    and this batch running must not silently turn a "no S3 risk" item into
    an actual S3 deletion; it is skipped and reported instead.

    Returns (purged_count, skipped_count, had_error).
    """
    total_size = sum(c.size for c in duplicates)
    prompt = (
        f'Type "yes" to permanently delete these {len(duplicates)} '
        f"duplicate file(s) ({_human_size(total_size)}) from DB — S3 "
        "objects are not affected: "
    )
    if not _ask_yes(prompt):
        print("Duplicate batch skipped.")
        return 0, 0, False

    purged = 0
    skipped = 0
    had_error = False
    for candidate in duplicates:
        if not is_still_duplicate(db, candidate):
            print(
                f"WARNING: file {candidate.file_id} skipped — no longer a "
                "safe duplicate (its active sibling is gone); review it "
                "individually via 'purge <id>'.",
                file=sys.stderr,
            )
            skipped += 1
            continue
        if _purge_one(db, storage, candidate, expect_no_s3_impact=True):
            had_error = True
        else:
            purged += 1
    return purged, skipped, had_error


def _run_purge_duplicates(db: Session, dir_id: int) -> NoReturn:
    candidates = list_deleted_files_in_dir(db, dir_id)
    if not candidates:
        print("No soft-deleted files in this directory.")
        sys.exit(0)

    _print_candidates(candidates)

    duplicates = [c for c in candidates if c.impact is PurgeImpact.DUPLICATE]
    remainder_count = len(candidates) - len(duplicates)

    if not duplicates:
        print("\nNo safely-duplicated files in this directory — nothing to purge.")
        if remainder_count:
            print(
                f"{remainder_count} file(s) need individual review — see "
                "'list-duplicates' and 'purge <id>'."
            )
        sys.exit(0)

    storage = get_storage()
    purged, skipped, had_error = _purge_duplicates_batch(db, storage, duplicates)

    print(f"\n{purged} file(s) purged, {skipped} skipped.")
    if remainder_count:
        print(
            f"{remainder_count} remaining file(s) in this directory need "
            "individual review — see 'list-duplicates' and 'purge <id>'."
        )
    sys.exit(1 if had_error else 0)


def _run_restore(db: Session, file_ids: list[uuid.UUID]) -> NoReturn:
    """Restores every given file id one after another. An id that can't be
    restored (not found, not soft-deleted, or not SOLE) only prints a
    warning and does not stop the remaining ids — see restore_file() for
    the SOLE requirement.
    """
    restored_count = 0
    failed_count = 0
    for file_id in file_ids:
        try:
            location = restore_file(db, file_id)
        except ArchiveMaintenanceError as exc:
            print(f"WARNING: file {file_id} not restored: {exc}", file=sys.stderr)
            failed_count += 1
            continue
        print(f'Restored file {file_id} ("{location.filename}" in "{location.path}").')
        restored_count += 1

    if len(file_ids) > 1:
        print(f"\n{restored_count} file(s) restored, {failed_count} failed.")
    sys.exit(1 if failed_count else 0)


def _print_file_table_header() -> None:
    print(f"   {'ID':<{_FILE_ID_WIDTH}} {'PATH':<{_DUPLICATE_PATH_WIDTH}} FILENAME")


def _print_file_row(file_id: uuid.UUID, path: str, filename: str) -> None:
    path = _truncate(path, _DUPLICATE_PATH_WIDTH)
    print(
        f"   {file_id!s:<{_FILE_ID_WIDTH}} {path:<{_DUPLICATE_PATH_WIDTH}} {filename}"
    )


def _print_duplicates_block(
    file_id: uuid.UUID,
    path: str,
    filename: str,
    *,
    deleted: bool,
    duplicates: list[ActiveDuplicate],
) -> None:
    print("DELETED FILE:" if deleted else "ACTIVE FILE:")
    _print_file_table_header()
    _print_file_row(file_id, path, filename)
    print()
    print("DUPLICATES:")
    if not duplicates:
        print("   (none)")
        return
    _print_file_table_header()
    for d in duplicates:
        _print_file_row(d.file_id, d.path, d.filename)


def _print_block_separator(block_index: int) -> None:
    """Prints the delimiter before every block except the first one, so
    multiple --file/--dir results in one run are visually separated without
    a stray delimiter before the first or after the last block."""
    if block_index > 0:
        print("\n=====\n")


def _run_list_duplicates(
    db: Session, file_ids: list[uuid.UUID], dir_ids: list[int]
) -> NoReturn:
    if not file_ids and not dir_ids:
        print("ERROR: provide at least one --file or --dir.", file=sys.stderr)
        sys.exit(2)

    had_error = False
    block_index = 0

    for file_id in file_ids:
        try:
            location, duplicates = active_duplicates_of_file(db, file_id)
        except ArchiveMaintenanceError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            had_error = True
            continue
        _print_block_separator(block_index)
        _print_duplicates_block(
            location.file_id,
            location.path,
            location.filename,
            deleted=location.deleted,
            duplicates=duplicates,
        )
        block_index += 1

    for dir_id in dir_ids:
        pairs = active_duplicates_in_dir(db, dir_id)
        if not pairs:
            _print_block_separator(block_index)
            print(f"Directory {dir_id}: no soft-deleted files.")
            block_index += 1
            continue
        for candidate, duplicates in pairs:
            _print_block_separator(block_index)
            _print_duplicates_block(
                candidate.file_id,
                candidate.path,
                candidate.filename,
                # active_duplicates_in_dir() only ever returns soft-deleted files
                deleted=True,
                duplicates=duplicates,
            )
            block_index += 1

    sys.exit(1 if had_error else 0)


def _run_urlpath(db: Session, id_: str) -> NoReturn:
    """Looks `id_` up as both a file id (UUID) and a directory id (plain
    integer) - the two id spaces don't overlap in shape any more since
    archive_files' own Final-Cutover, so at most one of the two parses
    below ever succeeds, but both are tried independently rather than
    guessing which one the caller meant."""
    file_location = None
    with contextlib.suppress(ValueError):
        file_location = find_file_location(db, uuid.UUID(id_))

    dir_location = None
    with contextlib.suppress(ValueError):
        dir_location = find_dir_location(db, int(id_))

    if file_location is None and dir_location is None:
        print(f"ERROR: no file or directory with id {id_}.", file=sys.stderr)
        sys.exit(1)

    block_index = 0

    if file_location is not None:
        _print_block_separator(block_index)
        print("DELETED FILE:" if file_location.deleted else "ACTIVE FILE:")
        _print_file_table_header()
        _print_file_row(
            file_location.file_id, file_location.path, file_location.filename
        )
        print(f"\nURL PATH: /archive/files/{id_}")
        block_index += 1

    if dir_location is not None:
        _print_block_separator(block_index)
        print("DELETED DIRECTORY:" if dir_location.deleted else "ACTIVE DIRECTORY:")
        print(f"   {'ID':<6} PATH")
        print(f"   {dir_location.dir_id:<6} {dir_location.path}")
        print(f"\nURL PATH: /archive/dirs/{id_}")
        block_index += 1

    sys.exit(0)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect, restore, or permanently purge soft-deleted archive "
            "files (DB + S3)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser(
        "list",
        help="Read-only: list soft-deleted archive files, grouped by directory",
    )
    list_parser.add_argument(
        "dir_id",
        type=int,
        nargs="?",
        default=None,
        help="Only list files directly in this directory (optional; omit for all)",
    )
    list_parser.add_argument(
        "--short",
        action="store_true",
        help="Only show one line per directory with its soft-deleted file count",
    )

    purge_parser = subparsers.add_parser(
        "purge",
        help=(
            "DESTRUCTIVE, S3-relevant: permanently delete one currently "
            "soft-deleted file from DB (and from S3 too, unless another "
            "file still references its content) after confirmation"
        ),
    )
    purge_parser.add_argument(
        "id",
        type=uuid.UUID,
        help="ID of the file to purge — must currently be soft-deleted (see 'list')",
    )

    purge_duplicates_parser = subparsers.add_parser(
        "purge-duplicates",
        help=(
            "DESTRUCTIVE to the DB, but S3-SAFE by construction: "
            "batch-purge only the safely duplicated files in one directory "
            "(active copy guarantees S3 survives); shared/sole files "
            "(which WOULD be S3-relevant) are reported but left untouched"
        ),
    )
    purge_duplicates_parser.add_argument(
        "dir_id",
        type=int,
        help="archive_dir_id to clean up (direct children only, not recursive)",
    )

    restore_parser = subparsers.add_parser(
        "restore",
        help=(
            "Reversible, non-destructive: restore one or more soft-deleted "
            "SOLE files (clears deleted_at), runs immediately without "
            "confirmation; ids that can't be restored are skipped with a "
            "warning instead of stopping the rest"
        ),
    )
    restore_parser.add_argument(
        "file_ids",
        type=uuid.UUID,
        nargs="+",
        metavar="FILE_ID",
        help=(
            "One or more ids of soft-deleted files to restore, separated "
            "by spaces. Each must currently be soft-deleted and SOLE — no "
            "other active or deleted file may reference the same content "
            "(see 'list')"
        ),
    )

    list_duplicates_parser = subparsers.add_parser(
        "list-duplicates",
        help=(
            "Read-only: show the active file(s) sharing content with given "
            "file(s) and/or a directory's soft-deleted files"
        ),
    )
    list_duplicates_parser.add_argument(
        "--file",
        dest="file_ids",
        type=uuid.UUID,
        action="append",
        default=[],
        metavar="FILE_ID",
        help=(
            "Show active duplicates of this file id (any status, active or "
            "soft-deleted). Repeatable: --file <uuid1> --file <uuid2> ..."
        ),
    )
    list_duplicates_parser.add_argument(
        "--dir",
        dest="dir_ids",
        type=int,
        action="append",
        default=[],
        metavar="DIR_ID",
        help=(
            "Show active duplicates for every currently soft-deleted file "
            "directly in this directory id. Repeatable: --dir 1 --dir 2 ... "
            "Combinable with --file in the same call, e.g.: "
            "list-duplicates --file <uuid1> --file <uuid2> --dir 10 --dir 11"
        ),
    )

    urlpath_parser = subparsers.add_parser(
        "urlpath",
        help=(
            "Read-only convenience: print the archive path and frontend "
            "URL path for an id — looked up as both a file id and a "
            "directory id, showing whichever exist"
        ),
    )
    urlpath_parser.add_argument(
        "id",
        help="Looked up as both a file id (UUID) and a directory id (integer)",
    )

    return parser


def _run_list(db: Session, dir_id: int | None, *, short: bool) -> NoReturn:
    candidates = (
        list_deleted_files_in_dir(db, dir_id)
        if dir_id is not None
        else list_deleted_files(db)
    )
    _print_candidates(candidates, short=short)
    sys.exit(0)


def _run_purge(db: Session, file_id: uuid.UUID) -> NoReturn:
    candidates = list_deleted_files(db)
    target = next((c for c in candidates if c.file_id == file_id), None)
    if target is None:
        print(
            f"ERROR: file {file_id} is not currently soft-deleted, not purgeable.",
            file=sys.stderr,
        )
        sys.exit(1)

    _print_candidates([target])
    _confirm(target)

    storage = get_storage()
    had_error = _purge_one(db, storage, target)
    sys.exit(1 if had_error else 0)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    db = SessionLocal()
    try:
        if args.command == "list":
            _run_list(db, args.dir_id, short=args.short)
        elif args.command == "purge-duplicates":
            _run_purge_duplicates(db, args.dir_id)
        elif args.command == "restore":
            _run_restore(db, args.file_ids)
        elif args.command == "list-duplicates":
            _run_list_duplicates(db, args.file_ids, args.dir_ids)
        elif args.command == "urlpath":
            _run_urlpath(db, args.id)
        else:
            _run_purge(db, args.id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
