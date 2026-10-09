"""Mail templates: greeting, payment details and capped lists."""

from datetime import date
from unittest.mock import patch

import pytest

from app.core.mailer import render_template
from app.core.scheduler import (
    _greeting_name,
    _payment_account,
    _send_birthday_mails,
    _send_debtor_reminders,
)
from app.models.member import Member
from app.models.org import Org
from app.models.p4x_account import GIROKONTO_ACCOUNT_ID, P4xAccount
from app.models.state import State


class TestBirthdayMail:
    def test_no_question_mark_when_the_age_is_unknown(self):
        html = render_template("birthday.html", name="Kater", age=None)

        assert "?" not in html
        assert "morgigen Geburtstag" in html

    def test_age_is_shown_when_known(self):
        html = render_template("birthday.html", name="Kater", age=40)

        assert "morgigen 40. Geburtstag" in html

    def test_the_greeting_uses_the_name(self):
        html = render_template("birthday.html", name="Kater", age=40)

        assert "Lieber Bundesbruder Kater!" in html

    def test_the_mail_declares_its_charset(self):
        html = render_template("birthday.html", name="Kater", age=40)

        assert '<meta charset="utf-8">' in html


class TestBirthdayJobMail:
    @staticmethod
    def _send(db_session, **member_columns):
        # Not stored: the mail only reads the member's attributes, and a date
        # with the year 0 cannot be stored but must still not print "?".
        member = Member(
            email="geburtstag@vbw.at",
            vorname="Max",
            nachname="Muster",
            org_id="vbw",
            **member_columns,
        )
        with patch("app.core.scheduler.send_to_recipients") as send:
            _send_birthday_mails(db_session, [member], date(2026, 6, 16))
        return send.call_args.kwargs["html_content"]

    def test_the_age_is_the_age_on_the_birthday(self, db_session):
        html = self._send(db_session, couleurname="Kater", geburtsdatum="1986-06-16")

        assert "morgigen 40. Geburtstag" in html

    def test_an_unknown_birth_year_leaves_the_age_out(self, db_session):
        html = self._send(db_session, couleurname="Kater", geburtsdatum="0000-06-16")

        assert "morgigen Geburtstag" in html
        assert "?" not in html
        assert "None" not in html

    def test_a_member_without_a_student_name_is_greeted_by_name(self, db_session):
        html = self._send(db_session, couleurname=None, geburtsdatum="1986-06-16")

        assert "Lieber Bundesbruder Max Muster!" in html
        assert "None" not in html


class TestGreetingName:
    def test_the_name_is_used_when_there_is_no_student_name(self):
        member = Member(vorname="Max", nachname="Muster")

        assert _greeting_name(member) == "Max Muster"

    def test_the_student_name_comes_first(self):
        member = Member(vorname="Max", nachname="Muster", couleurname="Kater")

        assert _greeting_name(member) == "Kater"

    def test_an_empty_student_name_falls_back_to_the_name(self):
        member = Member(vorname="Max", nachname="Muster", couleurname="")

        assert _greeting_name(member) == "Max Muster"


class TestDebtorReminder:
    @staticmethod
    def _debtor(db_session):
        db_session.add_all(
            [Org(id="vbw", label="V", order=1), State(id="up", label="U", order=1)]
        )
        db_session.add(
            Member(
                email="schuldner@vbw.at",
                vorname="Max",
                nachname="S",
                org_id="vbw",
                state_id="up",
                entlassen=False,
                verstorben=False,
            )
        )
        db_session.commit()

    @staticmethod
    def _run(db_session):
        with (
            patch("app.core.scheduler.fee_for_month", return_value=15.0),
            patch(
                "app.core.scheduler.calculate_fee_balance",
                return_value={"end_balance": -400.0},
            ),
            patch("app.core.scheduler.send_to_recipients") as send,
        ):
            run = _send_debtor_reminders(db_session, date(2026, 6, 30), "2026-06-30")
        return run, send

    def test_the_bank_details_come_from_the_account(self, db_session):
        self._debtor(db_session)
        db_session.add(
            P4xAccount(
                id=GIROKONTO_ACCOUNT_ID,
                iban="AT61 1904 3002 3457 3201",
                bic="BKAUATWWXXX",
                label="G",
                init_balance=0,
            )
        )
        db_session.commit()

        run, send = self._run(db_session)

        html = send.call_args.kwargs["html_content"]
        assert run.sent == 1
        assert "IBAN: AT61 1904 3002 3457 3201" in html
        assert "BIC: BKAUATWWXXX" in html
        assert "AT94 2011" not in html
        assert '<meta charset="utf-8">' in html

    def test_the_account_is_read_once_for_all_reminders(self, db_session):
        self._debtor(db_session)
        db_session.add(
            Member(
                email="schuldner2@vbw.at",
                vorname="Berta",
                nachname="S",
                org_id="vbw",
                state_id="up",
                entlassen=False,
                verstorben=False,
            )
        )
        db_session.add(
            P4xAccount(
                id=GIROKONTO_ACCOUNT_ID,
                iban="AT61 1904 3002 3457 3201",
                bic="BKAUATWWXXX",
                label="G",
                init_balance=0,
            )
        )
        db_session.commit()

        with patch(
            "app.core.scheduler._payment_account", wraps=_payment_account
        ) as read:
            run, _send = self._run(db_session)

        assert run.sent == 2
        read.assert_called_once()

    def test_a_missing_bic_leaves_the_line_out(self, db_session):
        self._debtor(db_session)
        db_session.add(
            P4xAccount(
                id=GIROKONTO_ACCOUNT_ID,
                iban="AT61 1904 3002 3457 3201",
                bic=None,
                label="G",
                init_balance=0,
            )
        )
        db_session.commit()

        _run, send = self._run(db_session)

        html = send.call_args.kwargs["html_content"]
        assert "IBAN: AT61 1904 3002 3457 3201" in html
        assert "BIC" not in html

    def test_without_the_account_the_job_fails_instead_of_sending(self, db_session):
        self._debtor(db_session)

        with pytest.raises(RuntimeError, match="account"):
            self._run(db_session)

    def test_without_an_account_a_month_without_debtors_still_works(self, db_session):
        self._debtor(db_session)

        with (
            patch("app.core.scheduler.fee_for_month", return_value=15.0),
            patch(
                "app.core.scheduler.calculate_fee_balance",
                return_value={"end_balance": -10.0},
            ),
            patch("app.core.scheduler.send_to_recipients") as send,
        ):
            run = _send_debtor_reminders(db_session, date(2026, 6, 30), "2026-06-30")

        assert run.sent == 0
        send.assert_not_called()

    def test_the_greeting_falls_back_to_the_name(self, db_session):
        self._debtor(db_session)
        db_session.add(
            P4xAccount(
                id=GIROKONTO_ACCOUNT_ID,
                iban="AT61 1904 3002 3457 3201",
                bic="BKAUATWWXXX",
                label="G",
                init_balance=0,
            )
        )
        db_session.commit()

        _run, send = self._run(db_session)

        assert "Lieber Bundesbruder Max S," in send.call_args.kwargs["html_content"]


class TestArchiveHealthMail:
    def test_a_long_list_is_capped(self):
        keys = [f"archive/store/{i:064d}" for i in range(5000)]

        html = render_template(
            "archive_health_check.html", missing=keys, orphans=[], unsorted_count=0
        )

        assert html.count("<li>") == 100
        assert "und 4900 weitere" in html
        assert "5000 fehlende Datei(en)" in html
        assert len(html) < 20_000

    def test_both_lists_are_capped_on_their_own(self):
        keys = [f"key/{i}" for i in range(250)]

        html = render_template(
            "archive_health_check.html", missing=keys, orphans=keys, unsorted_count=0
        )

        assert html.count("<li>") == 200
        assert html.count("und 150 weitere") == 2

    def test_a_list_of_exactly_the_limit_is_complete(self):
        keys = [f"key/{i}" for i in range(100)]

        html = render_template(
            "archive_health_check.html", missing=keys, orphans=[], unsorted_count=0
        )

        assert html.count("<li>") == 100
        assert "weitere" not in html

    def test_a_short_list_is_complete(self):
        html = render_template(
            "archive_health_check.html",
            missing=["a", "b"],
            orphans=["c"],
            unsorted_count=0,
        )

        assert html.count("<li>") == 3
        assert "weitere" not in html

    def test_the_mail_declares_its_charset(self):
        html = render_template(
            "archive_health_check.html", missing=[], orphans=[], unsorted_count=0
        )

        assert '<meta charset="utf-8">' in html
