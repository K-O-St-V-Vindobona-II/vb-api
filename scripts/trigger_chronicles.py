#!/usr/bin/env python3
"""CLI wrapper to manually trigger a chronicle-mail ("Verbindungschroniken")
run for an arbitrary reference date, for end-to-end validation in test/dev
before trusting the real Tuesday-17:00-Vienna cron job
(job_standesdb_chronicles in app/core/scheduler.py).

Safe by default: without --send, only a dry-run summary is printed - no
SMTP connection is made. With --send, the mail goes either to a single test
address (--to) or, only with --all-members and after typing "yes" at a
confirmation that names the stage and the recipient count, to every opted-in
member.

Usage:
    python scripts/trigger_chronicles.py --date 2026-03-31
    python scripts/trigger_chronicles.py --date 2026-03-31 --send \\
        --to test@vindobona2.at
    python scripts/trigger_chronicles.py --date 2026-03-31 --send --all-members
"""

import argparse
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.db.base  # noqa: F401 — registers all models  # pyright: ignore[reportUnusedImport]
from app.core.config import get_settings
from app.core.mailer import render_template, send_to_recipients
from app.db.database import SessionLocal
from app.services.anniversary_service import (
    AnniversaryResult,
    compute_anniversaries,
    format_date_de,
    get_opted_in_recipients,
    week_window,
)

_ADDRESS_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _email_address(value: str) -> str:
    if not _ADDRESS_PATTERN.fullmatch(value):
        msg = f"not a plain e-mail address: {value!r}"
        raise argparse.ArgumentTypeError(msg)
    return value


def print_summary(anniversaries: AnniversaryResult, recipients: list[str]) -> None:
    print(f"Recipients: {len(recipients)}")
    for org, statuses in anniversaries.items():
        for status, fields in statuses.items():
            for field, entries in fields.items():
                print(f"  {org}/{status}/{field}: {len(entries)}")


def _confirm_mass_mail(recipient_count: int, week_start: date, week_end: date) -> bool:
    question = (
        f'Type "yes" to send the chronicle mail for {week_start} .. {week_end} '
        f"to {recipient_count} real member(s) on stage "
        f"{get_settings().app_environment!r}: "
    )
    try:
        answer = input(question)
    except EOFError:
        print(
            "ERROR: no interactive terminal attached to confirm "
            "(rerun with 'podman exec -it ...').",
            file=sys.stderr,
        )
        return False
    return answer.strip().lower() == "yes"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=datetime.now(UTC).date(),
        help="Reference date (YYYY-MM-DD) to compute the anniversary week from.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually send via SMTP. Without this, only a dry-run summary is printed.",
    )
    recipients = parser.add_mutually_exclusive_group()
    recipients.add_argument(
        "--to",
        type=_email_address,
        help="Send only to this single test address (recommended in shared "
        "test/dev environments).",
    )
    recipients.add_argument(
        "--all-members",
        action="store_true",
        help="With --send: send to every opted-in member, after a confirmation.",
    )
    args = parser.parse_args()
    if args.send and not (args.to or args.all_members):
        parser.error("--send requires either --to ADDRESS or --all-members")
    return args


def main() -> None:
    args = _parse_args()

    db = SessionLocal()
    try:
        anniversaries = compute_anniversaries(db, args.date)
        week_start, week_end = week_window(args.date)
        recipients = [args.to] if args.to else get_opted_in_recipients(db)

        print(f"Reference date: {args.date}")
        print(f"Anniversary week: {week_start} .. {week_end}")
        print_summary(anniversaries, recipients)

        if not args.send:
            print("Dry run only — pass --send to actually deliver the email.")
            return
        if not recipients:
            print("No recipients — nothing sent.", file=sys.stderr)
            return
        if not anniversaries:
            print("No anniversaries in this window — nothing sent.")
            return
        if args.all_members and not _confirm_mass_mail(
            len(recipients), week_start, week_end
        ):
            print("Aborted.")
            sys.exit(1)

        html = render_template(
            "chronicles.html",
            anniversaries=anniversaries,
            start=format_date_de(week_start),
            end=format_date_de(week_end),
        )
        send_to_recipients(
            to_emails=[],
            bcc_emails=recipients,
            subject="Verbindungschroniken",
            html_content=html,
            template_key="chronicles",
        )
        print(f"Sent to {len(recipients)} recipient(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
