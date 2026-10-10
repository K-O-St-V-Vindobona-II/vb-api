from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.api.deps import get_current_user
from app.models.member import Member
from app.services.permission_service import calculate_permissions


def require_permission(required_perm: str) -> Callable[..., Member]:
    """
    Usage: Depends(require_permission("archiveAdmin"))
    """

    def _guard(current_user: Member = Depends(get_current_user)) -> Member:
        if required_perm not in calculate_permissions(current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"missing permission: {required_perm}",
            )
        return current_user

    return _guard


# Routers annotate the guarded user with one of these aliases instead of
# importing the ORM model and repeating the permission name.
RequireSystemAdmin = Annotated[Member, Depends(require_permission("systemAdmin"))]
RequireP4xView = Annotated[Member, Depends(require_permission("p4xView"))]
RequireP4xAdmin = Annotated[Member, Depends(require_permission("p4xAdmin"))]
RequirePublicContentEditor = Annotated[
    Member, Depends(require_permission("publicContentEditor"))
]
RequireStandesdbContactAdmin = Annotated[
    Member, Depends(require_permission("standesdbContactAdmin"))
]
RequireStandesdbExport = Annotated[
    Member, Depends(require_permission("standesdbExport"))
]
RequireKeylist = Annotated[Member, Depends(require_permission("keylist"))]
