"""Append-only history of role grants, revocations and period changes.

Roles carry permissions (see permission_service.PERMISSION_RULES), so the
history of who gave which role to whom is the history of who gave which
permissions to whom. Entries are written by the code that saves a member's
roles and read through a plain listing; nothing updates or deletes them.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import joinedload, selectinload

from app.models.enums import RoleAssignmentAction
from app.models.role_assignment_event import RoleAssignmentEvent
from app.schemas.role_history import RoleAssignmentEventResponse
from app.services.permission_service import permissions_conferred_by_role

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterable
    from datetime import date

    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class RoleEntry:
    role_id: str
    startdate: date
    enddate: date | None


@dataclass(frozen=True)
class RoleChange:
    action: RoleAssignmentAction
    entry: RoleEntry
    previous_enddate: date | None = None


def diff_role_entries(
    before: Iterable[RoleEntry], after: Iterable[RoleEntry]
) -> list[RoleChange]:
    """Changes that turn `before` into `after`.

    An entry is identified by its role and start date (the primary key of
    members_roles); the same entry with another end date is one period change,
    not a revocation plus a grant.
    """
    old = {(e.role_id, e.startdate): e for e in before}
    new = {(e.role_id, e.startdate): e for e in after}
    changes = [
        RoleChange(RoleAssignmentAction.REVOKED, entry)
        for key, entry in old.items()
        if key not in new
    ]
    changes += [
        RoleChange(RoleAssignmentAction.GRANTED, entry)
        for key, entry in new.items()
        if key not in old
    ]
    changes += [
        RoleChange(
            RoleAssignmentAction.PERIOD_CHANGED,
            new[key],
            previous_enddate=old[key].enddate,
        )
        for key in old.keys() & new.keys()
        if old[key].enddate != new[key].enddate
    ]
    return sorted(
        changes, key=lambda c: (c.entry.role_id, c.entry.startdate, c.action.value)
    )


def record_role_changes(
    db: Session,
    member_id: uuid.UUID,
    *,
    before: Iterable[RoleEntry],
    after: Iterable[RoleEntry],
    actor_id: uuid.UUID | None,
) -> None:
    """Add one history entry per change. Not committed: the caller's
    transaction decides, so a rejected save leaves no entry behind."""
    for change in diff_role_entries(before, after):
        db.add(
            RoleAssignmentEvent(
                member_id=member_id,
                role_id=change.entry.role_id,
                action=change.action,
                startdate=change.entry.startdate,
                enddate=change.entry.enddate,
                previous_enddate=change.previous_enddate,
                actor_id=actor_id,
            )
        )


def list_role_history(
    db: Session, page: int, page_size: int
) -> tuple[list[RoleAssignmentEventResponse], int]:
    """Newest first. Subject, actor and role are loaded with the page, so the
    number of statements does not depend on the page size."""
    total = db.query(RoleAssignmentEvent).count()
    events = (
        db.query(RoleAssignmentEvent)
        .options(
            joinedload(RoleAssignmentEvent.role),
            selectinload(RoleAssignmentEvent.member),
            selectinload(RoleAssignmentEvent.actor),
        )
        .order_by(RoleAssignmentEvent.created_at.desc(), RoleAssignmentEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [_to_response(event) for event in events], total


def _to_response(event: RoleAssignmentEvent) -> RoleAssignmentEventResponse:
    return RoleAssignmentEventResponse(
        id=event.id,
        occurred_at=event.created_at,
        action=event.action,
        member_id=event.member_id,
        member_cn=event.member.cn,
        actor_cn=event.actor.cn if event.actor else None,
        role_id=event.role_id,
        role_label=event.role.label or event.role_id,
        startdate=event.startdate,
        enddate=event.enddate,
        previous_enddate=event.previous_enddate,
        permissions=sorted(
            permissions_conferred_by_role(event.role, event.member.org_id)
        ),
    )
