"""What the standesdb routes decide for member and contact entries: lookups,
the org-scoped permission checks, create and update including the change
notification for the admins, and the self-service change request flows.

The routers keep only the request parameters, the permission aliases, the
thread pool hand-over and the queue calls."""

from typing import TYPE_CHECKING, NamedTuple, cast

from fastapi import HTTPException, status

from app.models.contact import Contact
from app.models.enums import MemberDeliveryPreference
from app.models.member import Member
from app.schemas.standesdb import (
    ChangeRequestFieldDiff,
    MemberChangeRequestDetailResponse,
    MemberChangeRequestListResponse,
    MemberChangeRequestSummary,
    MemberSelfServiceDetailResponse,
    MyChangeRequestResponse,
)
from app.services import member_change_request_service, standesdb_service
from app.services.permission_service import (
    calculate_permissions,
    get_emails_with_permission,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from app.models.member_change_request import MemberChangeRequest
    from app.schemas.standesdb import (
        ContactSaveRequest,
        MemberChangeRequestDecisionRequest,
        MemberSaveRequest,
        MemberSelfServiceSaveRequest,
    )

_NO_ORG_DETAIL = "Mitglied hat keine Verbindung zugewiesen."


class EntryChangedNotification(NamedTuple):
    """Arguments of the "task_send_entry_changed_email" queue task: the admin
    addresses, the "member" or "contact" literal, the entry's cn, the field
    diff and the change_type/modifier_cn pair. The last two are keyword-only on
    the task itself, so the router passes the fields explicitly."""

    to_emails: list[str]
    entry_type: str
    entry_cn: str
    diff: dict[str, dict[str, object]]
    change_type: str
    modifier_cn: str


class ChangeRequestSubmittedNotification(NamedTuple):
    to_emails: list[str]
    member_cn: str
    diff: dict[str, dict[str, object]]


class ChangeRequestResolvedNotification(NamedTuple):
    to_email: str
    diff: dict[str, dict[str, object]]
    field_decisions: dict[str, str]


def _org_admin_permission(org_id: str) -> str:
    return f"standesdb{org_id.capitalize()}Admin"


# --- Lookups ---


def get_member_or_404(db: Session, member_id: uuid.UUID) -> Member:
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )
    return member


def get_contact_or_404(db: Session, contact_id: uuid.UUID) -> Contact:
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kontakt nicht gefunden.",
        )
    return contact


# --- Org-scoped permission checks ---


def require_org_admin(user: Member, org_id: str | None) -> None:
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Keine Verbindung zugewiesen.",
        )
    org_perm = _org_admin_permission(org_id)
    if org_perm not in calculate_permissions(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Fehlende Berechtigung: {org_perm}",
        )


def require_any_org_admin(user: Member) -> None:
    perms = calculate_permissions(user)
    if not any(_org_admin_permission(org_id) in perms for org_id in ("vbw", "vbn")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Berechtigung.",
        )


def get_member_for_org_admin(db: Session, member_id: uuid.UUID, user: Member) -> Member:
    """The member, once the user is known to administer the member's org."""
    member = get_member_or_404(db, member_id)
    require_org_admin(user, member.org_id)
    return member


def get_assigned_member_for_org_admin(
    db: Session, member_id: uuid.UUID, user: Member
) -> Member:
    """Like get_member_for_org_admin, but a member without an org is refused
    with the more specific message."""
    member = get_member_or_404(db, member_id)
    if not member.org_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_NO_ORG_DETAIL,
        )
    require_org_admin(user, member.org_id)
    return member


# --- Members ---


def create_member(
    db: Session, data: MemberSaveRequest, current_user: Member
) -> tuple[Member, EntryChangedNotification]:
    require_org_admin(current_user, data.org_id)
    standesdb_service.validate_member_org(data, current_user)
    standesdb_service.validate_member_uniqueness(db, data)
    standesdb_service.validate_parent_id(db, data.parent_id, data.org_id)
    standesdb_service.validate_member_references(db, data)

    # A new record always differs from the empty one, so the diff is never empty.
    member = Member()
    diff = standesdb_service.apply_member_input(db, member, data, current_user)

    recipients = get_emails_with_permission(db, _org_admin_permission(data.org_id))
    notification = EntryChangedNotification(
        recipients, "member", member.cn, diff, "store", current_user.cn
    )
    return member, notification


def update_member(
    db: Session,
    member_id: uuid.UUID,
    data: MemberSaveRequest,
    current_user: Member,
) -> tuple[Member, EntryChangedNotification | None]:
    member = get_assigned_member_for_org_admin(db, member_id, current_user)
    # The stored member's org (not the payload's) decides who may edit, and
    # the lookup above has just refused a member without one.
    org_id = cast("str", member.org_id)
    standesdb_service.validate_member_org(data, current_user)
    standesdb_service.validate_member_uniqueness(db, data, exclude_id=member_id)
    standesdb_service.validate_parent_id(db, data.parent_id, data.org_id, member_id)
    standesdb_service.validate_member_references(db, data)

    diff = standesdb_service.apply_member_input(db, member, data, current_user)

    if not diff:
        return member, None

    recipients = get_emails_with_permission(db, _org_admin_permission(org_id))
    notification = EntryChangedNotification(
        recipients, "member", member.cn, diff, "update", current_user.cn
    )
    return member, notification


# --- Contacts ---


def create_contact(
    db: Session, data: ContactSaveRequest, current_user: Member
) -> tuple[Contact, EntryChangedNotification]:
    standesdb_service.validate_contact_uniqueness(db, data.name)

    # A new record always differs from the empty one, so the diff is never empty.
    contact = Contact()
    diff = standesdb_service.apply_contact_input(
        db, contact, data.model_dump(), current_user
    )

    recipients = get_emails_with_permission(db, "standesdbContactAdmin")
    notification = EntryChangedNotification(
        recipients, "contact", contact.cn, diff, "store", current_user.cn
    )
    return contact, notification


def update_contact(
    db: Session,
    contact_id: uuid.UUID,
    data: ContactSaveRequest,
    current_user: Member,
) -> tuple[Contact, EntryChangedNotification | None]:
    contact = get_contact_or_404(db, contact_id)

    standesdb_service.validate_contact_uniqueness(db, data.name, exclude_id=contact_id)

    diff = standesdb_service.apply_contact_input(
        db, contact, data.model_dump(), current_user
    )

    if not diff:
        return contact, None

    recipients = get_emails_with_permission(db, "standesdbContactAdmin")
    notification = EntryChangedNotification(
        recipients, "contact", contact.cn, diff, "update", current_user.cn
    )
    return contact, notification


def delete_contact(db: Session, contact_id: uuid.UUID, current_user: Member) -> None:
    contact = get_contact_or_404(db, contact_id)
    standesdb_service.soft_delete_contact(db, contact, current_user)


# --- Self-service Stammdaten ---


def build_own_stammdaten_response(
    member: Member,
) -> MemberSelfServiceDetailResponse:
    return MemberSelfServiceDetailResponse(
        id=member.id,
        cn=member.cn,
        vortitel=member.vortitel,
        vorname=member.vorname,
        nachname=member.nachname,
        nachname_geburt=member.nachname_geburt,
        nachtitel=member.nachtitel,
        couleurname=member.couleurname,
        email=member.email,
        url=member.url,
        mkv_ogv_url=member.mkv_ogv_url,
        rufnummer_mobil=member.rufnummer_mobil,
        rufnummer_privat=member.rufnummer_privat,
        rufnummer_beruf=member.rufnummer_beruf,
        zustellungen=member.zustellungen or MemberDeliveryPreference.DEAKTIVIERT,
        adresse_privat_anschrift=member.adresse_privat_anschrift,
        adresse_privat_plz=member.adresse_privat_plz,
        adresse_privat_ort=member.adresse_privat_ort,
        adresse_privat_land=member.adresse_privat_land,
        adresse_beruf_anschrift=member.adresse_beruf_anschrift,
        adresse_beruf_plz=member.adresse_beruf_plz,
        adresse_beruf_ort=member.adresse_beruf_ort,
        adresse_beruf_land=member.adresse_beruf_land,
        arbeitgeber=member.arbeitgeber,
        taetigkeit=member.taetigkeit,
        mitgliedschaften=member.mitgliedschaften,
        verbandchargen=member.verbandchargen,
    )


def _proposed_data(request: MemberChangeRequest) -> dict[str, dict[str, object]]:
    return cast("dict[str, dict[str, object]]", request.proposed_data)


def get_own_change_request_response(
    db: Session, current_user: Member
) -> MyChangeRequestResponse:
    """The member's own pending change request; 404 if there is none, so the
    frontend falls back to the live Stammdaten."""
    request = member_change_request_service.get_own_pending_request(db, current_user)
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kein offener Änderungsantrag.",
        )
    proposed_fields = {
        field: values["new"] for field, values in _proposed_data(request).items()
    }
    return MyChangeRequestResponse(
        id=request.id,
        created_at=request.created_at,
        proposed_fields=proposed_fields,
    )


def submit_own_change_request(
    db: Session, data: MemberSelfServiceSaveRequest, current_user: Member
) -> ChangeRequestSubmittedNotification | None:
    if not current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Deinem Konto ist keine Verbindung zugewiesen — "
                "Änderungsanträge können nicht eingereicht werden."
            ),
        )

    request = member_change_request_service.submit_change_request(
        db, current_user, data
    )

    if request is None:
        return None

    recipients = get_emails_with_permission(
        db, _org_admin_permission(current_user.org_id)
    )
    return ChangeRequestSubmittedNotification(
        recipients, current_user.cn, _proposed_data(request)
    )


# --- Member change requests (admin review) ---


def list_change_requests(
    db: Session, current_user: Member
) -> MemberChangeRequestListResponse:
    """The pending requests for the orgs the caller administers. A caller who
    administers no org is refused; an admin without requests gets an empty
    list."""
    require_any_org_admin(current_user)
    requests = member_change_request_service.get_pending_requests_for_admin(
        db, current_user
    )
    items = [
        MemberChangeRequestSummary(
            id=r.id,
            member_id=r.member_id,
            member_cn=r.member.cn,
            member_org_id=r.member.org_id,
            field_count=len(r.proposed_data),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in requests
    ]
    return MemberChangeRequestListResponse(items=items, total=len(items))


def get_change_request_detail(
    db: Session, request_id: uuid.UUID, current_user: Member
) -> MemberChangeRequestDetailResponse:
    request = member_change_request_service.get_change_request_or_404(db, request_id)
    require_org_admin(current_user, request.member.org_id)

    diff = [
        ChangeRequestFieldDiff(
            field=field,
            old=str(values["old"]) if values["old"] is not None else None,
            new=str(values["new"]) if values["new"] is not None else None,
        )
        for field, values in _proposed_data(request).items()
    ]
    return MemberChangeRequestDetailResponse(
        id=request.id,
        member_id=request.member_id,
        member_cn=request.member.cn,
        status=request.status,
        created_at=request.created_at,
        updated_at=request.updated_at,
        resolved_at=request.resolved_at,
        resolved_by_name=(request.resolver.cn if request.resolver else None),
        diff=diff,
        field_decisions=request.field_decisions,
    )


def decide_change_request(
    db: Session,
    request_id: uuid.UUID,
    data: MemberChangeRequestDecisionRequest,
    current_user: Member,
) -> ChangeRequestResolvedNotification | None:
    request = member_change_request_service.get_change_request_or_404(
        db, request_id, for_update=True
    )
    require_org_admin(current_user, request.member.org_id)

    diff_snapshot = cast("dict[str, dict[str, object]]", dict(request.proposed_data))
    decisions_snapshot: dict[str, str] = dict(data.field_decisions)
    member = member_change_request_service.resolve_change_request(
        db,
        request,
        decisions_snapshot,
        data.expected_updated_at,
        current_user,
    )

    if not member.email:
        return None
    return ChangeRequestResolvedNotification(
        member.email, diff_snapshot, decisions_snapshot
    )


# --- Own image changes ---


def get_own_image_changed_recipients(db: Session, member: Member) -> list[str] | None:
    """The admins of the member's org to inform about a self-service image
    change, or None when the member has no org (nobody to notify)."""
    if not member.org_id:
        return None
    return get_emails_with_permission(db, _org_admin_permission(member.org_id))
