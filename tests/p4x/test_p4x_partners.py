import uuid
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
from app.services.p4x_partner_service import (
    find_partner_entity,
    search_partners,
    set_transaction_partner,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(db) -> tuple[P4xAccount, Member, Contact, P4xSpecialcontact]:
    db.add_all(
        [
            Org(id="vbw", label="VBW", sort_order=1),
            State(id="up", label="UP", sort_order=1),
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
        kontakttyp="organisation",
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
        sha256_hash=f"partner_tx_{iban}",
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
        assert results[0].type == "member"
        assert "Kopernikus" in results[0].label

    def test_search_member_label_includes_org(self, db_session):
        _, _member, _, _ = _seed(db_session)
        results = search_partners(db_session, "Kopernikus")
        assert results[0].label == "Mitglied (VBW): Michael Schimpl v/o Kopernikus"

    def test_search_contact(self, db_session):
        _, _, _contact, _ = _seed(db_session)
        results = search_partners(db_session, "Netcup")
        assert len(results) == 1
        assert results[0].type == "contact"

    def test_search_special(self, db_session):
        _, _, _, _special = _seed(db_session)
        results = search_partners(db_session, "Sparkassen")
        assert len(results) == 1
        assert results[0].type == "special"

    def test_search_account(self, db_session):
        _account, _, _, _ = _seed(db_session)
        results = search_partners(db_session, "Girokonto")
        assert len(results) == 1
        assert results[0].type == "account"

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
        predecessor system's database default collation. A search term in
        a different case than the stored name must still match."""
        _seed(db_session)
        results = search_partners(db_session, "kopernikus")
        assert len(results) == 1
        assert results[0].type == "member"

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
        types = {r.type for r in results}
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
        assert find_partner_entity(db_session, "unknown", uuid.uuid4()) is None

    def test_find_nonexistent_id(self, db_session):
        _seed(db_session)
        assert find_partner_entity(db_session, "member", uuid.uuid4()) is None


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
                partner_data={"type": "member", "id": uuid.uuid4()},
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
                delegating_data={"type": "member", "id": uuid.uuid4()},
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


_PARTNER_ARC_COLUMNS = {
    "member": "member_id",
    "contact": "contact_id",
    "account": "p4x_account_id",
    "special": "p4x_special_contact_id",
}
_DELEGATING_ARC_COLUMNS = {
    "member": "delegating_member_id",
    "contact": "delegating_contact_id",
    "account": "delegating_p4x_account_id",
    "special": "delegating_p4x_special_contact_id",
}


def _entities(account, member, contact, special):
    return {
        "member": member,
        "contact": contact,
        "account": account,
        "special": special,
    }


def _stored_partner(db, tx):
    return db.query(P4xPartner).filter(P4xPartner.iban == tx.iban).one()


class TestEveryKindOfPartner:
    """A partner is exactly one of a member, a contact, an account of the
    club or a special contact, and changing the kind replaces the old link."""

    @pytest.mark.parametrize("kind", ["member", "contact", "account", "special"])
    def test_each_kind_is_stored_in_its_own_column_only(self, db_session, kind):
        account, member, contact, special = _seed(db_session)
        entity = _entities(account, member, contact, special)[kind]
        tx = _create_tx(db_session, account)

        set_transaction_partner(
            db_session,
            tx,
            partner_data={"type": kind, "id": entity.id},
            has_delegating=False,
            delegating_data=None,
        )

        partner = _stored_partner(db_session, tx)
        assert partner.partner_type == kind
        assert partner.partner_id == entity.id
        stored = {
            name: getattr(partner, column)
            for name, column in _PARTNER_ARC_COLUMNS.items()
        }
        assert stored == {
            name: (entity.id if name == kind else None) for name in stored
        }

    def test_changing_the_kind_resets_the_previous_link(self, db_session):
        account, member, contact, special = _seed(db_session)
        entities = _entities(account, member, contact, special)
        tx = _create_tx(db_session, account)

        for kind in ("member", "account", "special", "contact", "member"):
            set_transaction_partner(
                db_session,
                tx,
                partner_data={"type": kind, "id": entities[kind].id},
                has_delegating=False,
                delegating_data=None,
            )

            partner = _stored_partner(db_session, tx)
            linked = [
                name
                for name, column in _PARTNER_ARC_COLUMNS.items()
                if getattr(partner, column) is not None
            ]
            assert linked == [kind]
            assert partner.deleted_at is None

    @pytest.mark.parametrize("kind", ["member", "contact", "account", "special"])
    def test_each_kind_of_delegating_partner_is_stored_in_its_own_column_only(
        self, db_session, kind
    ):
        account, member, contact, special = _seed(db_session)
        entity = _entities(account, member, contact, special)[kind]
        tx = _create_tx(db_session, account)

        set_transaction_partner(
            db_session,
            tx,
            partner_data={"type": "member", "id": member.id},
            has_delegating=True,
            delegating_data={"type": kind, "id": entity.id},
        )

        db_session.refresh(tx)
        assert tx.delegating_partner_type == kind
        assert tx.delegating_partner_id == entity.id
        stored = {
            name: getattr(tx, column)
            for name, column in _DELEGATING_ARC_COLUMNS.items()
        }
        assert stored == {
            name: (entity.id if name == kind else None) for name in stored
        }

    def test_changing_the_delegating_kind_resets_the_previous_link(self, db_session):
        account, member, contact, special = _seed(db_session)
        entities = _entities(account, member, contact, special)
        tx = _create_tx(db_session, account)

        for kind in ("account", "special", "member", "contact"):
            set_transaction_partner(
                db_session,
                tx,
                partner_data={"type": "member", "id": member.id},
                has_delegating=True,
                delegating_data={"type": kind, "id": entities[kind].id},
            )

            db_session.refresh(tx)
            linked = [
                name
                for name, column in _DELEGATING_ARC_COLUMNS.items()
                if getattr(tx, column) is not None
            ]
            assert linked == [kind]


class TestPartnerRowWithoutAReference:
    """The database CHECK keeps such a row out; the model refuses to guess."""

    def test_the_type_of_a_row_without_a_reference_is_an_error(self):
        with pytest.raises(ValueError, match="exclusive-arc CHECK"):
            _ = P4xPartner(iban="DE001").partner_type

    def test_the_id_of_a_row_without_a_reference_is_an_error(self):
        with pytest.raises(ValueError, match="exclusive-arc CHECK"):
            _ = P4xPartner(iban="DE001").partner_id
