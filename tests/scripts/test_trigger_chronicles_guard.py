"""Pure control-flow tests (no DB): the sending guard of trigger_chronicles."""

from unittest.mock import MagicMock, patch

import pytest

import scripts.trigger_chronicles as tc


def _run(
    argv: list[str], answer: str | BaseException | None = None
) -> tuple[MagicMock, object]:
    db = MagicMock()
    with (
        patch.object(tc, "SessionLocal", return_value=db),
        patch.object(
            tc,
            "compute_anniversaries",
            return_value={"vbw": {"lebend": {"geburtsdatum": [1]}}},
        ),
        patch.object(tc, "get_opted_in_recipients", return_value=["a@b.at", "c@d.at"]),
        patch.object(tc, "render_template", return_value="<p/>"),
        patch.object(tc, "send_to_recipients") as send,
        patch(
            "builtins.input",
            side_effect=answer if isinstance(answer, BaseException) else [answer],
        ),
        patch("sys.argv", ["t", *argv]),
    ):
        try:
            tc.main()
        except SystemExit as exc:
            return send, exc.code
    return send, None


def test_send_without_target_is_rejected() -> None:
    send, code = _run(["--date", "2026-07-14", "--send"])
    assert code == 2
    send.assert_not_called()


def test_to_and_all_members_exclude_each_other() -> None:
    send, code = _run(["--send", "--to", "x@y.at", "--all-members"])
    assert code == 2
    send.assert_not_called()


@pytest.mark.parametrize("bad", ["x", "a@b", "a@b.at\nBcc: z@z.at", "a b@c.at"])
def test_to_must_be_a_plain_address(bad: str) -> None:
    send, code = _run(["--send", "--to", bad])
    assert code == 2
    send.assert_not_called()


def test_all_members_sends_only_after_yes() -> None:
    send, code = _run(["--date", "2026-07-14", "--send", "--all-members"], answer="yes")
    assert code is None
    assert send.call_args.kwargs["bcc_emails"] == ["a@b.at", "c@d.at"]


@pytest.mark.parametrize("answer", ["no", "", EOFError()])
def test_all_members_without_yes_sends_nothing(answer: str | BaseException) -> None:
    send, code = _run(
        ["--date", "2026-07-14", "--send", "--all-members"], answer=answer
    )
    assert code == 1
    send.assert_not_called()


def test_to_sends_without_confirmation() -> None:
    send, code = _run(
        ["--date", "2026-07-14", "--send", "--to", "t@v.at"],
        answer=AssertionError("no prompt"),
    )
    assert code is None
    assert send.call_args.kwargs["bcc_emails"] == ["t@v.at"]
