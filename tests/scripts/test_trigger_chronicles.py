"""Regression tests for scripts/trigger_chronicles.py.

The anniversary-computation logic itself is covered by
tests/test_anniversary_service.py — these tests only exercise the CLI
wrapper's own control flow (dry-run vs --send, --to override, empty-result
short-circuits).
"""

from datetime import date
from unittest.mock import patch

import scripts.trigger_chronicles as trigger_chronicles
from app.models.member import Member
from app.models.org import Org
from app.models.state import State
from tests.scripts._subprocess_helpers import (
    assert_module_imports_and_configures_mappers,
)


def _seed_base(db) -> None:
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="fu", label="Fux", order=1),
        ]
    )
    db.commit()


def test_standalone_import_configures_mappers_without_error() -> None:
    assert_module_imports_and_configures_mappers("scripts.trigger_chronicles")


def test_dry_run_prints_summary_without_sending(capsys, db_session) -> None:
    _seed_base(db_session)
    target = date(2026, 7, 22)
    db_session.add(
        Member(
            email="chronik@vbw.at",
            vorname="Test",
            nachname="User",
            couleurname="Testikus",
            org_id="vbw",
            state_id="fu",
            geburtsdatum=date(1990, target.month, target.day),
            geburtsdatum_accuracy=3,
            entlassen=False,
            verstorben=False,
            chroniclemail=True,
        )
    )
    db_session.commit()

    with (
        patch.object(trigger_chronicles, "SessionLocal", return_value=db_session),
        patch.object(db_session, "close"),
        patch.object(trigger_chronicles, "send_to_recipients") as mock_send,
        patch("sys.argv", ["trigger_chronicles.py", "--date", "2026-07-14"]),
    ):
        trigger_chronicles.main()

    mock_send.assert_not_called()
    out = capsys.readouterr().out
    assert "Recipients: 1" in out
    assert "vbw/lebend/geburtsdatum: 1" in out
    assert "Dry run only" in out


def test_send_flag_with_to_calls_send_to_recipients(capsys, db_session) -> None:
    _seed_base(db_session)
    target = date(2026, 7, 22)
    db_session.add(
        Member(
            email="whoever@vbw.at",
            vorname="Test",
            nachname="User",
            couleurname="Testikus",
            org_id="vbw",
            state_id="fu",
            geburtsdatum=date(1990, target.month, target.day),
            geburtsdatum_accuracy=3,
            entlassen=False,
            verstorben=False,
        )
    )
    db_session.commit()

    with (
        patch.object(trigger_chronicles, "SessionLocal", return_value=db_session),
        patch.object(db_session, "close"),
        patch.object(trigger_chronicles, "send_to_recipients") as mock_send,
        patch(
            "sys.argv",
            [
                "trigger_chronicles.py",
                "--date",
                "2026-07-14",
                "--send",
                "--to",
                "test@vindobona2.at",
            ],
        ),
    ):
        trigger_chronicles.main()

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to_emails"] == []
    assert kwargs["bcc_emails"] == ["test@vindobona2.at"]
    out = capsys.readouterr().out
    assert "Sent to 1 recipient(s)." in out


def test_no_recipients_skips_send(capsys, db_session) -> None:
    _seed_base(db_session)

    with (
        patch.object(trigger_chronicles, "SessionLocal", return_value=db_session),
        patch.object(db_session, "close"),
        patch.object(trigger_chronicles, "send_to_recipients") as mock_send,
        patch(
            "sys.argv",
            ["trigger_chronicles.py", "--date", "2026-07-14", "--send"],
        ),
    ):
        trigger_chronicles.main()

    mock_send.assert_not_called()
    err = capsys.readouterr().err
    assert "No recipients" in err


def test_no_anniversaries_skips_send(capsys, db_session) -> None:
    _seed_base(db_session)

    with (
        patch.object(trigger_chronicles, "SessionLocal", return_value=db_session),
        patch.object(db_session, "close"),
        patch.object(trigger_chronicles, "send_to_recipients") as mock_send,
        patch(
            "sys.argv",
            [
                "trigger_chronicles.py",
                "--date",
                "2026-07-14",
                "--send",
                "--to",
                "test@vindobona2.at",
            ],
        ),
    ):
        trigger_chronicles.main()

    mock_send.assert_not_called()
    out = capsys.readouterr().out
    assert "No anniversaries in this window" in out
