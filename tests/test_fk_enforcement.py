"""Proves the FK ondelete/onupdate constraints (Alembic revision
3f58e5fc7f5f) are actually enforced by the database — the reason the whole
test suite was moved off SQLite onto real PostgreSQL. SQLite never enforces
foreign keys by default, so none of this was ever exercised before."""

import datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.models.contact import Contact
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.p4x_account import P4xAccount
from app.models.p4x_partner import P4xPartner
from app.models.p4x_specialcontact import P4xSpecialcontact
from app.models.p4x_transaction import P4xTransaction
from app.models.personal_access_token import PersonalAccessToken
from app.models.role import Role
from app.models.standesdb_image import StandesdbImage


def _seed_p4x_account(db) -> P4xAccount:
    account = P4xAccount(iban="AT611904300234573201", label="FK Test Account")
    db.add(account)
    db.commit()
    return account


def _seed_p4x_transaction(db, account: P4xAccount, hash_suffix: str) -> P4xTransaction:
    tx = P4xTransaction(
        sha256_hash=f"fk_test_{hash_suffix}",
        booking=datetime.date(2026, 1, 1),
        valuation=datetime.date(2026, 1, 1),
        iban="AT999",
        amount=10.0,
        subject="FK test",
        p4x_account_id=account.id,
    )
    db.add(tx)
    db.commit()
    return tx


def _seed_member_with_role(db) -> tuple[Member, Role, MemberRole]:
    member = Member(vorname="Test", nachname="User")
    role = Role(id="fk-test-role", label="FK Test Role")
    db.add_all([member, role])
    db.commit()

    member_role = MemberRole(
        member_id=member.id,
        role_id=role.id,
        startdate=datetime.date(2020, 1, 1),
    )
    db.add(member_role)
    db.commit()
    return member, role, member_role


class TestFkOndeleteRestrict:
    def test_deleting_a_role_still_assigned_to_a_member_is_blocked(self, db_session):
        """members_roles.role_id -> roles.id is ON DELETE RESTRICT."""
        _member, role, _member_role = _seed_member_with_role(db_session)

        db_session.delete(role)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestFkOndeleteCascade:
    def test_deleting_a_member_cascades_to_their_role_assignments(self, db_session):
        """members_roles.member_id -> members.id is ON DELETE CASCADE."""
        member, _role, member_role = _seed_member_with_role(db_session)
        pk = (member_role.member_id, member_role.role_id, member_role.startdate)

        # A Core-level DELETE, not session.delete(member): the ORM's own
        # unit-of-work would otherwise try to null out members_roles.member_id
        # itself (as if onupdate were SET NULL) before the DB gets a chance to
        # apply the real ON DELETE CASCADE — and fail, since member_id is part
        # of that table's primary key. This way the DB constraint is what
        # actually gets exercised, not SQLAlchemy's in-memory cascade guess.
        db_session.execute(delete(Member).where(Member.id == member.id))
        db_session.commit()

        assert db_session.get(MemberRole, pk) is None


class TestFkInsertEnforcement:
    def test_inserting_a_member_role_with_unknown_role_id_is_rejected(self, db_session):
        member = Member(vorname="Test", nachname="User")
        db_session.add(member)
        db_session.commit()

        db_session.add(
            MemberRole(
                member_id=member.id,
                role_id="does-not-exist",
                startdate=datetime.date(2020, 1, 1),
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestPersonalAccessTokenFk:
    """personal_access_tokens.member_id -> members.id (Alembic revision
    5292367fb696) replaces the previously vestigial tokenable_type/
    tokenable_id pair — no code ever branched on the discriminator, so this
    became a real FK instead of an exclusive-arc redesign."""

    def test_inserting_a_token_with_unknown_member_id_is_rejected(self, db_session):
        db_session.add(
            PersonalAccessToken(member_id=999999, name="session", token="jti-1")
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_deleting_a_member_cascades_to_their_sessions(self, db_session):
        member = Member(vorname="Test", nachname="User")
        db_session.add(member)
        db_session.commit()

        token = PersonalAccessToken(member_id=member.id, name="session", token="jti-2")
        db_session.add(token)
        db_session.commit()
        token_id = token.id

        db_session.execute(delete(Member).where(Member.id == member.id))
        db_session.commit()

        assert db_session.get(PersonalAccessToken, token_id) is None


class TestStandesdbImageExclusiveArc:
    """standesdb_images.owner_member_id/owner_contact_id (Alembic revision
    1e14a4e8ec0c) replaces owner_type/owner_id with an exclusive-arc pair of
    real FKs — exactly one must be set, enforced by a CHECK constraint."""

    def test_both_owner_columns_set_is_rejected(self, db_session):
        member = Member(vorname="Test", nachname="User")
        contact = Contact(kontakttyp="person", name="Test Contact")
        db_session.add_all([member, contact])
        db_session.commit()

        db_session.add(
            StandesdbImage(
                owner_member_id=member.id,
                owner_contact_id=contact.id,
                sha256_hash="a" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_neither_owner_column_set_is_rejected(self, db_session):
        db_session.add(StandesdbImage(sha256_hash="b" * 64))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_inserting_with_unknown_owner_member_id_is_rejected(self, db_session):
        db_session.add(StandesdbImage(owner_member_id=999999, sha256_hash="c" * 64))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_inserting_with_unknown_owner_contact_id_is_rejected(self, db_session):
        db_session.add(StandesdbImage(owner_contact_id=999999, sha256_hash="d" * 64))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_deleting_a_member_cascades_to_their_images(self, db_session):
        member = Member(vorname="Test", nachname="User")
        db_session.add(member)
        db_session.commit()

        img = StandesdbImage(owner_member_id=member.id, sha256_hash="e" * 64)
        db_session.add(img)
        db_session.commit()
        img_id = img.id

        db_session.execute(delete(Member).where(Member.id == member.id))
        db_session.commit()

        assert db_session.get(StandesdbImage, img_id) is None

    def test_deleting_a_contact_cascades_to_their_images(self, db_session):
        contact = Contact(kontakttyp="person", name="Test Contact")
        db_session.add(contact)
        db_session.commit()

        img = StandesdbImage(owner_contact_id=contact.id, sha256_hash="f" * 64)
        db_session.add(img)
        db_session.commit()
        img_id = img.id

        db_session.execute(delete(Contact).where(Contact.id == contact.id))
        db_session.commit()

        assert db_session.get(StandesdbImage, img_id) is None


class TestP4xPartnerExclusiveArc:
    """p4x_partners.member_id/contact_id/p4x_account_id/
    p4x_specialcontact_id (Alembic revision 514ac66eb66e) replaces
    partner_type/partner_id with an exclusive-arc pair of real FKs —
    exactly one must be set, enforced by a CHECK constraint. ondelete is
    RESTRICT (not CASCADE like standesdb_images) since a P4xPartner row is
    a financial/accounting link, not owned content."""

    def test_two_columns_set_is_rejected(self, db_session):
        member = Member(vorname="Test", nachname="User")
        contact = Contact(kontakttyp="person", name="Test Contact")
        db_session.add_all([member, contact])
        db_session.commit()

        db_session.add(
            P4xPartner(iban="AT001", member_id=member.id, contact_id=contact.id)
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_no_columns_set_is_rejected(self, db_session):
        db_session.add(P4xPartner(iban="AT002"))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_inserting_with_unknown_member_id_is_rejected(self, db_session):
        db_session.add(P4xPartner(iban="AT003", member_id=999999))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_inserting_with_unknown_contact_id_is_rejected(self, db_session):
        db_session.add(P4xPartner(iban="AT004", contact_id=999999))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_inserting_with_unknown_p4x_account_id_is_rejected(self, db_session):
        db_session.add(P4xPartner(iban="AT005", p4x_account_id=999999))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_inserting_with_unknown_p4x_specialcontact_id_is_rejected(self, db_session):
        db_session.add(P4xPartner(iban="AT006", p4x_specialcontact_id=999999))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_deleting_a_member_with_a_partner_record_is_restricted(self, db_session):
        member = Member(vorname="Test", nachname="User")
        db_session.add(member)
        db_session.commit()
        db_session.add(P4xPartner(iban="AT007", member_id=member.id))
        db_session.commit()

        db_session.delete(member)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_deleting_a_contact_with_a_partner_record_is_restricted(self, db_session):
        contact = Contact(kontakttyp="person", name="Test Contact")
        db_session.add(contact)
        db_session.commit()
        db_session.add(P4xPartner(iban="AT008", contact_id=contact.id))
        db_session.commit()

        db_session.delete(contact)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_deleting_a_p4x_account_with_a_partner_record_is_restricted(
        self, db_session
    ):
        account = _seed_p4x_account(db_session)
        db_session.add(P4xPartner(iban="AT009", p4x_account_id=account.id))
        db_session.commit()

        db_session.delete(account)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_deleting_a_p4x_specialcontact_with_a_partner_record_is_restricted(
        self, db_session
    ):
        special = P4xSpecialcontact(cn="Test Special")
        db_session.add(special)
        db_session.commit()
        db_session.add(P4xPartner(iban="AT010", p4x_specialcontact_id=special.id))
        db_session.commit()

        db_session.delete(special)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestP4xTransactionDelegatingExclusiveArc:
    """p4x_transactions.delegating_member_id/delegating_contact_id/
    delegating_p4x_account_id/delegating_p4x_specialcontact_id (Alembic
    revision 514ac66eb66e) replaces delegating_partner_type/
    delegating_partner_id with an exclusive-arc pair of real FKs — at most
    one may be set (the field is optional, "all four NULL" stays valid).
    ondelete is SET NULL (not RESTRICT/CASCADE) since this is an optional
    display annotation, not the transaction's own financial identity."""

    def test_two_delegating_columns_set_is_rejected(self, db_session):
        account = _seed_p4x_account(db_session)
        member = Member(vorname="Test", nachname="User")
        contact = Contact(kontakttyp="person", name="Test Contact")
        db_session.add_all([member, contact])
        db_session.commit()

        tx = _seed_p4x_transaction(db_session, account, "two_cols")
        tx.delegating_member_id = member.id
        tx.delegating_contact_id = contact.id
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_all_four_delegating_columns_null_is_allowed(self, db_session):
        account = _seed_p4x_account(db_session)
        tx = _seed_p4x_transaction(db_session, account, "all_null")
        assert tx.delegating_partner_type is None
        assert tx.delegating_partner_id is None

    def test_inserting_with_unknown_delegating_member_id_is_rejected(self, db_session):
        account = _seed_p4x_account(db_session)
        tx = _seed_p4x_transaction(db_session, account, "unk_member")
        tx.delegating_member_id = 999999
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_inserting_with_unknown_delegating_contact_id_is_rejected(self, db_session):
        account = _seed_p4x_account(db_session)
        tx = _seed_p4x_transaction(db_session, account, "unk_contact")
        tx.delegating_contact_id = 999999
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_inserting_with_unknown_delegating_p4x_account_id_is_rejected(
        self, db_session
    ):
        account = _seed_p4x_account(db_session)
        tx = _seed_p4x_transaction(db_session, account, "unk_account")
        tx.delegating_p4x_account_id = 999999
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_inserting_with_unknown_delegating_p4x_specialcontact_id_is_rejected(
        self, db_session
    ):
        account = _seed_p4x_account(db_session)
        tx = _seed_p4x_transaction(db_session, account, "unk_special")
        tx.delegating_p4x_specialcontact_id = 999999
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_deleting_a_delegated_member_sets_delegating_member_id_null(
        self, db_session
    ):
        account = _seed_p4x_account(db_session)
        member = Member(vorname="Test", nachname="User")
        db_session.add(member)
        db_session.commit()

        tx = _seed_p4x_transaction(db_session, account, "set_null_member")
        tx.delegating_member_id = member.id
        db_session.commit()
        tx_id = tx.id

        db_session.execute(delete(Member).where(Member.id == member.id))
        db_session.commit()

        refreshed = db_session.get(P4xTransaction, tx_id)
        assert refreshed is not None
        assert refreshed.delegating_member_id is None

    def test_deleting_a_delegated_p4x_specialcontact_sets_null(self, db_session):
        account = _seed_p4x_account(db_session)
        special = P4xSpecialcontact(cn="Test Special")
        db_session.add(special)
        db_session.commit()

        tx = _seed_p4x_transaction(db_session, account, "set_null_special")
        tx.delegating_p4x_specialcontact_id = special.id
        db_session.commit()
        tx_id = tx.id

        db_session.execute(
            delete(P4xSpecialcontact).where(P4xSpecialcontact.id == special.id)
        )
        db_session.commit()

        refreshed = db_session.get(P4xTransaction, tx_id)
        assert refreshed is not None
        assert refreshed.delegating_p4x_specialcontact_id is None
