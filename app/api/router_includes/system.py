from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth_guards import require_permission
from app.core.scheduler import get_scheduled_jobs
from app.core.storage import StorageClient, get_storage
from app.db.database import get_db
from app.models.member import Member
from app.services import system_service
from app.services.backup_service import run_backup
from app.services.permission_service import (
    get_permission_rules_display,
)

system_router = APIRouter()


class PermissionRuleResponse(BaseModel):
    permission: str
    description: str


class ScheduledJobResponse(BaseModel):
    id: str
    name: str
    trigger: str
    next_run: str | None = None
    description: str | None = None


class BackupTriggerResponse(BaseModel):
    backup_name: str
    triggered_at: str


@system_router.get("/permission-rules")
def list_permission_rules(
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> list[PermissionRuleResponse]:
    """List all permission rules with their descriptions. Requires systemAdmin."""
    return [PermissionRuleResponse(**r) for r in get_permission_rules_display()]


@system_router.post("/backups/trigger", status_code=status.HTTP_201_CREATED)
def trigger_backup(
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
    storage: Annotated[StorageClient, Depends(get_storage)],
) -> BackupTriggerResponse:
    """Manually trigger an immediate DB backup to S3. Requires systemAdmin.

    Runs synchronously (pg_dump + S3 upload blocks the request), same as
    backup_db.py's CLI behavior — DB size here does not warrant background-
    job infrastructure. Available on every stage, not just production.
    """
    try:
        backup_name = run_backup(storage, manual=True)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return BackupTriggerResponse(
        backup_name=backup_name,
        triggered_at=datetime.now(UTC).isoformat(),
    )


@system_router.get("/scheduled-jobs")
def list_scheduled_jobs(
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> list[ScheduledJobResponse]:
    """List all registered APScheduler jobs with trigger info and next run time.

    Requires systemAdmin.
    """
    return [ScheduledJobResponse.model_validate(j) for j in get_scheduled_jobs()]


@system_router.get("/tables")
def list_tables(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> list[str]:
    """List all database table names. Requires systemAdmin."""
    return system_service.get_valid_tables(db)


@system_router.get("/tables/{table_name}")
def get_table_data(
    table_name: str,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, object]:
    """Browse a database table with paginated rows and column metadata.

    Requires systemAdmin.
    """
    return system_service.get_table_data(db, table_name, page, page_size)
