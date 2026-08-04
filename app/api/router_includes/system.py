from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, PlainSerializer
from sqlalchemy.orm import Session

from app.api.auth_guards import require_permission
from app.api.deps import get_current_user
from app.core.scheduler import get_scheduled_jobs
from app.core.storage import StorageClient, get_storage
from app.db.database import get_db
from app.models.member import Member
from app.services import system_service
from app.services.backup_service import run_backup
from app.services.permission_service import (
    get_dev_superuser_cn,
    get_permission_rules_display,
)
from app.services.scheduled_task_run_service import (
    get_latest_run_per_job,
    list_job_runs,
)

system_router = APIRouter()


class EnvironmentResponse(BaseModel):
    environment: str


class PermissionRuleResponse(BaseModel):
    permission: str
    description: str
    cns: list[str]


class PermissionRulesResponse(BaseModel):
    rules: list[PermissionRuleResponse]
    dev_superuser_cn: str | None


# Pydantic v2 serializes Decimal as a JSON string by default, not a
# number — silently breaks the frontend's `number` typing. Same fix as
# app/schemas/p4x.py's MoneyOut (see feedback memory on this gotcha).
_DurationSecondsOut = Annotated[
    Decimal, PlainSerializer(float, return_type=float, when_used="json")
]


class ScheduledJobRunSummary(BaseModel):
    exit_code: int
    output: str | None
    started_at: datetime
    finished_at: datetime
    duration_seconds: _DurationSecondsOut

    model_config = ConfigDict(from_attributes=True)


class ScheduledJobRunListItem(ScheduledJobRunSummary):
    id: int
    job_id: str


class ScheduledJobResponse(BaseModel):
    id: str
    name: str
    trigger: str
    next_run: str | None = None
    description: str | None = None
    last_run: ScheduledJobRunSummary | None = None


class BackupTriggerResponse(BaseModel):
    backup_name: str
    triggered_at: str


@system_router.get("/environment")
def get_environment(
    _user: Annotated[Member, Depends(get_current_user)],
) -> EnvironmentResponse:
    """Current backend deployment stage (development/test/qa/production).

    Any authenticated member may read this — shown next to the frontend's
    own stage on the profile page so a mismatch between the two is visible
    at a glance. Requires only authentication, not systemAdmin.
    """
    return EnvironmentResponse(environment=system_service.get_app_environment())


@system_router.get("/permission-rules")
def list_permission_rules(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(get_current_user)],
) -> PermissionRulesResponse:
    """List all permission rules with their descriptions and current holders,
    plus the DEV_SUPERUSER_ID member's CN if that dev-only bypass is active.

    Any authenticated member may read this — it's a transparency page, not
    an admin tool, so it requires only authentication, not systemAdmin.
    """
    return PermissionRulesResponse(
        rules=[PermissionRuleResponse(**r) for r in get_permission_rules_display(db)],
        dev_superuser_cn=get_dev_superuser_cn(db),
    )


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
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
) -> list[ScheduledJobResponse]:
    """List all registered APScheduler jobs with trigger info, next run time,
    and each job's most recent recorded run (if any).

    Requires systemAdmin.
    """
    latest_runs = get_latest_run_per_job(db)
    jobs = []
    for j in get_scheduled_jobs():
        last_run = latest_runs.get(j["id"] or "")
        jobs.append(
            ScheduledJobResponse.model_validate(
                {
                    **j,
                    "last_run": ScheduledJobRunSummary.model_validate(last_run)
                    if last_run
                    else None,
                }
            )
        )
    return jobs


@system_router.get("/scheduled-jobs/{job_id}/history", response_model=dict)
def get_scheduled_job_history(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[Member, Depends(require_permission("systemAdmin"))],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, list[ScheduledJobRunListItem] | int]:
    """List the recorded run history for one job, newest first (paginated).

    Requires systemAdmin.
    """
    result = list_job_runs(db, job_id, page, page_size)
    return {
        "items": [
            ScheduledJobRunListItem.model_validate(item) for item in result["items"]
        ],
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
    }


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
