import uuid
from typing import Annotated, cast

from arq.connections import ArqRedis
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.auth_guards import require_permission
from app.api.deps import get_current_user
from app.core.arq_pool import get_arq_pool
from app.core.datetime_utils import local_today
from app.core.storage import StorageClient, get_storage
from app.db.database import get_db
from app.models.contact import Contact
from app.models.enums import MemberDeliveryPreference
from app.models.member import Member
from app.schemas.archive import PresignedUrlResponse
from app.schemas.base import PaginatedResponse, StatusIdResponse, StatusResponse
from app.schemas.standesdb import (
    ChangeLogEntry,
    ChangeRequestFieldDiff,
    ContactDetailResponse,
    ContactSaveRequest,
    ContactStatsResponse,
    ExportConfigResponse,
    ExportRequest,
    ImageListResponse,
    ImageUpdateRequest,
    KeysListResponse,
    MemberAuthActivityResponse,
    MemberChangeRequestDecisionRequest,
    MemberChangeRequestDetailResponse,
    MemberChangeRequestListResponse,
    MemberChangeRequestSummary,
    MemberDetailResponse,
    MemberDismissedResponse,
    MemberSaveRequest,
    MemberSelfServiceDetailResponse,
    MemberSelfServiceSaveRequest,
    MemberStatsResponse,
    MyChangeRequestResponse,
    ParentSearchResponse,
    ReferenceDataResponse,
    RolesListResponse,
    SearchResponse,
    StatsResponse,
)
from app.services import (
    export_service,
    image_service,
    member_change_request_service,
    standesdb_service,
)
from app.services.permission_service import (
    calculate_permissions,
    get_emails_with_permission,
)

standesdb_router = APIRouter()

# Fields captured for the "task_send_entry_changed_email" ARQ task: the
# admin email list, the "member" or "contact" literal, the entry's cn,
# the field diff, and the change_type/modifier_cn pair — shared by every
# endpoint that notifies admins of a member/contact create-or-update.
# The last two are keyword-only on the task itself (matching
# send_entry_changed_email's own signature), so callers destructure this
# tuple explicitly rather than *-unpacking it straight into
# enqueue_job() — see _enqueue_entry_changed_email() below.
_EntryChangedNotification = tuple[
    list[str], str, str, dict[str, dict[str, object]], str, str
]


async def _enqueue_entry_changed_email(
    arq_pool: ArqRedis, notification: _EntryChangedNotification
) -> None:
    to_emails, entry_type, entry_cn, diff, change_type, modifier_cn = notification
    await arq_pool.enqueue_job(
        "task_send_entry_changed_email",
        to_emails,
        entry_type,
        entry_cn,
        diff,
        change_type=change_type,
        modifier_cn=modifier_cn,
    )


@standesdb_router.get("/stats")
def get_stats(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> StatsResponse:
    """Return aggregate statistics (member/contact counts by org and state). No special
    permission - any authenticated member."""
    return StatsResponse(
        member=MemberStatsResponse(**standesdb_service.get_member_stats(db)),
        contact=ContactStatsResponse(**standesdb_service.get_contact_stats(db)),
    )


@standesdb_router.get("/search")
def search(
    q: Annotated[str, Query(min_length=3)],
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> SearchResponse:
    """Full-text search across members and contacts by name/couleurname, minimum 3
    characters. No special permission - any authenticated member."""
    return SearchResponse.model_validate(
        {"data": standesdb_service.search_members_and_contacts(db, q)}
    )


@standesdb_router.get("/reference-data")
def get_reference_data(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> ReferenceDataResponse:
    """Return reference data (orgs, states, roles, badges) for form dropdowns. No
    special permission - any authenticated member."""
    data = standesdb_service.get_reference_data(db)
    return ReferenceDataResponse.model_validate(data, from_attributes=True)


# --- Export ---


@standesdb_router.get("/export/config")
def get_export_config(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(require_permission("standesdbExport"))],
) -> ExportConfigResponse:
    """Return export configuration options (available formats/fields) for the export
    module. Requires standesdbExport."""
    return ExportConfigResponse.model_validate(export_service.get_export_config(db))


@standesdb_router.post("/export")
def do_export(
    data: ExportRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(require_permission("standesdbExport"))],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> Response:
    """Generate and download an export file - one of 4 modules (mailing-liste/excel-
    liste-komplett/mitgliederverzeichnis/adress-etiketten-zweckform-3490),
    each returning a different file format. 422 for an unrecognized module.
    Requires standesdbExport."""
    module = data.module
    filter_data: dict[str, object] = {
        **data.selections,
        **data.model_dump(exclude={"selections", "module"}),
    }

    members = export_service.filter_members(db, filter_data)
    contacts = export_service.filter_contacts(db, filter_data)

    today = local_today().isoformat()

    if module == "mailing-liste":
        content = export_service.generate_mailing_list(members, contacts)
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=mailing-liste_{today}.txt"
                )
            },
        )

    if module == "excel-liste-komplett":
        content = export_service.generate_excel_full(db, members, contacts)
        return Response(
            content=content,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": (
                    f"attachment; filename=excel-liste-komplett_{today}.xlsx"
                )
            },
        )

    if module == "mitgliederverzeichnis":
        content = export_service.generate_booklet(
            db,
            members,
            contacts,
            current_user,
            storage,
        )
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=mitgliederverzeichnis_{today}.pdf"
                )
            },
        )

    if module == "adress-etiketten-zweckform-3490":
        content = export_service.generate_labels(db, members, contacts)
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=adress-etiketten-zweckform-3490_{today}.pdf"
                )
            },
        )

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"Unbekanntes Modul: {module}",
    )


# --- Keys List ---


@standesdb_router.get("/keys")
def get_keys_list(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(require_permission("keylist"))],
) -> KeysListResponse:
    """Return the list of key holders with their assigned keys. Requires keylist."""
    return KeysListResponse.model_validate(standesdb_service.get_keys_list(db))


@standesdb_router.get("/keys/download")
def download_keys_list(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(require_permission("keylist"))],
) -> Response:
    """Download the key holders list as a plain-text file. Requires keylist."""
    today = local_today().isoformat()
    content = standesdb_service.generate_keys_download(db)
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": (f"attachment; filename=schluessel_{today}.txt")
        },
    )


# --- Roles List ---


@standesdb_router.get("/roles")
def get_roles_list(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
    year: Annotated[int | None, Query(ge=1928, lt=2100)] = None,
    semester: Annotated[str | None, Query(pattern="^(ss|ws)$")] = None,
) -> RolesListResponse:
    """Return members grouped by their current active roles, or by role membership
    during a specific year+semester if both are given (422 if only one of the two is
    given). No special permission - any authenticated member."""
    if (year is None) != (semester is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "year und semester müssen beide angegeben werden oder beide fehlen."
            ),
        )
    return RolesListResponse.model_validate(
        standesdb_service.get_roles_list(db, year, semester)
    )


# --- Members ---


@standesdb_router.get("/members/{member_id}")
def get_member(
    member_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> MemberDetailResponse | MemberDismissedResponse:
    """Retrieve a single member by ID with all related data (roles, badges, keys,
    contact info). No special permission - any authenticated member; write operations
    enforce org-scoped admin permission separately."""
    return standesdb_service.get_member_detail(db, member_id)


@standesdb_router.get("/members/{member_id}/auth-activity")
def get_member_auth_activity(
    member_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> MemberAuthActivityResponse:
    """Return recent authentication activity for a member.

    Requires standesdb write access for the member's own org (same check
    as editing the member — see _require_standesdb_admin()), not a
    blanket systemAdmin permission.
    """
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )
    _require_standesdb_admin(current_user, member.org_id)
    return MemberAuthActivityResponse(
        auth_lastlogin=member.auth_lastlogin,
        auth_lastsignal=member.auth_lastsignal,
        auth_lastlogout=member.auth_lastlogout,
    )


def _create_member_sync(
    db: Session, data: MemberSaveRequest, current_user: Member
) -> tuple[Member, _EntryChangedNotification | None]:
    _require_standesdb_admin(current_user, data.org_id)
    standesdb_service.validate_member_org(data, current_user)
    standesdb_service.validate_member_uniqueness(db, data)
    standesdb_service.validate_parent_id(db, data.parent_id, data.org_id)
    standesdb_service.validate_member_references(db, data)

    member = Member()
    diff = standesdb_service.apply_member_input(db, member, data, current_user)

    if not diff:
        return member, None

    perm = f"standesdb{data.org_id.capitalize()}Admin"
    recipients = get_emails_with_permission(db, perm)
    return member, (recipients, "member", member.cn, diff, "store", current_user.cn)


@standesdb_router.post("/members", status_code=status.HTTP_201_CREATED)
async def create_member(
    data: MemberSaveRequest,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> StatusIdResponse:
    """Create a new member record. Requires the standesdb admin permission for the
    target org (data.org_id) - checked at runtime, not via a route dependency."""
    member, notification = await run_in_threadpool(
        _create_member_sync, db, data, current_user
    )
    if notification:
        await _enqueue_entry_changed_email(arq_pool, notification)
    return StatusIdResponse(status="ok", id=member.id)


def _update_member_sync(
    db: Session, member_id: uuid.UUID, data: MemberSaveRequest, current_user: Member
) -> tuple[Member, _EntryChangedNotification | None]:
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )

    if not member.org_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Mitglied hat keine Verbindung zugewiesen.",
        )
    _require_standesdb_admin(current_user, member.org_id)
    standesdb_service.validate_member_org(data, current_user)
    standesdb_service.validate_member_uniqueness(db, data, exclude_id=member_id)
    standesdb_service.validate_parent_id(db, data.parent_id, data.org_id, member_id)
    standesdb_service.validate_member_references(db, data)

    diff = standesdb_service.apply_member_input(db, member, data, current_user)

    if not diff:
        return member, None

    perm = f"standesdb{member.org_id.capitalize()}Admin"
    recipients = get_emails_with_permission(db, perm)
    return member, (recipients, "member", member.cn, diff, "update", current_user.cn)


@standesdb_router.put("/members/{member_id}")
async def update_member(
    member_id: uuid.UUID,
    data: MemberSaveRequest,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> StatusIdResponse:
    """Update an existing member's data and notify the org's standesdb admins by email
    if anything actually changed. Requires the standesdb admin permission for the
    member's own org - checked at runtime, not via a route dependency."""
    member, notification = await run_in_threadpool(
        _update_member_sync, db, member_id, data, current_user
    )
    if notification:
        await _enqueue_entry_changed_email(arq_pool, notification)
    return StatusIdResponse(status="ok", id=member.id)


@standesdb_router.get("/members/{member_id}/searchparent")
def search_parent(
    member_id: uuid.UUID,
    q: Annotated[str, Query(min_length=3)],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> ParentSearchResponse:
    """Search for potential parent members (e.g. for the sponsoring 'Bürge'
    relationship) by name, minimum 3 characters. Requires the standesdb admin
    permission for the member's own org."""
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )
    if not member.org_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Mitglied hat keine Verbindung zugewiesen.",
        )
    _require_standesdb_admin(current_user, member.org_id)

    return ParentSearchResponse.model_validate(
        {"data": standesdb_service.search_parent(db, member_id, q)}
    )


# --- Self-service Stammdaten (own account, no admin permission required) ---


def _own_self_service_response(
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


@standesdb_router.get("/members/me/stammdaten")
def get_own_stammdaten(
    current_user: Annotated[Member, Depends(get_current_user)],
) -> MemberSelfServiceDetailResponse:
    """Return the authenticated member's own live Stammdaten - used to
    pre-fill the self-service form when there's no pending change request
    (see GET /members/me/change-request for that case)."""
    return _own_self_service_response(current_user)


@standesdb_router.get("/members/me/change-request")
def get_own_change_request(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> MyChangeRequestResponse:
    """Return the authenticated member's own pending change request, if
    any - 404 if none, so the frontend falls back to live Stammdaten."""
    request = member_change_request_service.get_own_pending_request(db, current_user)
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kein offener Änderungsantrag.",
        )
    proposed_data = cast("dict[str, dict[str, object]]", request.proposed_data)
    proposed_fields = {field: values["new"] for field, values in proposed_data.items()}
    return MyChangeRequestResponse(
        id=request.id,
        created_at=request.created_at,
        proposed_fields=proposed_fields,
    )


def _submit_own_change_request_sync(
    db: Session, data: MemberSelfServiceSaveRequest, current_user: Member
) -> tuple[list[str], str, dict[str, dict[str, object]]] | None:
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

    perm = f"standesdb{current_user.org_id.capitalize()}Admin"
    recipients = get_emails_with_permission(db, perm)
    return (
        recipients,
        current_user.cn,
        cast("dict[str, dict[str, object]]", request.proposed_data),
    )


@standesdb_router.post("/members/me/change-request")
async def submit_own_change_request(
    data: MemberSelfServiceSaveRequest,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> StatusResponse:
    """Submit (or update, if one is already pending) a self-service
    Stammdaten change request. No admin permission required - every member
    may propose changes to their own account.
    """
    notification = await run_in_threadpool(
        _submit_own_change_request_sync, db, data, current_user
    )
    if notification is None:
        return StatusResponse(status="no_changes")

    await arq_pool.enqueue_job(
        "task_send_member_change_request_submitted_email", *notification
    )
    return StatusResponse(status="submitted")


# --- Contacts ---


@standesdb_router.get("/contacts/{contact_id}")
def get_contact(
    contact_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> ContactDetailResponse:
    """Retrieve a single contact by ID with all related data. No special permission -
    any authenticated member."""
    return standesdb_service.get_contact_detail(db, contact_id)


def _create_contact_sync(
    db: Session, data: ContactSaveRequest, current_user: Member
) -> tuple[Contact, _EntryChangedNotification | None]:
    standesdb_service.validate_contact_uniqueness(db, data.name)

    contact = Contact()
    input_dict = data.model_dump()
    diff = standesdb_service.apply_contact_input(db, contact, input_dict, current_user)

    if not diff:
        return contact, None

    recipients = get_emails_with_permission(db, "standesdbContactAdmin")
    return contact, (recipients, "contact", contact.cn, diff, "store", current_user.cn)


@standesdb_router.post("/contacts", status_code=status.HTTP_201_CREATED)
async def create_contact(
    data: ContactSaveRequest,
    # Permission check first: FastAPI resolves dependencies in parameter
    # order, so an unprivileged caller gets rejected before the (real,
    # connection-acquiring) arq pool dependency ever runs.
    current_user: Annotated[
        Member, Depends(require_permission("standesdbContactAdmin"))
    ],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
) -> StatusIdResponse:
    """Create a new contact record. Requires standesdbContactAdmin."""
    contact, notification = await run_in_threadpool(
        _create_contact_sync, db, data, current_user
    )
    if notification:
        await _enqueue_entry_changed_email(arq_pool, notification)
    return StatusIdResponse(status="ok", id=contact.id)


def _update_contact_sync(
    db: Session, contact_id: uuid.UUID, data: ContactSaveRequest, current_user: Member
) -> tuple[Contact, _EntryChangedNotification | None]:
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kontakt nicht gefunden.",
        )

    standesdb_service.validate_contact_uniqueness(db, data.name, exclude_id=contact_id)

    input_dict = data.model_dump()
    diff = standesdb_service.apply_contact_input(db, contact, input_dict, current_user)

    if not diff:
        return contact, None

    recipients = get_emails_with_permission(db, "standesdbContactAdmin")
    return contact, (recipients, "contact", contact.cn, diff, "update", current_user.cn)


@standesdb_router.put("/contacts/{contact_id}")
async def update_contact(
    contact_id: uuid.UUID,
    data: ContactSaveRequest,
    current_user: Annotated[
        Member, Depends(require_permission("standesdbContactAdmin"))
    ],
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
) -> StatusIdResponse:
    """Update an existing contact's data and notify subscribers by email if anything
    actually changed. Requires standesdbContactAdmin."""
    contact, notification = await run_in_threadpool(
        _update_contact_sync, db, contact_id, data, current_user
    )
    if notification:
        await _enqueue_entry_changed_email(arq_pool, notification)
    return StatusIdResponse(status="ok", id=contact.id)


@standesdb_router.delete(
    "/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_contact(
    contact_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        Member, Depends(require_permission("standesdbContactAdmin"))
    ],
) -> None:
    """Soft-delete a contact record - sets deleted_at, does not remove
    the row. Requires standesdbContactAdmin."""
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kontakt nicht gefunden.",
        )
    standesdb_service.soft_delete_contact(db, contact, current_user)


# --- Member Images: Self-service (own images, no admin permission required) ---
#
# NOTE: these four "me" routes must stay registered before their
# "/members/{member_id}/images..." counterparts below. member_id has no
# explicit int path converter, so Starlette compiles a generic
# single-segment pattern for it; if {member_id} were registered first, a
# GET/POST/PUT/DELETE to "/members/me/images..." would match THAT route
# instead and fail int-conversion with a 422 instead of falling through
# to these routes. Same technique already used by /fee-members/me in
# p4x.py.


@standesdb_router.get("/members/me/images")
def list_own_member_images(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> ImageListResponse:
    """List the authenticated member's own profile images, plus which one
    (if any) is the default. No admin permission required - identical data
    to GET /members/{member_id}/images (already open to any authenticated
    caller), just without needing to know/pass your own id."""
    images = image_service.get_images_for_owner(db, "member", current_user.id)
    return ImageListResponse.model_validate(
        {
            "owner": {
                "type": "member",
                "id": current_user.id,
                "cn": current_user.cn,
                "org_id": current_user.org_id,
                "default_image": current_user.default_image,
            },
            "images": [
                {
                    "id": i.id,
                    "type": i.type,
                    "height": i.height,
                    "width": i.width,
                    "size": i.size,
                    "description": i.description,
                    "default": i.default,
                }
                for i in images
            ],
        }
    )


@standesdb_router.post("/members/me/images", status_code=status.HTTP_201_CREATED)
async def upload_own_member_image(
    file: Annotated[UploadFile, File()],
    *,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
    description: Annotated[str | None, Form()] = None,
) -> StatusIdResponse:
    """Upload a new profile image for the authenticated member's own
    account. No admin permission required - every member may manage their
    own profile images; org admins are notified by email afterward,
    purely informational (no approval gate)."""
    img = await run_in_threadpool(
        image_service.upload_image,
        db,
        "member",
        current_user.id,
        file,
        description=description,
        created_by=current_user.id,
        storage=storage,
    )
    await _notify_own_image_changed(db, arq_pool, current_user, "upload")
    return StatusIdResponse(status="ok", id=img.id)


@standesdb_router.put("/members/me/images/{image_id}")
async def update_own_member_image(
    image_id: uuid.UUID,
    data: ImageUpdateRequest,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> StatusResponse:
    """Update the description or default-flag of one of the authenticated
    member's own profile images. No admin permission required - every
    member may manage their own profile images; org admins are notified
    by email afterward, purely informational (no approval gate)."""

    def _update_sync() -> None:
        img = image_service.get_image_record(db, "member", current_user.id, image_id)
        image_service.update_image(db, img, data.description, data.default)

    await run_in_threadpool(_update_sync)
    await _notify_own_image_changed(db, arq_pool, current_user, "update")
    return StatusResponse(status="ok")


@standesdb_router.delete(
    "/members/me/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_own_member_image(
    image_id: uuid.UUID,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> None:
    """Delete one of the authenticated member's own profile images from
    storage. No admin permission required - every member may manage their
    own profile images; org admins are notified by email afterward,
    purely informational (no approval gate)."""

    def _delete_sync() -> None:
        img = image_service.get_image_record(db, "member", current_user.id, image_id)
        image_service.delete_image(db, img)

    await run_in_threadpool(_delete_sync)
    await _notify_own_image_changed(db, arq_pool, current_user, "delete")


# --- Member Images: Admin / shared reads ---


@standesdb_router.get("/members/{member_id}/images")
def list_member_images(
    member_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> ImageListResponse:
    """List all profile images for a member, plus which one (if any) is the default. No
    special permission - any authenticated member."""
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )
    images = image_service.get_images_for_owner(db, "member", member_id)
    return ImageListResponse.model_validate(
        {
            "owner": {
                "type": "member",
                "id": member.id,
                "cn": member.cn,
                "org_id": member.org_id,
                "default_image": member.default_image,
            },
            "images": [
                {
                    "id": i.id,
                    "type": i.type,
                    "height": i.height,
                    "width": i.width,
                    "size": i.size,
                    "description": i.description,
                    "default": i.default,
                }
                for i in images
            ],
        }
    )


@standesdb_router.get("/members/{member_id}/images/{image_id}/download")
def download_member_image(
    member_id: uuid.UUID,
    image_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> Response:
    """Download a member's profile image file (original or thumbnail). No special
    permission - any authenticated member."""
    img = image_service.get_image_for_serving(db, "member", member_id, image_id)
    return image_service.serve_download(img, storage)


@standesdb_router.get("/members/{member_id}/images/{image_id}/url")
def member_image_url(
    member_id: uuid.UUID,
    image_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
    thumb: Annotated[bool, Query()] = False,  # noqa: FBT002
) -> PresignedUrlResponse:
    """Generate a time-limited presigned S3 URL for a member's profile image (original
    or thumbnail via thumb=true). No special permission - any authenticated member."""
    img = image_service.get_image_for_serving(db, "member", member_id, image_id)
    url = image_service.get_presigned_url(
        img,
        storage,
        thumb=thumb,
    )
    return PresignedUrlResponse(url=url)


@standesdb_router.post(
    "/members/{member_id}/images", status_code=status.HTTP_201_CREATED
)
def upload_member_image(
    member_id: uuid.UUID,
    *,
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
    description: Annotated[str | None, Form()] = None,
) -> StatusIdResponse:
    """Upload a new profile image for a member. Requires the standesdb admin permission
    for the member's own org."""
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )
    _require_standesdb_admin(current_user, member.org_id)
    img = image_service.upload_image(
        db,
        "member",
        member_id,
        file,
        description=description,
        created_by=current_user.id,
        storage=storage,
    )
    return StatusIdResponse(status="ok", id=img.id)


@standesdb_router.put("/members/{member_id}/images/{image_id}")
def update_member_image(
    member_id: uuid.UUID,
    image_id: uuid.UUID,
    data: ImageUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> StatusResponse:
    """Update a member image's description or set it as the default. Requires the
    standesdb admin permission for the member's own org."""
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )
    _require_standesdb_admin(current_user, member.org_id)
    img = image_service.get_image_record(db, "member", member_id, image_id)
    image_service.update_image(db, img, data.description, data.default)
    return StatusResponse(status="ok")


@standesdb_router.delete(
    "/members/{member_id}/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_member_image(
    member_id: uuid.UUID,
    image_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> None:
    """Delete a member's profile image from storage. Requires the standesdb admin
    permission for the member's own org."""
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )
    _require_standesdb_admin(current_user, member.org_id)
    img = image_service.get_image_record(db, "member", member_id, image_id)
    image_service.delete_image(db, img)


# --- Contact Images ---


@standesdb_router.get("/contacts/{contact_id}/images")
def list_contact_images(
    contact_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> ImageListResponse:
    """List all profile images for a contact, plus which one (if any) is the default. No
    special permission - any authenticated member."""
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kontakt nicht gefunden.",
        )
    images = image_service.get_images_for_owner(db, "contact", contact_id)
    return ImageListResponse.model_validate(
        {
            "owner": {
                "type": "contact",
                "id": contact.id,
                "cn": contact.cn,
                "org_id": contact.org_id,
                "default_image": contact.default_image,
            },
            "images": [
                {
                    "id": i.id,
                    "type": i.type,
                    "height": i.height,
                    "width": i.width,
                    "size": i.size,
                    "description": i.description,
                    "default": i.default,
                }
                for i in images
            ],
        }
    )


@standesdb_router.get("/contacts/{contact_id}/images/{image_id}/download")
def download_contact_image(
    contact_id: uuid.UUID,
    image_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> Response:
    """Download a contact's profile image file (original or thumbnail). No special
    permission - any authenticated member."""
    img = image_service.get_image_for_serving(db, "contact", contact_id, image_id)
    return image_service.serve_download(img, storage)


@standesdb_router.get("/contacts/{contact_id}/images/{image_id}/url")
def contact_image_url(
    contact_id: uuid.UUID,
    image_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
    thumb: Annotated[bool, Query()] = False,  # noqa: FBT002
) -> PresignedUrlResponse:
    """Generate a time-limited presigned S3 URL for a contact's profile image (original
    or thumbnail via thumb=true). No special permission - any authenticated member."""
    img = image_service.get_image_for_serving(db, "contact", contact_id, image_id)
    url = image_service.get_presigned_url(
        img,
        storage,
        thumb=thumb,
    )
    return PresignedUrlResponse(url=url)


@standesdb_router.post(
    "/contacts/{contact_id}/images", status_code=status.HTTP_201_CREATED
)
def upload_contact_image(
    contact_id: uuid.UUID,
    *,
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        Member, Depends(require_permission("standesdbContactAdmin"))
    ],
    storage: Annotated[StorageClient, Depends(get_storage)],
    description: Annotated[str | None, Form()] = None,
) -> StatusIdResponse:
    """Upload a new profile image for a contact. Requires standesdbContactAdmin."""
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kontakt nicht gefunden.",
        )
    img = image_service.upload_image(
        db,
        "contact",
        contact_id,
        file,
        description=description,
        created_by=current_user.id,
        storage=storage,
    )
    return StatusIdResponse(status="ok", id=img.id)


@standesdb_router.put("/contacts/{contact_id}/images/{image_id}")
def update_contact_image(
    contact_id: uuid.UUID,
    image_id: uuid.UUID,
    data: ImageUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[
        Member, Depends(require_permission("standesdbContactAdmin"))
    ],
) -> StatusResponse:
    """Update a contact image's description or set it as the default.
    Requires standesdbContactAdmin."""
    img = image_service.get_image_record(db, "contact", contact_id, image_id)
    image_service.update_image(db, img, data.description, data.default)
    return StatusResponse(status="ok")


@standesdb_router.delete(
    "/contacts/{contact_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_contact_image(
    contact_id: uuid.UUID,
    image_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[
        Member, Depends(require_permission("standesdbContactAdmin"))
    ],
) -> None:
    """Delete a contact's profile image from storage. Requires standesdbContactAdmin."""
    img = image_service.get_image_record(db, "contact", contact_id, image_id)
    image_service.delete_image(db, img)


# --- Changelog ---


@standesdb_router.get("/members/{member_id}/changelog")
def list_member_changelog(
    member_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PaginatedResponse[ChangeLogEntry]:
    """Return the change history for a member, paginated.

    Requires the standesdb admin permission for the member's own org.
    """
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )
    _require_standesdb_admin(current_user, member.org_id)
    return PaginatedResponse[ChangeLogEntry].model_validate(
        standesdb_service.get_member_changelog(db, member_id, page, page_size)
    )


@standesdb_router.get("/contacts/{contact_id}/changelog")
def list_contact_changelog(
    contact_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("standesdbContactAdmin"))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PaginatedResponse[ChangeLogEntry]:
    """Return the change history for a contact, paginated.
    Requires standesdbContactAdmin."""
    return PaginatedResponse[ChangeLogEntry].model_validate(
        standesdb_service.get_contact_changelog(db, contact_id, page, page_size)
    )


# --- Member Change Requests (admin review) ---


@standesdb_router.get("/member-change-requests")
def list_member_change_requests(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> MemberChangeRequestListResponse:
    """List pending self-service change requests for the orgs the caller
    administers. Empty for a non-admin, not an error - matches the
    org-scoped filtering already applied inside the service query."""
    _require_any_standesdb_admin(current_user)
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


@standesdb_router.get("/member-change-requests/{request_id}")
def get_member_change_request(
    request_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> MemberChangeRequestDetailResponse:
    """Return one change request with its full field-level diff. Org-scoped
    admin permission check, same function as the member-edit endpoints."""
    request = member_change_request_service.get_change_request_or_404(db, request_id)
    _require_standesdb_admin(current_user, request.member.org_id)

    proposed_data = cast("dict[str, dict[str, object]]", request.proposed_data)
    diff = [
        ChangeRequestFieldDiff(
            field=field,
            old=str(values["old"]) if values["old"] is not None else None,
            new=str(values["new"]) if values["new"] is not None else None,
        )
        for field, values in proposed_data.items()
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


def _decide_member_change_request_sync(
    db: Session,
    request_id: uuid.UUID,
    data: MemberChangeRequestDecisionRequest,
    current_user: Member,
) -> tuple[str, dict[str, dict[str, object]], dict[str, str]] | None:
    request = member_change_request_service.get_change_request_or_404(db, request_id)
    _require_standesdb_admin(current_user, request.member.org_id)

    diff_snapshot = cast("dict[str, dict[str, object]]", dict(request.proposed_data))
    decisions_snapshot: dict[str, str] = dict(data.field_decisions)
    member = member_change_request_service.resolve_change_request(
        db, request, decisions_snapshot, current_user
    )

    if not member.email:
        return None
    return member.email, diff_snapshot, decisions_snapshot


@standesdb_router.post("/member-change-requests/{request_id}/decide")
async def decide_member_change_request(
    request_id: uuid.UUID,
    data: MemberChangeRequestDecisionRequest,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> StatusResponse:
    """Atomically resolve a change request: every proposed field must have
    a decision in one submission (enforced in the service layer) - there is
    no partially-decided state to save and resume later."""
    notification = await run_in_threadpool(
        _decide_member_change_request_sync, db, request_id, data, current_user
    )
    if notification:
        await arq_pool.enqueue_job(
            "task_send_member_change_request_resolved_email", *notification
        )
    return StatusResponse(status="resolved")


# --- Helper ---


def _require_standesdb_admin(user: Member, org_id: str | None) -> None:
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Keine Verbindung zugewiesen.",
        )
    perms = calculate_permissions(user)
    org_perm = f"standesdb{org_id.capitalize()}Admin"
    if org_perm not in perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(f"Fehlende Berechtigung: {org_perm}"),
        )


def _require_any_standesdb_admin(user: Member) -> None:
    perms = calculate_permissions(user)
    is_any_org_admin = any(
        f"standesdb{org_id.capitalize()}Admin" in perms for org_id in ("vbw", "vbn")
    )
    if not is_any_org_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Berechtigung.",
        )


def _own_image_changed_recipients(db: Session, member: Member) -> list[str] | None:
    if not member.org_id:
        return None
    perm = f"standesdb{member.org_id.capitalize()}Admin"
    return get_emails_with_permission(db, perm)


async def _notify_own_image_changed(
    db: Session,
    arq_pool: ArqRedis,
    member: Member,
    action: str,
) -> None:
    """Enqueue an info mail to the member's own org's standesdb admins
    after a self-service image change. Purely informational - there is no
    approval gate, the change is already applied by the time this runs.
    Silently skipped if the member has no org (nobody to notify), the
    image action itself is never blocked by this."""
    recipients = await run_in_threadpool(_own_image_changed_recipients, db, member)
    if recipients is None:
        return
    await arq_pool.enqueue_job(
        "task_send_own_image_changed_email", recipients, member.cn, action
    )
