from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.auth_guards import require_permission
from app.api.deps import get_current_user
from app.core.mailer import send_entry_changed_email
from app.core.storage import StorageClient, get_storage
from app.db.database import get_db
from app.models.contact import Contact
from app.models.member import Member
from app.schemas.archive import PresignedUrlResponse
from app.schemas.standesdb import (
    ChangeLogEntry,
    ContactDetailResponse,
    ContactSaveRequest,
    ContactStatsResponse,
    ExportRequest,
    ImageUpdateRequest,
    KeysListResponse,
    MemberAuthActivityResponse,
    MemberDetailResponse,
    MemberDismissedResponse,
    MemberSaveRequest,
    MemberStatsResponse,
    ReferenceDataResponse,
    RolesListResponse,
    StatsResponse,
)
from app.services import (
    export_service,
    image_service,
    standesdb_service,
)
from app.services.permission_service import (
    calculate_permissions,
    get_emails_with_permission,
)

standesdb_router = APIRouter()


@standesdb_router.get("/stats")
def get_stats(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> StatsResponse:
    """Return aggregate statistics (member/contact counts by org and state)."""
    return StatsResponse(
        member=MemberStatsResponse(**standesdb_service.get_member_stats(db)),
        contact=ContactStatsResponse(**standesdb_service.get_contact_stats(db)),
    )


@standesdb_router.get("/search")
def search(
    q: Annotated[str, Query(min_length=3)],
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, list[dict[str, str | int]]]:
    """Full-text search across members and contacts."""
    return {"data": (standesdb_service.search_members_and_contacts(db, q))}


@standesdb_router.get("/reference-data")
def get_reference_data(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> ReferenceDataResponse:
    """Return reference data (orgs, states, roles, badges) for form dropdowns."""
    data = standesdb_service.get_reference_data(db)
    return ReferenceDataResponse.model_validate(data, from_attributes=True)


# --- Export ---


@standesdb_router.get("/export/config")
def get_export_config(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(require_permission("standesdbExport"))],
) -> dict[str, object]:
    """Return export configuration options (formats, available fields)."""
    return export_service.get_export_config(db)


@standesdb_router.post("/export")
def do_export(
    data: ExportRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(require_permission("standesdbExport"))],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> Response:
    """Generate and download an export file (booklet, labels, etc.)."""
    filter_data = data.model_dump()
    module = filter_data.pop("module")

    members = export_service.filter_members(db, filter_data)
    contacts = export_service.filter_contacts(db, filter_data)

    today = datetime.now(UTC).date().isoformat()

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


@standesdb_router.get(
    "/keys",
    response_model=KeysListResponse,
)
def get_keys_list(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(require_permission("keylist"))],
) -> dict[str, object]:
    """Return the list of key holders with their assigned keys."""
    return standesdb_service.get_keys_list(db)


@standesdb_router.get("/keys/download")
def download_keys_list(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(require_permission("keylist"))],
) -> Response:
    """Download the key holders list as a plain-text file."""
    today = datetime.now(UTC).date().isoformat()
    content = standesdb_service.generate_keys_download(db)
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": (f"attachment; filename=schluessel_{today}.txt")
        },
    )


# --- Roles List ---


@standesdb_router.get(
    "/roles",
    response_model=RolesListResponse,
)
def get_roles_list(
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
    year: Annotated[int | None, Query(ge=1928, lt=2100)] = None,
    semester: Annotated[str | None, Query(pattern="^(ss|ws)$")] = None,
) -> dict[str, object]:
    """Return members grouped by their current active roles."""
    if (year is None) != (semester is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "year und semester müssen beide angegeben werden oder beide fehlen."
            ),
        )
    return standesdb_service.get_roles_list(db, year, semester)


# --- Members ---


@standesdb_router.get("/members/{member_id}")
def get_member(
    member_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> MemberDetailResponse | MemberDismissedResponse:
    """Retrieve a single member by ID with all related data."""
    return standesdb_service.get_member_detail(db, member_id)


@standesdb_router.get("/members/{member_id}/auth-activity")
def get_member_auth_activity(
    member_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> MemberAuthActivityResponse:
    """Return recent authentication activity for a member."""
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )
    return MemberAuthActivityResponse(
        auth_lastlogin=member.auth_lastlogin,
        auth_lastsignal=member.auth_lastsignal,
        auth_lastlogout=member.auth_lastlogout,
    )


@standesdb_router.post("/members")
def create_member(
    data: MemberSaveRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str | int]:
    """Create a new member record."""
    _require_standesdb_admin(current_user, data.org_id)
    standesdb_service.validate_member_org(data, current_user)
    standesdb_service.validate_member_uniqueness(db, data)
    standesdb_service.validate_parent_id(db, data.parent_id, data.org_id)
    standesdb_service.validate_member_references(db, data)

    member = Member()
    diff = standesdb_service.apply_member_input(db, member, data, current_user)

    if diff:
        perm = f"standesdb{data.org_id.capitalize()}Admin"
        recipients = get_emails_with_permission(db, perm)
        background_tasks.add_task(
            send_entry_changed_email,
            recipients,
            "member",
            member.cn,
            diff,
            "store",
            current_user.cn,
        )

    return {"status": "ok", "id": member.id}


@standesdb_router.put("/members/{member_id}")
def update_member(
    member_id: int,
    data: MemberSaveRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str | int]:
    """Update an existing member's data and notify change subscribers."""
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

    if diff:
        perm = f"standesdb{member.org_id.capitalize()}Admin"
        recipients = get_emails_with_permission(db, perm)
        background_tasks.add_task(
            send_entry_changed_email,
            recipients,
            "member",
            member.cn,
            diff,
            "update",
            current_user.cn,
        )

    return {"status": "ok", "id": member.id}


@standesdb_router.get("/members/{member_id}/searchparent")
def search_parent(
    member_id: int,
    q: Annotated[str, Query(min_length=3)],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, list[dict[str, str | int]]]:
    """Search for potential parent members by name."""
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

    return {"data": standesdb_service.search_parent(db, member_id, q)}


# --- Contacts ---


@standesdb_router.get("/contacts/{contact_id}")
def get_contact(
    contact_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> ContactDetailResponse:
    """Retrieve a single contact by ID with all related data."""
    return standesdb_service.get_contact_detail(db, contact_id)


@standesdb_router.post("/contacts")
def create_contact(
    data: ContactSaveRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        Member, Depends(require_permission("standesdbContactAdmin"))
    ],
) -> dict[str, str | int]:
    """Create a new contact record."""
    standesdb_service.validate_contact_uniqueness(db, data.name)

    contact = Contact()
    input_dict = data.model_dump()
    diff = standesdb_service.apply_contact_input(db, contact, input_dict, current_user)

    if diff:
        recipients = get_emails_with_permission(db, "standesdbContactAdmin")
        background_tasks.add_task(
            send_entry_changed_email,
            recipients,
            "contact",
            contact.cn,
            diff,
            "store",
            current_user.cn,
        )

    return {"status": "ok", "id": contact.id}


@standesdb_router.put("/contacts/{contact_id}")
def update_contact(
    contact_id: int,
    data: ContactSaveRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        Member, Depends(require_permission("standesdbContactAdmin"))
    ],
) -> dict[str, str | int]:
    """Update an existing contact's data and notify change subscribers."""
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kontakt nicht gefunden.",
        )

    standesdb_service.validate_contact_uniqueness(db, data.name, exclude_id=contact_id)

    input_dict = data.model_dump()
    diff = standesdb_service.apply_contact_input(db, contact, input_dict, current_user)

    if diff:
        recipients = get_emails_with_permission(db, "standesdbContactAdmin")
        background_tasks.add_task(
            send_entry_changed_email,
            recipients,
            "contact",
            contact.cn,
            diff,
            "update",
            current_user.cn,
        )

    return {"status": "ok", "id": contact.id}


@standesdb_router.delete("/contacts/{contact_id}")
def delete_contact(
    contact_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        Member, Depends(require_permission("standesdbContactAdmin"))
    ],
) -> dict[str, str]:
    """Soft-delete a contact record."""
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kontakt nicht gefunden.",
        )
    standesdb_service.soft_delete_contact(db, contact, current_user)
    return {"status": "ok"}


# --- Member Images ---


@standesdb_router.get("/members/{member_id}/images")
def list_member_images(
    member_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, object]:
    """List all profile images for a member."""
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )
    images = image_service.get_images_for_owner(db, "member", member_id)
    return {
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


@standesdb_router.get("/members/{member_id}/images/{image_id}/download")
def download_member_image(
    member_id: int,
    image_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> Response:
    """Download a member's profile image (original or thumbnail)."""
    img = image_service.get_image_record(db, "member", member_id, image_id)
    return image_service.serve_download(img, storage)


@standesdb_router.get(
    "/members/{member_id}/images/{image_id}/url",
    response_model=PresignedUrlResponse,
)
def member_image_url(
    member_id: int,
    image_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
    thumb: Annotated[bool, Query()] = False,  # noqa: FBT002
) -> dict[str, str]:
    """Generate a presigned S3 URL for a member's profile image."""
    img = image_service.get_image_record(db, "member", member_id, image_id)
    url = image_service.get_presigned_url(
        img,
        storage,
        thumb=thumb,
    )
    return {"url": url}


@standesdb_router.post("/members/{member_id}/images")
def upload_member_image(
    member_id: int,
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
    description: Annotated[str | None, Form()] = None,
) -> dict[str, str | int]:
    """Upload a new profile image for a member."""
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
        description,
        current_user.id,
        storage,
    )
    return {"status": "ok", "id": img.id}


@standesdb_router.put("/members/{member_id}/images/{image_id}")
def update_member_image(
    member_id: int,
    image_id: int,
    data: ImageUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str]:
    """Update image metadata (description, set as default)."""
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )
    _require_standesdb_admin(current_user, member.org_id)
    img = image_service.get_image_record(db, "member", member_id, image_id)
    image_service.update_image(db, img, data.description, data.default)
    return {"status": "ok"}


@standesdb_router.delete("/members/{member_id}/images/{image_id}")
def delete_member_image(
    member_id: int,
    image_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, str]:
    """Delete a member's profile image from storage."""
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )
    _require_standesdb_admin(current_user, member.org_id)
    img = image_service.get_image_record(db, "member", member_id, image_id)
    image_service.delete_image(db, img)
    return {"status": "ok"}


# --- Contact Images ---


@standesdb_router.get("/contacts/{contact_id}/images")
def list_contact_images(
    contact_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
) -> dict[str, object]:
    """List all profile images for a contact."""
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kontakt nicht gefunden.",
        )
    images = image_service.get_images_for_owner(db, "contact", contact_id)
    return {
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


@standesdb_router.get("/contacts/{contact_id}/images/{image_id}/download")
def download_contact_image(
    contact_id: int,
    image_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> Response:
    """Download a contact's profile image (original or thumbnail)."""
    img = image_service.get_image_record(db, "contact", contact_id, image_id)
    return image_service.serve_download(img, storage)


@standesdb_router.get(
    "/contacts/{contact_id}/images/{image_id}/url",
    response_model=PresignedUrlResponse,
)
def contact_image_url(
    contact_id: int,
    image_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[Member, Depends(get_current_user)],
    storage: Annotated[StorageClient, Depends(get_storage)],
    thumb: Annotated[bool, Query()] = False,  # noqa: FBT002
) -> dict[str, str]:
    """Generate a presigned S3 URL for a contact's profile image."""
    img = image_service.get_image_record(db, "contact", contact_id, image_id)
    url = image_service.get_presigned_url(
        img,
        storage,
        thumb=thumb,
    )
    return {"url": url}


@standesdb_router.post("/contacts/{contact_id}/images")
def upload_contact_image(
    contact_id: int,
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        Member, Depends(require_permission("standesdbContactAdmin"))
    ],
    storage: Annotated[StorageClient, Depends(get_storage)],
    description: Annotated[str | None, Form()] = None,
) -> dict[str, str | int]:
    """Upload a new profile image for a contact."""
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
        description,
        current_user.id,
        storage,
    )
    return {"status": "ok", "id": img.id}


@standesdb_router.put("/contacts/{contact_id}/images/{image_id}")
def update_contact_image(
    contact_id: int,
    image_id: int,
    data: ImageUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[
        Member, Depends(require_permission("standesdbContactAdmin"))
    ],
) -> dict[str, str]:
    """Update contact image metadata (description, set as default)."""
    img = image_service.get_image_record(db, "contact", contact_id, image_id)
    image_service.update_image(db, img, data.description, data.default)
    return {"status": "ok"}


@standesdb_router.delete("/contacts/{contact_id}/images/{image_id}")
def delete_contact_image(
    contact_id: int,
    image_id: int,
    db: Annotated[Session, Depends(get_db)],
    _current_user: Annotated[
        Member, Depends(require_permission("standesdbContactAdmin"))
    ],
) -> dict[str, str]:
    """Delete a contact's profile image from storage."""
    img = image_service.get_image_record(db, "contact", contact_id, image_id)
    image_service.delete_image(db, img)
    return {"status": "ok"}


# --- Changelog ---


@standesdb_router.get("/members/{member_id}/changelog")
def list_member_changelog(
    member_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> list[ChangeLogEntry]:
    """Return the change history for a member."""
    return standesdb_service.get_member_changelog(db, member_id)


@standesdb_router.get("/contacts/{contact_id}/changelog")
def list_contact_changelog(
    contact_id: int,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> list[ChangeLogEntry]:
    """Return the change history for a contact."""
    return standesdb_service.get_contact_changelog(db, contact_id)


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
