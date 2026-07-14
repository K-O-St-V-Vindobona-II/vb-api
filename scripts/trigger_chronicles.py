#!/usr/bin/env python3
"""CLI wrapper to manually trigger a chronicle-mail ("Verbindungschroniken")
run for an arbitrary reference date, for end-to-end validation in test/dev
before trusting the real Tuesday-17:00-Vienna cron job
(job_standesdb_chronicles in app/core/scheduler.py).

Safe by default: without --send, only a dry-run summary is printed — no
SMTP connection is made.

Usage:
    python scripts/trigger_chronicles.py --date 2026-03-31
    python scripts/trigger_chronicles.py --date 2026-03-31 --send \\
        --to test@vindobona2.at
"""

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.db.base  # noqa: F401 — registers all models  # pyright: ignore[reportUnusedImport]
from app.core.mailer import render_template, send_to_recipients
from app.db.database import SessionLocal
from app.services.anniversary_service import (
    AnniversaryResult,
    compute_anniversaries,
    format_date_de,
    get_opted_in_recipients,
    week_window,
)


def print_summary(anniversaries: AnniversaryResult, recipients: list[str]) -> None:
    print(f"Recipients: {len(recipients)}")
    for org, statuses in anniversaries.items():
        for status, fields in statuses.items():
            for field, entries in fields.items():
                print(f"  {org}/{status}/{field}: {len(entries)}")


def main() -> None:
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
    parser.add_argument(
        "--to",
        help="Send only to this single test address instead of all opted-in "
        "members (recommended in shared test/dev environments).",
    )
    args = parser.parse_args()

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
        if not args.to:
            print(
                "WARNING: no --to given, sending to ALL real opted-in members.",
                file=sys.stderr,
            )

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
