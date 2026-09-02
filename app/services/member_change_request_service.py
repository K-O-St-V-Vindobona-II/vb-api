"""Self-service "Meine Stammdaten" change requests: a member proposes
changes to their own personal data, an org-scoped standesdb admin approves
or rejects them field-by-field in a single, atomic review session (no
persisted "partially decided" state - see MemberChangeRequest's docstring).
"""

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, cast

from fastapi import HTTPException, status

from app.models.enums import MemberChangeRequestStatus, MemberDeliveryPreference
from app.models.member import Member
from app.models.member_change_request import MemberChangeRequest
from app.schemas.standesdb import MemberSaveRequest, MemberSelfServiceSaveRequest
from app.services.permission_service import calculate_permissions
from app.services.standesdb_service import (
    apply_member_input,
    validate_member_org,
    validate_member_uniqueness,
    values_differ,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


def _json_safe(value: object) -> object:
    """JSONB has no native date type - the only self-service field this
    ever applies to is geburtsdatum, everything else (str/int/bool/None,
    and MemberDeliveryPreference since StrEnum already subclasses str) is
    JSON-native as-is."""
    if isinstance(value, date):
        return value.isoformat()
    return value


def submit_change_request(
    db: Session, member: Member, proposed: MemberSelfServiceSaveRequest
) -> MemberChangeRequest | None:
    """Computes the diff between the submitted self-service fields and the
    member's current live values, then upserts the member's single pending
    request (find-or-create - the partial unique index on member_id WHERE
    status='pending' makes "at most one" safe even under a race) with it.

    An empty diff (nothing actually changed vs. the live record - either
    the member resubmitted unchanged values, or reverted every field back
    to its original value) deletes any existing pending request instead of
    leaving a request with nothing to review, and returns None.
    """
    proposed_dict = proposed.model_dump()
    diff: dict[str, dict[str, object]] = {}
    for field, new_val in proposed_dict.items():
        old_val = getattr(member, field, None)
        if values_differ(old_val, new_val):
            diff[field] = {"old": _json_safe(old_val), "new": _json_safe(new_val)}

    existing = get_own_pending_request(db, member)

    if not diff:
        if existing:
            db.delete(existing)
            db.commit()
        return None

    if existing:
        existing.proposed_data = cast("dict[str, object]", diff)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    request = MemberChangeRequest(
        member_id=member.id,
        status=MemberChangeRequestStatus.PENDING,
        proposed_data=cast("dict[str, object]", diff),
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def get_own_pending_request(db: Session, member: Member) -> MemberChangeRequest | None:
    return (
        db.query(MemberChangeRequest)
        .filter(
            MemberChangeRequest.member_id == member.id,
            MemberChangeRequest.status == MemberChangeRequestStatus.PENDING,
        )
        .first()
    )


def get_pending_requests_for_admin(
    db: Session, current_user: Member
) -> list[MemberChangeRequest]:
    """Org-scoped: only requests for members whose org the caller actually
    administers - an admin holding only standesdbVbwAdmin never sees vbn
    rows, structurally (query-level filter), not just UI-hidden."""
    perms = calculate_permissions(current_user)
    admin_org_ids = [
        org_id
        for org_id in ("vbw", "vbn")
        if f"standesdb{org_id.capitalize()}Admin" in perms
    ]
    if not admin_org_ids:
        return []

    return (
        db.query(MemberChangeRequest)
        .join(Member, MemberChangeRequest.member_id == Member.id)
        .filter(
            MemberChangeRequest.status == MemberChangeRequestStatus.PENDING,
            Member.org_id.in_(admin_org_ids),
        )
        .order_by(MemberChangeRequest.created_at.asc())
        .all()
    )


def get_change_request_or_404(
    db: Session, request_id: uuid.UUID
) -> MemberChangeRequest:
    request = db.get(MemberChangeRequest, request_id)
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Änderungsantrag nicht gefunden.",
        )
    return request


def _build_current_full_request_dict(member: Member) -> dict[str, object]:
    """Builds a MemberSaveRequest-shaped dict from the member's LIVE
    values - every single field MemberSaveRequest declares, built
    explicitly off member.*/its relationships. Deliberately NOT derived
    from a partial dict: every MemberSaveRequest field has a Pydantic
    default (e.g. gruender: bool = False), so omitting a field here would
    silently reset it to that default the moment
    MemberSaveRequest.model_validate(current_full) runs below - exactly the
    mechanism that would otherwise wipe roles_history/badges/keys/org_id/
    gruender/etc. when a self-service request gets resolved. A regression
    test asserts this dict's key set matches MemberSaveRequest.model_fields
    exactly, so a future field added to MemberSaveRequest fails loudly here
    instead of silently under-covering.
    """
    return {
        "vortitel": member.vortitel,
        "vorname": member.vorname,
        "nachname": member.nachname,
        "nachname_geburt": member.nachname_geburt,
        "nachtitel": member.nachtitel,
        "couleurname": member.couleurname,
        "org_id": member.org_id,
        "state_id": member.state_id,
        "gruender": member.gruender or False,
        "entlassen": member.entlassen or False,
        "verstorben": member.verstorben or False,
        "parent_id": member.parent_id,
        "grabadresse": member.grabadresse,
        "geburtsdatum": member.geburtsdatum,
        "geburtsdatum_accuracy": member.geburtsdatum_accuracy or 0,
        "aufnahmedatum": member.aufnahmedatum,
        "aufnahmedatum_accuracy": member.aufnahmedatum_accuracy or 0,
        "branderdatum": member.branderdatum,
        "branderdatum_accuracy": member.branderdatum_accuracy or 0,
        "burschungsdatum": member.burschungsdatum,
        "burschungsdatum_accuracy": member.burschungsdatum_accuracy or 0,
        "philistrierungsdatum": member.philistrierungsdatum,
        "philistrierungsdatum_accuracy": member.philistrierungsdatum_accuracy or 0,
        "entlassungsdatum": member.entlassungsdatum,
        "entlassungsdatum_accuracy": member.entlassungsdatum_accuracy or 0,
        "sterbedatum": member.sterbedatum,
        "sterbedatum_accuracy": member.sterbedatum_accuracy or 0,
        "email": member.email,
        "url": member.url,
        "mkv_ogv_url": member.mkv_ogv_url,
        "rufnummer_mobil": member.rufnummer_mobil,
        "rufnummer_privat": member.rufnummer_privat,
        "rufnummer_beruf": member.rufnummer_beruf,
        "zustellungen": member.zustellungen or MemberDeliveryPreference.DEAKTIVIERT,
        "adresse_privat_anschrift": member.adresse_privat_anschrift,
        "adresse_privat_plz": member.adresse_privat_plz,
        "adresse_privat_ort": member.adresse_privat_ort,
        "adresse_privat_land": member.adresse_privat_land,
        "adresse_beruf_anschrift": member.adresse_beruf_anschrift,
        "adresse_beruf_plz": member.adresse_beruf_plz,
        "adresse_beruf_ort": member.adresse_beruf_ort,
        "adresse_beruf_land": member.adresse_beruf_land,
        "arbeitgeber": member.arbeitgeber,
        "taetigkeit": member.taetigkeit,
        "mitgliedschaften": member.mitgliedschaften,
        "verbandchargen": member.verbandchargen,
        "anmerkungen": member.anmerkungen,
        "chroniclemail": member.chroniclemail or False,
        "auth_locked": (member.auth_locked if member.auth_locked is not None else True),
        "roles_history": [
            {
                "id": mr.role_id,
                "label": mr.role.label if mr.role else None,
                "startdate": mr.startdate,
                "enddate": mr.enddate,
            }
            for mr in member.member_roles
        ],
        "badges": [
            {
                "id": mb.badge_id,
                "presentationdate": mb.presentationdate,
                "presentationdate_accuracy": mb.presentationdate_accuracy or 0,
            }
            for mb in member.member_badges
        ],
        "keys": [
            {
                "id": mk.key_id,
                "presentationdate": mk.presentationdate,
                "presentationdate_accuracy": mk.presentationdate_accuracy or 0,
            }
            for mk in member.member_keys
        ],
    }


def resolve_change_request(
    db: Session,
    request: MemberChangeRequest,
    field_decisions: dict[str, str],
    resolving_admin: Member,
) -> Member:
    """Atomically resolves a pending request: every proposed field must
    have a decision (enforced below), approved fields are applied to the
    live Member record via apply_member_input() - which also, as a
    side-effect that already existed there, resets email_verified_at when
    the approved change touches the email field, and produces the usual
    MembersLog changelog entries, attributed to the resolving admin.
    """
    if request.status != MemberChangeRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Antrag wurde bereits entschieden.",
        )
    if set(field_decisions) != set(request.proposed_data):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Es muss für jedes vorgeschlagene Feld eine Entscheidung vorliegen."
            ),
        )

    # proposed_data is Mapped[dict[str, object]] (JSONB has no way to
    # express the nested {field: {"old": ..., "new": ...}} shape at the
    # type level) - cast back to the shape submit_change_request() actually
    # writes.
    proposed_data = cast("dict[str, dict[str, object]]", request.proposed_data)

    member = request.member
    current_full = _build_current_full_request_dict(member)
    for field, decision in field_decisions.items():
        if decision == "approved":
            current_full[field] = proposed_data[field]["new"]

    # model_validate() (not MemberSaveRequest(**current_full)) deliberately:
    # current_full is dict[str, object] by nature (a heterogeneous mix of
    # str/bool/int/date/list-of-dicts sourced from Member's live columns
    # plus JSONB-decoded values), so every value arrives already erased to
    # `object` - there is no way to preserve each key's real type through a
    # dict[str, object] and still use **kwargs against a strictly-typed
    # constructor. model_validate() is Pydantic's own designated entry
    # point for exactly this case (validating a loosely-typed, dynamically
    # built payload), matching the pattern already used for
    # FeeMemberSelfResponse.model_validate(...) in app/schemas/p4x.py.
    merged = MemberSaveRequest.model_validate(current_full)
    validate_member_org(merged, resolving_admin)
    validate_member_uniqueness(db, merged, exclude_id=member.id)
    apply_member_input(db, member, merged, current_user=resolving_admin)

    request.status = MemberChangeRequestStatus.RESOLVED
    request.field_decisions = field_decisions
    request.resolved_by = resolving_admin.id
    request.resolved_at = datetime.now(UTC)
    db.add(request)
    db.commit()
    db.refresh(member)

    return member
