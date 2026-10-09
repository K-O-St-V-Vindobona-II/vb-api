import uuid
from typing import Annotated

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

from app.api.auth_guards import (
    RequireKeylist,
    RequireStandesdbContactAdmin,
    RequireStandesdbExport,
)
from app.api.deps import CurrentUser
from app.core.arq_pool import get_arq_pool
from app.core.datetime_utils import local_today
from app.core.storage import StorageClient, get_storage
from app.db.database import get_db
from app.schemas.archive import PresignedUrlResponse
from app.schemas.base import PaginatedResponse, StatusIdResponse, StatusResponse
from app.schemas.standesdb import (
    ChangeLogEntry,
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
    standesdb_entry_service,
    standesdb_service,
)

standesdb_router = APIRouter()


async def _enqueue_entry_changed_email(
    arq_pool: ArqRedis, notification: standesdb_entry_service.EntryChangedNotification
) -> None:
    await arq_pool.enqueue_job(
        "task_send_entry_changed_email",
        notification.to_emails,
        notification.entry_type,
        notification.entry_cn,
        notification.diff,
        change_type=notification.change_type,
        modifier_cn=notification.modifier_cn,
    )


@standesdb_router.get("/stats")
def get_stats(
    db: Annotated[Session, Depends(get_db)],
    _current_user: CurrentUser,
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
    _current_user: CurrentUser,
) -> SearchResponse:
    """Full-text search across members and contacts by name/couleurname, minimum 3
    characters. No special permission - any authenticated member."""
    return SearchResponse.model_validate(
        {"data": standesdb_service.search_members_and_contacts(db, q)}
    )


@standesdb_router.get("/reference-data")
def get_reference_data(
    db: Annotated[Session, Depends(get_db)],
    _current_user: CurrentUser,
) -> ReferenceDataResponse:
    """Return reference data (orgs, states, roles, badges) for form dropdowns. No
    special permission - any authenticated member."""
    data = standesdb_service.get_reference_data(db)
    return ReferenceDataResponse.model_validate(data, from_attributes=True)


# --- Export ---


@standesdb_router.get("/export/config")
def get_export_config(
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequireStandesdbExport,
) -> ExportConfigResponse:
    """Return export configuration options (available formats/fields) for the export
    module. Requires standesdbExport."""
    return ExportConfigResponse.model_validate(export_service.get_export_config(db))


@standesdb_router.post("/export")
def do_export(
    data: ExportRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: RequireStandesdbExport,
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
    _current_user: RequireKeylist,
) -> KeysListResponse:
    """Return the list of key holders with their assigned keys. Requires keylist."""
    return KeysListResponse.model_validate(standesdb_service.get_keys_list(db))


@standesdb_router.get("/keys/download")
def download_keys_list(
    db: Annotated[Session, Depends(get_db)],
    _current_user: RequireKeylist,
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
    _current_user: CurrentUser,
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
    current_user: CurrentUser,
) -> MemberDetailResponse | MemberDismissedResponse:
    """Retrieve a single member by ID with all related data (roles, badges, keys,
    contact info). No special permission - any authenticated member; the account
    status is returned only to the member and the admins of the member's
    organisation, and write operations enforce org-scoped admin permission
    separately."""
    return standesdb_service.get_member_detail(db, member_id, current_user)


@standesdb_router.get("/members/{member_id}/auth-activity")
def get_member_auth_activity(
    member_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> MemberAuthActivityResponse:
    """Return recent authentication activity for a member.

    Requires standesdb write access for the member's own org (same check
    as editing the member - see require_org_admin()), not a
    blanket systemAdmin permission.
    """
    member = standesdb_entry_service.get_member_for_org_admin(
        db, member_id, current_user
    )
    return MemberAuthActivityResponse(
        auth_lastlogin=member.auth_lastlogin,
        auth_lastsignal=member.auth_lastsignal,
        auth_lastlogout=member.auth_lastlogout,
    )


@standesdb_router.post("/members", status_code=status.HTTP_201_CREATED)
async def create_member(
    data: MemberSaveRequest,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> StatusIdResponse:
    """Create a new member record. Requires the standesdb admin permission for the
    target org (data.org_id) - checked at runtime, not via a route dependency."""
    member, notification = await run_in_threadpool(
        standesdb_entry_service.create_member, db, data, current_user
    )
    await _enqueue_entry_changed_email(arq_pool, notification)
    return StatusIdResponse(status="ok", id=member.id)


@standesdb_router.put("/members/{member_id}")
async def update_member(
    member_id: uuid.UUID,
    data: MemberSaveRequest,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> StatusIdResponse:
    """Update an existing member's data and notify the org's standesdb admins by email
    if anything actually changed. Requires the standesdb admin permission for the
    member's own org - checked at runtime, not via a route dependency."""
    member, notification = await run_in_threadpool(
        standesdb_entry_service.update_member, db, member_id, data, current_user
    )
    if notification:
        await _enqueue_entry_changed_email(arq_pool, notification)
    return StatusIdResponse(status="ok", id=member.id)


@standesdb_router.get("/members/{member_id}/searchparent")
def search_parent(
    member_id: uuid.UUID,
    q: Annotated[str, Query(min_length=3)],
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> ParentSearchResponse:
    """Search for potential parent members (e.g. for the sponsoring 'Bürge'
    relationship) by name, minimum 3 characters. Requires the standesdb admin
    permission for the member's own org."""
    standesdb_entry_service.get_assigned_member_for_org_admin(
        db, member_id, current_user
    )
    return ParentSearchResponse.model_validate(
        {"data": standesdb_service.search_parent(db, member_id, q)}
    )


# --- Self-service Stammdaten (own account, no admin permission required) ---


@standesdb_router.get("/members/me/stammdaten")
def get_own_stammdaten(
    current_user: CurrentUser,
) -> MemberSelfServiceDetailResponse:
    """Return the authenticated member's own live Stammdaten - used to
    pre-fill the self-service form when there's no pending change request
    (see GET /members/me/change-request for that case)."""
    return standesdb_entry_service.build_own_stammdaten_response(current_user)


@standesdb_router.get("/members/me/change-request")
def get_own_change_request(
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> MyChangeRequestResponse:
    """Return the authenticated member's own pending change request, if
    any - 404 if none, so the frontend falls back to live Stammdaten."""
    return standesdb_entry_service.get_own_change_request_response(db, current_user)


@standesdb_router.post("/members/me/change-request")
async def submit_own_change_request(
    data: MemberSelfServiceSaveRequest,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> StatusResponse:
    """Submit (or update, if one is already pending) a self-service
    Stammdaten change request. No admin permission required - every member
    may propose changes to their own account.
    """
    notification = await run_in_threadpool(
        standesdb_entry_service.submit_own_change_request, db, data, current_user
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
    _current_user: CurrentUser,
) -> ContactDetailResponse:
    """Retrieve a single contact by ID with all related data. No special permission -
    any authenticated member."""
    return standesdb_service.get_contact_detail(db, contact_id)


@standesdb_router.post("/contacts", status_code=status.HTTP_201_CREATED)
async def create_contact(
    data: ContactSaveRequest,
    # Permission check first: FastAPI resolves dependencies in parameter
    # order, so an unprivileged caller gets rejected before the (real,
    # connection-acquiring) arq pool dependency ever runs.
    current_user: RequireStandesdbContactAdmin,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
) -> StatusIdResponse:
    """Create a new contact record. Requires standesdbContactAdmin."""
    contact, notification = await run_in_threadpool(
        standesdb_entry_service.create_contact, db, data, current_user
    )
    await _enqueue_entry_changed_email(arq_pool, notification)
    return StatusIdResponse(status="ok", id=contact.id)


@standesdb_router.put("/contacts/{contact_id}")
async def update_contact(
    contact_id: uuid.UUID,
    data: ContactSaveRequest,
    current_user: RequireStandesdbContactAdmin,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
) -> StatusIdResponse:
    """Update an existing contact's data and notify subscribers by email if anything
    actually changed. Requires standesdbContactAdmin."""
    contact, notification = await run_in_threadpool(
        standesdb_entry_service.update_contact, db, contact_id, data, current_user
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
    current_user: RequireStandesdbContactAdmin,
) -> None:
    """Soft-delete a contact record - sets deleted_at, does not remove
    the row. Requires standesdbContactAdmin."""
    standesdb_entry_service.delete_contact(db, contact_id, current_user)


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
    current_user: CurrentUser,
) -> ImageListResponse:
    """List the authenticated member's own profile images, plus which one
    (if any) is the default. No admin permission required - identical data
    to GET /members/{member_id}/images (already open to any authenticated
    caller), just without needing to know/pass your own id."""
    return image_service.build_image_list_response(db, "member", current_user)


@standesdb_router.post("/members/me/images", status_code=status.HTTP_201_CREATED)
async def upload_own_member_image(
    file: Annotated[UploadFile, File()],
    *,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
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
    current_user: CurrentUser,
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
    current_user: CurrentUser,
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
    _current_user: CurrentUser,
) -> ImageListResponse:
    """List all profile images for a member, plus which one (if any) is the default. No
    special permission - any authenticated member."""
    member = standesdb_service.get_visible_member_or_404(db, member_id)
    return image_service.build_image_list_response(db, "member", member)


@standesdb_router.get("/members/{member_id}/images/{image_id}/download")
def download_member_image(
    member_id: uuid.UUID,
    image_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: CurrentUser,
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> Response:
    """Download a member's profile image file (original or thumbnail). No special
    permission - any authenticated member."""
    standesdb_service.get_visible_member_or_404(db, member_id)
    img = image_service.get_image_for_serving(db, "member", member_id, image_id)
    return image_service.serve_download(img, storage)


@standesdb_router.get("/members/{member_id}/images/{image_id}/url")
def member_image_url(
    member_id: uuid.UUID,
    image_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: CurrentUser,
    storage: Annotated[StorageClient, Depends(get_storage)],
    thumb: Annotated[bool, Query()] = False,  # noqa: FBT002
) -> PresignedUrlResponse:
    """Generate a time-limited presigned S3 URL for a member's profile image (original
    or thumbnail via thumb=true). No special permission - any authenticated member."""
    standesdb_service.get_visible_member_or_404(db, member_id)
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
    current_user: CurrentUser,
    storage: Annotated[StorageClient, Depends(get_storage)],
    description: Annotated[str | None, Form()] = None,
) -> StatusIdResponse:
    """Upload a new profile image for a member. Requires the standesdb admin permission
    for the member's own org."""
    standesdb_entry_service.get_member_for_org_admin(db, member_id, current_user)
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
    current_user: CurrentUser,
) -> StatusResponse:
    """Update a member image's description or set it as the default. Requires the
    standesdb admin permission for the member's own org."""
    standesdb_entry_service.get_member_for_org_admin(db, member_id, current_user)
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
    current_user: CurrentUser,
) -> None:
    """Delete a member's profile image from storage. Requires the standesdb admin
    permission for the member's own org."""
    standesdb_entry_service.get_member_for_org_admin(db, member_id, current_user)
    img = image_service.get_image_record(db, "member", member_id, image_id)
    image_service.delete_image(db, img)


# --- Contact Images ---


@standesdb_router.get("/contacts/{contact_id}/images")
def list_contact_images(
    contact_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: CurrentUser,
) -> ImageListResponse:
    """List all profile images for a contact, plus which one (if any) is the default. No
    special permission - any authenticated member."""
    contact = standesdb_entry_service.get_contact_or_404(db, contact_id)
    return image_service.build_image_list_response(db, "contact", contact)


@standesdb_router.get("/contacts/{contact_id}/images/{image_id}/download")
def download_contact_image(
    contact_id: uuid.UUID,
    image_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _current_user: CurrentUser,
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
    _current_user: CurrentUser,
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
    current_user: RequireStandesdbContactAdmin,
    storage: Annotated[StorageClient, Depends(get_storage)],
    description: Annotated[str | None, Form()] = None,
) -> StatusIdResponse:
    """Upload a new profile image for a contact. Requires standesdbContactAdmin."""
    standesdb_entry_service.get_contact_or_404(db, contact_id)
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
    _current_user: RequireStandesdbContactAdmin,
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
    _current_user: RequireStandesdbContactAdmin,
) -> None:
    """Delete a contact's profile image from storage. Requires standesdbContactAdmin."""
    img = image_service.get_image_record(db, "contact", contact_id, image_id)
    image_service.delete_image(db, img)


# --- Changelog ---


@standesdb_router.get("/members/{member_id}/changelog")
def list_member_changelog(
    member_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PaginatedResponse[ChangeLogEntry]:
    """Return the change history for a member, paginated.

    Requires the standesdb admin permission for the member's own org.
    """
    standesdb_entry_service.get_member_for_org_admin(db, member_id, current_user)
    return PaginatedResponse[ChangeLogEntry].model_validate(
        standesdb_service.get_member_changelog(db, member_id, page, page_size)
    )


@standesdb_router.get("/contacts/{contact_id}/changelog")
def list_contact_changelog(
    contact_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    _user: RequireStandesdbContactAdmin,
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
    current_user: CurrentUser,
) -> MemberChangeRequestListResponse:
    """List pending self-service change requests for the orgs the caller
    administers. Empty for a non-admin, not an error - matches the
    org-scoped filtering already applied inside the service query."""
    return standesdb_entry_service.list_change_requests(db, current_user)


@standesdb_router.get("/member-change-requests/{request_id}")
def get_member_change_request(
    request_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> MemberChangeRequestDetailResponse:
    """Return one change request with its full field-level diff. Org-scoped
    admin permission check, same function as the member-edit endpoints."""
    return standesdb_entry_service.get_change_request_detail(
        db, request_id, current_user
    )


@standesdb_router.post("/member-change-requests/{request_id}/decide")
async def decide_member_change_request(
    request_id: uuid.UUID,
    data: MemberChangeRequestDecisionRequest,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> StatusResponse:
    """Atomically resolve a change request: every proposed field must have
    a decision in one submission (enforced in the service layer) - there is
    no partially-decided state to save and resume later."""
    notification = await run_in_threadpool(
        standesdb_entry_service.decide_change_request,
        db,
        request_id,
        data,
        current_user,
    )
    if notification:
        await arq_pool.enqueue_job(
            "task_send_member_change_request_resolved_email", *notification
        )
    return StatusResponse(status="resolved")


# --- Helper ---


async def _notify_own_image_changed(
    db: Session,
    arq_pool: ArqRedis,
    member: CurrentUser,
    action: str,
) -> None:
    """Enqueue an info mail to the member's own org's standesdb admins
    after a self-service image change. Purely informational - there is no
    approval gate, the change is already applied by the time this runs.
    Silently skipped if the member has no org (nobody to notify), the
    image action itself is never blocked by this."""
    recipients = await run_in_threadpool(
        standesdb_entry_service.get_own_image_changed_recipients, db, member
    )
    if recipients is None:
        return
    await arq_pool.enqueue_job(
        "task_send_own_image_changed_email", recipients, member.cn, action
    )
