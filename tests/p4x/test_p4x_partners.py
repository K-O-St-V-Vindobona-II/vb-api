from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException

from app.models.contact import Contact
from app.models.member import Member
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_partner import P4xPartner
from app.models.p4x_specialcontact import P4xSpecialcontact
from app.models.p4x_transaction import P4xTransaction
from app.models.state import State
from app.services.p4x_service import (
    find_partner_entity,
    search_partners,
    set_transaction_partner,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(db) -> tuple[P4xAccount, Member, Contact, P4xSpecialcontact]:
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            State(id="up", label="UP", order=1),
        ]
    )
    db.commit()

    member = Member(
        vorname="Michael",
        nachname="Schimpl",
        couleurname="Kopernikus",
        email="test@test.at",
        auth_password="x",
        auth_locked=False,
        org_id="vbw",
        state_id="up",
    )
    contact = Contact(
        kontakttyp="firma",
        name="Netcup GmbH",
        org_id="vbw",
    )
    special = P4xSpecialcontact(cn="Konto-Intern(Sparkassen-Information)")
    account = P4xAccount(
        iban="AT942011100005301947",
        bic="GIBAATWWXXX",
        label="Girokonto",
        init_date=date(2017, 1, 1),
        init_balance=0,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add_all([member, contact, special, account])
    db.commit()
    db.refresh(member)
    db.refresh(contact)
    db.refresh(special)
    db.refresh(account)
    return account, member, contact, special


def _create_tx(db, account: P4xAccount, iban: str = "DE001") -> P4xTransaction:
    tx = P4xTransaction(
        sha256hash=f"partner_tx_{iban}",
        booking=date(2026, 3, 20),
        valuation=date(2026, 3, 20),
        iban=iban,
        amount=15.0,
        subject="test",
        p4x_account_id=account.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


class TestSearchPartners:
    def test_search_member(self, db_session):
        _, _member, _, _ = _seed(db_session)
        results = search_partners(db_session, "Kopernikus")
        assert len(results) == 1
        assert results[0]["type"] == "member"
        assert "Kopernikus" in results[0]["label"]

    def test_search_member_label_includes_org(self, db_session):
        _, _member, _, _ = _seed(db_session)
        results = search_partners(db_session, "Kopernikus")
        assert results[0]["label"] == "Mitglied (VBW): Michael Schimpl v/o Kopernikus"

    def test_search_contact(self, db_session):
        _, _, _contact, _ = _seed(db_session)
        results = search_partners(db_session, "Netcup")
        assert len(results) == 1
        assert results[0]["type"] == "contact"

    def test_search_special(self, db_session):
        _, _, _, _special = _seed(db_session)
        results = search_partners(db_session, "Sparkassen")
        assert len(results) == 1
        assert results[0]["type"] == "special"

    def test_search_account(self, db_session):
        _account, _, _, _ = _seed(db_session)
        results = search_partners(db_session, "Girokonto")
        assert len(results) == 1
        assert results[0]["type"] == "account"

    def test_search_min_length(self, db_session):
        _seed(db_session)
        results = search_partners(db_session, "ab")
        assert results == []

    def test_search_no_results(self, db_session):
        _seed(db_session)
        results = search_partners(db_session, "zzzzzzz")
        assert results == []

    def test_search_is_case_insensitive(self, db_session):
        """Regression test: Postgres LIKE is case-sensitive, unlike the
        legacy MySQL system's default collation. A search term in a
        different case than the stored name must still match."""
        _seed(db_session)
        results = search_partners(db_session, "kopernikus")
        assert len(results) == 1
        assert results[0]["type"] == "member"

    def test_search_multiple_types(self, db_session):
        _seed(db_session)
        db_session.add(
            Member(
                vorname="Giro",
                nachname="Test",
                email="giro@test.at",
                auth_password="x",
                auth_locked=False,
                org_id="vbw",
                state_id="up",
            )
        )
        db_session.commit()
        results = search_partners(db_session, "Giro")
        types = {r["type"] for r in results}
        assert "member" in types
        assert "account" in types


class TestFindPartnerEntity:
    def test_find_member(self, db_session):
        _, member, _, _ = _seed(db_session)
        entity = find_partner_entity(db_session, "member", member.id)
        assert entity is not None
        assert entity.id == member.id

    def test_find_contact(self, db_session):
        _, _, contact, _ = _seed(db_session)
        entity = find_partner_entity(db_session, "contact", contact.id)
        assert entity is not None

    def test_find_special(self, db_session):
        _, _, _, special = _seed(db_session)
        entity = find_partner_entity(db_session, "special", special.id)
        assert entity is not None

    def test_find_account(self, db_session):
        account, _, _, _ = _seed(db_session)
        entity = find_partner_entity(db_session, "account", account.id)
        assert entity is not None

    def test_find_unknown_type(self, db_session):
        assert find_partner_entity(db_session, "unknown", 1) is None

    def test_find_nonexistent_id(self, db_session):
        _seed(db_session)
        assert find_partner_entity(db_session, "member", 99999) is None


class TestSetTransactionPartner:
    def test_set_partner(self, db_session):
        account, member, _, _ = _seed(db_session)
        tx = _create_tx(db_session, account)

        set_transaction_partner(
            db_session,
            tx,
            partner_data={"type": "member", "id": member.id},
            has_delegating=False,
            delegating_data=None,
        )

        partner = (
            db_session.query(P4xPartner)
            .filter(
                P4xPartner.iban == tx.iban,
                P4xPartner.deleted_at.is_(None),
            )
            .first()
        )
        assert partner is not None
        assert partner.partner_type == "member"
        assert partner.partner_id == member.id

    def test_unset_partner(self, db_session):
        account, member, _, _ = _seed(db_session)
        tx = _create_tx(db_session, account)

        set_transaction_partner(
            db_session,
            tx,
            partner_data={"type": "member", "id": member.id},
            has_delegating=False,
            delegating_data=None,
        )
        set_transaction_partner(
            db_session,
            tx,
            partner_data=None,
            has_delegating=False,
            delegating_data=None,
        )

        active = (
            db_session.query(P4xPartner)
            .filter(
                P4xPartner.iban == tx.iban,
                P4xPartner.deleted_at.is_(None),
            )
            .first()
        )
        assert active is None

    def test_upsert_existing_partner(self, db_session):
        account, member, contact, _ = _seed(db_session)
        tx = _create_tx(db_session, account)

        set_transaction_partner(
            db_session,
            tx,
            partner_data={"type": "member", "id": member.id},
            has_delegating=False,
            delegating_data=None,
        )
        set_transaction_partner(
            db_session,
            tx,
            partner_data={"type": "contact", "id": contact.id},
            has_delegating=False,
            delegating_data=None,
        )

        partner = (
            db_session.query(P4xPartner)
            .filter(
                P4xPartner.iban == tx.iban,
                P4xPartner.deleted_at.is_(None),
            )
            .first()
        )
        assert partner.partner_type == "contact"
        assert partner.partner_id == contact.id

    def test_restore_soft_deleted_partner(self, db_session):
        account, member, _, _ = _seed(db_session)
        tx = _create_tx(db_session, account)

        set_transaction_partner(
            db_session,
            tx,
            partner_data={"type": "member", "id": member.id},
            has_delegating=False,
            delegating_data=None,
        )
        set_transaction_partner(
            db_session,
            tx,
            partner_data=None,
            has_delegating=False,
            delegating_data=None,
        )
        set_transaction_partner(
            db_session,
            tx,
            partner_data={"type": "member", "id": member.id},
            has_delegating=False,
            delegating_data=None,
        )

        active = (
            db_session.query(P4xPartner)
            .filter(
                P4xPartner.iban == tx.iban,
                P4xPartner.deleted_at.is_(None),
            )
            .first()
        )
        assert active is not None
        assert active.partner_type == "member"

    def test_set_partner_with_unknown_id_raises_404(self, db_session):
        account, _member, _contact, _special = _seed(db_session)
        tx = _create_tx(db_session, account)

        with pytest.raises(HTTPException) as exc_info:
            set_transaction_partner(
                db_session,
                tx,
                partner_data={"type": "member", "id": 999999},
                has_delegating=False,
                delegating_data=None,
            )
        assert exc_info.value.status_code == 404


class TestDelegatingPartner:
    def test_set_delegating(self, db_session):
        account, member, contact, _ = _seed(db_session)
        tx = _create_tx(db_session, account)

        set_transaction_partner(
            db_session,
            tx,
            partner_data={"type": "member", "id": member.id},
            has_delegating=True,
            delegating_data={"type": "contact", "id": contact.id},
        )

        db_session.refresh(tx)
        assert tx.delegating_partner_type == "contact"
        assert tx.delegating_partner_id == contact.id

    def test_unset_delegating(self, db_session):
        account, member, contact, _ = _seed(db_session)
        tx = _create_tx(db_session, account)

        set_transaction_partner(
            db_session,
            tx,
            partner_data={"type": "member", "id": member.id},
            has_delegating=True,
            delegating_data={"type": "contact", "id": contact.id},
        )
        set_transaction_partner(
            db_session,
            tx,
            partner_data={"type": "member", "id": member.id},
            has_delegating=False,
            delegating_data=None,
        )

        db_session.refresh(tx)
        assert tx.delegating_partner_type is None
        assert tx.delegating_partner_id is None

    def test_set_delegating_with_unknown_id_raises_404(self, db_session):
        account, member, _contact, _special = _seed(db_session)
        tx = _create_tx(db_session, account)

        with pytest.raises(HTTPException) as exc_info:
            set_transaction_partner(
                db_session,
                tx,
                partner_data={"type": "member", "id": member.id},
                has_delegating=True,
                delegating_data={"type": "member", "id": 999999},
            )
        assert exc_info.value.status_code == 404

    def test_no_partner_clears_delegating(self, db_session):
        account, member, contact, _ = _seed(db_session)
        tx = _create_tx(db_session, account)

        set_transaction_partner(
            db_session,
            tx,
            partner_data={"type": "member", "id": member.id},
            has_delegating=True,
            delegating_data={"type": "contact", "id": contact.id},
        )
        set_transaction_partner(
            db_session,
            tx,
            partner_data=None,
            has_delegating=False,
            delegating_data=None,
        )

        db_session.refresh(tx)
        assert tx.delegating_partner_type is None
