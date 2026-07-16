from fastapi import APIRouter

from app.api.router_includes.archive import (
    archive_router,
)
from app.api.router_includes.auth import auth_router
from app.api.router_includes.information import (
    information_router,
)
from app.api.router_includes.members import (
    members_router,
)
from app.api.router_includes.p4x import p4x_router
from app.api.router_includes.public_gallery_admin import (
    public_gallery_admin_router,
)
from app.api.router_includes.public_site import public_site_router
from app.api.router_includes.standesdb import (
    standesdb_router,
)
from app.api.router_includes.system import system_router
from app.api.router_includes.tracking import tracking_router

OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "Authentication",
        "description": (
            "Login, logout, JWT token refresh, password reset, and Google OAuth. "
            "Rate-limited to prevent brute-force attacks."
        ),
    },
    {
        "name": "Members",
        "description": "Current user profile and per-user preferences.",
    },
    {
        "name": "StandesDB",
        "description": (
            "The central member and contact registry (Standesbuch). "
            "CRUD for members, contacts, and profile images. "
            "Includes search, export (PDF booklet, labels), "
            "keys/roles lists, parent linking, and changelog."
        ),
    },
    {
        "name": "Archive",
        "description": (
            "Hierarchical document archive backed by S3 object storage. "
            "Directories, files, comments, versioning, trash/restore, "
            "and presigned download URLs."
        ),
    },
    {
        "name": "Information",
        "description": "Public information pages (payment account details).",
    },
    {
        "name": "P4x",
        "description": (
            "Financial accounting module (AH-Kassen). "
            "Bank account management, transaction import/browse, "
            "category assignment (filter-based and direct), "
            "partner management, fee configuration, debtor tracking, "
            "SumUp balance, and PDF summary reports."
        ),
    },
    {
        "name": "Tracking",
        "description": (
            "Audit trail and email monitoring. "
            "Activity logs with session grouping, sent email archive, "
            "email template registry with live preview rendering."
        ),
    },
    {
        "name": "System",
        "description": (
            "System administration: permission rule overview, "
            "scheduled job status, and a paginated database table browser."
        ),
    },
    {
        "name": "Public",
        "description": (
            "Unauthenticated endpoints backing the public www.vindobona2.at "
            "site: published gallery images and the contact form."
        ),
    },
    {
        "name": "Public Site Administration",
        "description": (
            "Authenticated management of the public www.vindobona2.at "
            "gallery (upload, reorder, publish/unpublish, delete). "
            "Requires the 'publicContentEditor' permission."
        ),
    },
]

api_router = APIRouter()

api_router.include_router(
    auth_router,
    prefix="/auth",
    tags=["Authentication"],
)
api_router.include_router(
    members_router,
    prefix="/members",
    tags=["Members"],
)
api_router.include_router(
    standesdb_router,
    prefix="/standesdb",
    tags=["StandesDB"],
)
api_router.include_router(
    archive_router,
    prefix="/archive",
    tags=["Archive"],
)
api_router.include_router(
    information_router,
    prefix="/information",
    tags=["Information"],
)
api_router.include_router(
    p4x_router,
    prefix="/p4x",
    tags=["P4x"],
)
api_router.include_router(
    tracking_router,
    prefix="/tracking",
    tags=["Tracking"],
)
api_router.include_router(
    system_router,
    prefix="/system",
    tags=["System"],
)
api_router.include_router(
    public_site_router,
    prefix="/public",
    tags=["Public"],
)
api_router.include_router(
    public_gallery_admin_router,
    prefix="/public-gallery-admin",
    tags=["Public Site Administration"],
)
