"""Filter criteria and partner search match what was typed, not a pattern."""

from datetime import date

import pytest

from app.models.contact import Contact
from app.models.enums import SubjectMode
from app.models.member import Member
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_specialcontact import P4xSpecialcontact
from app.models.state import State
from app.services import p4x_category_service, p4x_partner_service
from app.services.search_utils import escape_like
from tests.p4x.test_p4x_service_queries import _add_tx, _create_account, _now


def _category(db):
    category = P4xCategory(
        name="c1",
        label="c1",
        background_color="#000000",
        text_color="#ffffff",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(category)
    db.commit()
    return category


def _hits(db, account, **criteria) -> list[str]:
    category_filter = P4xCategoryFilter(
        name="f",
        p4x_account_id=account.id,
        p4x_category_id=_category(db).id,
        created_at=_now(),
        updated_at=_now(),
        **criteria,
    )
    db.add(category_filter)
    db.commit()
    p4x_category_service.apply_single_filter(db, category_filter)
    return sorted(
        t.subject for t in p4x_category_service.get_filter_hits(db, category_filter)
    )


SUBJECTS = [
    "A_B",
    "AXB",
    "Beitrag 100% bezahlt",
    "Beitrag 100 Euro",
    "Pfad C:\\temp",
    "Pfad C:Xtemp",
    "_Anfang",
    "XAnfang",
]


@pytest.fixture
def account_with_transactions(db_session):
    account = _create_account(db_session)
    for i, subject in enumerate(SUBJECTS):
        _add_tx(
            db_session,
            account,
            date(2026, 3, 1),
            10.0,
            subject=subject,
            hash_suffix=str(i),
        )
    _add_tx(
        db_session,
        account,
        date(2026, 3, 2),
        10.0,
        iban="AT611904300234573201",
        subject="iban",
        hash_suffix="i",
    )
    return account


class TestSubjectCriteria:
    def test_equals_treats_underscore_as_text(
        self, db_session, account_with_transactions
    ):
        hits = _hits(
            db_session,
            account_with_transactions,
            subject="A_B",
            subject_mode=SubjectMode.EQUALS,
        )

        assert hits == ["A_B"]

    def test_contains_treats_percent_as_text(
        self, db_session, account_with_transactions
    ):
        hits = _hits(
            db_session,
            account_with_transactions,
            subject="100%",
            subject_mode=SubjectMode.CONTAINS,
        )

        assert hits == ["Beitrag 100% bezahlt"]

    def test_a_lone_percent_does_not_match_everything(
        self, db_session, account_with_transactions
    ):
        hits = _hits(
            db_session,
            account_with_transactions,
            subject="%",
            subject_mode=SubjectMode.CONTAINS,
        )

        assert hits == ["Beitrag 100% bezahlt"]

    def test_starts_treats_underscore_as_text(
        self, db_session, account_with_transactions
    ):
        hits = _hits(
            db_session,
            account_with_transactions,
            subject="_An",
            subject_mode=SubjectMode.STARTS,
        )

        assert hits == ["_Anfang"]

    def test_a_backslash_is_text_too(self, db_session, account_with_transactions):
        hits = _hits(
            db_session,
            account_with_transactions,
            subject="C:\\t",
            subject_mode=SubjectMode.CONTAINS,
        )

        assert hits == ["Pfad C:\\temp"]

    @pytest.mark.parametrize(
        ("mode", "subject", "expected"),
        [
            (SubjectMode.EQUALS, "a_b", ["A_B"]),
            (SubjectMode.CONTAINS, "BEITRAG 100%", ["Beitrag 100% bezahlt"]),
            (
                SubjectMode.STARTS,
                "beitrag 100",
                ["Beitrag 100 Euro", "Beitrag 100% bezahlt"],
            ),
        ],
    )
    def test_matching_stays_case_insensitive(
        self, db_session, account_with_transactions, mode, subject, expected
    ):
        hits = _hits(
            db_session, account_with_transactions, subject=subject, subject_mode=mode
        )

        assert hits == expected

    def test_plain_text_still_matches_in_every_mode(
        self, db_session, account_with_transactions
    ):
        equals = _hits(
            db_session,
            account_with_transactions,
            subject="AXB",
            subject_mode=SubjectMode.EQUALS,
        )

        assert equals == ["AXB"]


class TestIbanCriterion:
    @pytest.mark.parametrize(
        "iban",
        [
            "AT611904300234573201",
            "AT61 1904 3002 3457 3201",
            "at611904300234573201",
            "  at61 1904\t3002 3457 3201 ",
        ],
    )
    def test_blanks_and_case_do_not_matter(
        self, db_session, account_with_transactions, iban
    ):
        hits = _hits(
            db_session,
            account_with_transactions,
            iban=iban,
            subject_mode=SubjectMode.CONTAINS,
        )

        assert hits == ["iban"]

    def test_an_empty_iban_is_no_criterion(self, db_session, account_with_transactions):
        hits = _hits(
            db_session,
            account_with_transactions,
            iban="",
            subject="AXB",
            subject_mode=SubjectMode.EQUALS,
        )

        assert hits == ["AXB"]

    def test_another_iban_still_matches_nothing(
        self, db_session, account_with_transactions
    ):
        hits = _hits(
            db_session,
            account_with_transactions,
            iban="AT00 0000 0000 0000 0000",
            subject_mode=SubjectMode.CONTAINS,
        )

        assert hits == []


class TestPartnerSearch:
    @pytest.fixture
    def partners(self, db_session):
        db_session.add_all(
            [
                Org(id="vbw", label="V", sort_order=1),
                State(id="up", label="U", sort_order=1),
            ]
        )
        db_session.commit()
        db_session.add_all(
            [
                Member(vorname="Max_M", nachname="Muster", org_id="vbw", state_id="up"),
                Member(vorname="MaxXM", nachname="Muster", org_id="vbw", state_id="up"),
                Contact(
                    name="Fa_Con", kontakttyp="person", org_id="vbw", zustellungen=True
                ),
                Contact(
                    name="FaXCon", kontakttyp="person", org_id="vbw", zustellungen=True
                ),
                P4xSpecialcontact(cn="Spe_Cial"),
                P4xSpecialcontact(cn="SpeXCial"),
                P4xAccount(iban="AT611904300234573201", label="Ko_nto", init_balance=0),
                P4xAccount(iban="AT942011100005301947", label="KoXnto", init_balance=0),
            ]
        )
        db_session.commit()

    @pytest.mark.parametrize("term", ["%%%", "___", "%_%", "\\\\\\"])
    def test_wildcards_alone_find_nothing(self, db_session, partners, term):
        assert p4x_partner_service.search_partners(db_session, term) == []

    @pytest.mark.parametrize(
        ("term", "kind"),
        [
            ("x_M", "member"),
            ("Fa_C", "contact"),
            ("Spe_C", "special"),
            ("Ko_n", "account"),
        ],
    )
    def test_an_underscore_is_text_for_every_partner_type(
        self, db_session, partners, term, kind
    ):
        results = p4x_partner_service.search_partners(db_session, term)

        assert [r.type for r in results] == [kind]
        assert "_" in results[0].label

    def test_plain_terms_still_find_all_types(self, db_session, partners):
        results = p4x_partner_service.search_partners(db_session, "Con")

        assert {r.type for r in results} == {"contact"}
        assert len(results) == 2


def test_escape_like():
    assert escape_like("a%b_c\\d") == "a\\%b\\_c\\\\d"


def test_escape_like_leaves_other_text_alone():
    assert escape_like("Beitrag 2026, Müller & Söhne") == "Beitrag 2026, Müller & Söhne"
