from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from fastapi import HTTPException, status

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from app.models.p4x_transaction import P4xTransaction

from app.models.contact import Contact
from app.models.member import Member
from app.models.p4x_account import P4xAccount
from app.models.p4x_partner import P4xPartner
from app.models.p4x_specialcontact import P4xSpecialcontact
from app.schemas.p4x import PartnerSearchResult

# ---------------------------------------------------------------------------
# Partner search
# ---------------------------------------------------------------------------


def search_partners(db: Session, term: str) -> list[PartnerSearchResult]:
    if len(term) < 3:
        return []

    pattern = f"%{term}%"
    results: list[PartnerSearchResult] = []

    members = (
        db.query(Member)
        .filter(
            (Member.vorname.ilike(pattern))
            | (Member.nachname.ilike(pattern))
            | (Member.couleurname.ilike(pattern)),
        )
        .all()
    )
    for m in members:
        org_label = m.org_id.upper() if m.org_id else "?"
        results.append(
            PartnerSearchResult(
                type="member", id=m.id_uuid, label=f"Mitglied ({org_label}): {m.cn}"
            )
        )

    contacts = (
        db.query(Contact)
        .filter(
            Contact.deleted_at.is_(None),
            (Contact.name.ilike(pattern)) | (Contact.couleurname.ilike(pattern)),
        )
        .all()
    )
    results.extend(
        PartnerSearchResult(type="contact", id=c.id_uuid, label=f"Kontakt: {c.cn}")
        for c in contacts
    )

    specials = (
        db.query(P4xSpecialcontact)
        .filter(
            P4xSpecialcontact.cn.ilike(pattern),
        )
        .all()
    )
    results.extend(
        PartnerSearchResult(type="special", id=s.id_uuid, label=f"Spezial: {s.cn}")
        for s in specials
    )

    accounts = (
        db.query(P4xAccount)
        .filter(
            P4xAccount.deleted_at.is_(None),
            P4xAccount.label.ilike(pattern),
        )
        .all()
    )
    results.extend(
        PartnerSearchResult(type="account", id=a.id_uuid, label=f"Konto: {a.cn}")
        for a in accounts
    )

    return results


# ---------------------------------------------------------------------------
# Partner assignment (1:1 from TransactionController::setPartner)
# ---------------------------------------------------------------------------


def find_partner_entity(
    db: Session,
    partner_type: str,
    partner_id: uuid.UUID,
) -> Member | Contact | P4xAccount | P4xSpecialcontact | None:
    """Looks up the entity a search result/set-partner request refers to,
    keyed by its id_uuid - the identifier p4x_partners' own FK columns
    store, and the one search_partners()/PartnerSearchResult hand out.
    None of members/contacts/p4x_accounts/p4x_special_contacts has its
    own UUID primary key yet (each keeps its own Final-Cutover for a
    later slice), so this deliberately queries the additive id_uuid
    column, not id."""
    if partner_type == "member":
        return db.query(Member).filter(Member.id_uuid == partner_id).first()
    if partner_type == "contact":
        return db.query(Contact).filter(Contact.id_uuid == partner_id).first()
    if partner_type == "account":
        return db.query(P4xAccount).filter(P4xAccount.id_uuid == partner_id).first()
    if partner_type == "special":
        return (
            db.query(P4xSpecialcontact)
            .filter(
                P4xSpecialcontact.id_uuid == partner_id,
            )
            .first()
        )
    return None


def find_partner_entity_by_legacy_id(
    db: Session,
    partner_type: str,
    partner_id: int,
) -> Member | Contact | P4xAccount | P4xSpecialcontact | None:
    """Same lookup as find_partner_entity(), but keyed by the target's
    still-integer primary key - used only for a transaction's delegating
    partner, since p4x_transactions.delegating_* stores that legacy id
    until that table's own UUID cutover unifies it with the identifier
    used above."""
    if partner_type == "member":
        return db.query(Member).filter(Member.id == partner_id).first()
    if partner_type == "contact":
        return db.query(Contact).filter(Contact.id == partner_id).first()
    if partner_type == "account":
        return db.query(P4xAccount).filter(P4xAccount.id == partner_id).first()
    if partner_type == "special":
        return (
            db.query(P4xSpecialcontact)
            .filter(
                P4xSpecialcontact.id == partner_id,
            )
            .first()
        )
    return None


def _clear_delegating(transaction: P4xTransaction) -> None:
    transaction.delegating_member_id = None
    transaction.delegating_contact_id = None
    transaction.delegating_p4x_account_id = None
    transaction.delegating_p4x_specialcontact_id = None


def set_transaction_partner(  # noqa: C901, PLR0912
    db: Session,
    transaction: P4xTransaction,
    partner_data: dict[str, str | uuid.UUID] | None,
    has_delegating: bool,  # noqa: FBT001
    delegating_data: dict[str, str | uuid.UUID] | None,
) -> None:
    """Sets the transaction's partner and, independently, its delegating
    partner. Kept as one function since both halves share the same
    404-on-missing-remote / exclusive-arc-column-reset shape - splitting
    them apart would be the artificial helper-spaghetti this review is
    meant to remove, not genuine complexity reduction.

    Both partner_data["id"] and delegating_data["id"] are the target
    entity's id_uuid - the same identifier search_partners() hands out
    for both fields, since the frontend reuses one partner-search widget
    for both. What differs is the write target: partner.member_id (etc.)
    is itself UUID now, so remote.id_uuid is stored; transaction.
    delegating_member_id (etc.) stays a legacy integer column until that
    table's own UUID cutover, so remote.id is stored there instead."""
    now = datetime.now(UTC)

    if partner_data:
        p_type = str(partner_data["type"])
        p_id = cast("uuid.UUID", partner_data["id"])
        remote = find_partner_entity(db, p_type, p_id)
        if not remote:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Angegebener Partner wurde nicht gefunden.",
            )
        partner = (
            db.query(P4xPartner)
            .filter(
                P4xPartner.iban == transaction.iban,
            )
            .first()
        )
        if not partner:
            partner = P4xPartner(iban=transaction.iban)
            db.add(partner)
        # Reset all four exclusive-arc columns, then set the one matching
        # p_type. The reset is required because this is an upsert: a
        # partner's type can change between calls (e.g. member -> contact).
        partner.member_id = None
        partner.contact_id = None
        partner.p4x_account_id = None
        partner.p4x_specialcontact_id = None
        if p_type == "member":
            partner.member_id = remote.id_uuid
        elif p_type == "contact":
            partner.contact_id = remote.id_uuid
        elif p_type == "account":
            partner.p4x_account_id = remote.id_uuid
        else:
            partner.p4x_specialcontact_id = remote.id_uuid
        partner.deleted_at = None
        db.flush()
    else:
        db.query(P4xPartner).filter(
            P4xPartner.iban == transaction.iban,
            P4xPartner.deleted_at.is_(None),
        ).update({"deleted_at": now})

    if has_delegating and delegating_data:
        d_type = str(delegating_data["type"])
        d_id = cast("uuid.UUID", delegating_data["id"])
        remote = find_partner_entity(db, d_type, d_id)
        if not remote:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Angegebener delegierender Partner wurde nicht gefunden.",
            )
        _clear_delegating(transaction)
        if d_type == "member":
            transaction.delegating_member_id = remote.id
        elif d_type == "contact":
            transaction.delegating_contact_id = remote.id
        elif d_type == "account":
            transaction.delegating_p4x_account_id = remote.id
        else:
            transaction.delegating_p4x_specialcontact_id = remote.id
    else:
        _clear_delegating(transaction)

    db.commit()


# ---------------------------------------------------------------------------
# Transaction edit (comment + attachment)
# ---------------------------------------------------------------------------


def update_transaction_meta(
    db: Session,
    transaction: P4xTransaction,
    comment: str | None,
    file_bytes: bytes | None,
    delete_attachment: bool,  # noqa: FBT001
) -> None:
    transaction.comment = comment

    if transaction.has_attachment and delete_attachment:
        transaction.attachment = None
    elif not transaction.has_attachment and file_bytes:
        transaction.attachment = base64.b64encode(file_bytes).decode()

    db.commit()
