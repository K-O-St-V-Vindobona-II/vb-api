import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import APP_ENVIRONMENT
from app.models.member import Member

logger = logging.getLogger(__name__)

_raw_dev_superuser_id: int = int(os.environ.get("DEV_SUPERUSER_ID", "0"))
if _raw_dev_superuser_id and APP_ENVIRONMENT == "production":
    logger.warning(
        "DEV_SUPERUSER_ID is set but will be IGNORED in production. "
        "Remove it from the production env file."
    )
# Forced to 0 in production — existing check already short-circuits for 0.
DEV_SUPERUSER_ID: int = _raw_dev_superuser_id if APP_ENVIRONMENT != "production" else 0


@dataclass(frozen=True)
class PermissionRule:
    permission: str
    description: str
    condition: Callable[
        [set[str], set[str], str | None, str | None],
        bool,
    ]


PERMISSION_RULES: list[PermissionRule] = [
    PermissionRule(
        permission="archiveAdmin",
        description=("Rolle 'Internetreferent' + Organisation VBW"),
        condition=lambda rids, _rgrps, org, _email: (
            "internetreferent" in rids and org == "vbw"
        ),
    ),
    PermissionRule(
        permission="systemAdmin",
        description=(
            "Rolle 'Internetreferent'"
            " + Organisation VBW,"
            " oder Email"
            " michael.schimpl@gmail.com"
        ),
        condition=lambda rids, _rgrps, org, email: (
            ("internetreferent" in rids and org == "vbw")
            or email == "michael.schimpl@gmail.com"
        ),
    ),
    PermissionRule(
        permission="standesdbContactAdmin",
        description="Rolle 'Standesführer'",
        condition=lambda rids, _rgrps, _org, _email: "standesfuehrer" in rids,
    ),
    PermissionRule(
        permission="standesdbVbwAdmin",
        description=("Rolle 'Standesführer' + Organisation VBW"),
        condition=lambda rids, _rgrps, org, _email: (
            "standesfuehrer" in rids and org == "vbw"
        ),
    ),
    PermissionRule(
        permission="standesdbVbnAdmin",
        description=("Rolle 'Standesführer' + Organisation VBN"),
        condition=lambda rids, _rgrps, org, _email: (
            "standesfuehrer" in rids and org == "vbn"
        ),
    ),
    PermissionRule(
        permission="standesdbExport",
        description=("Rollengruppe CHC/PhilCHC oder Rolle 'Standesführer'"),
        condition=lambda rids, rgrps, _org, _email: (
            bool({"chc", "philchc"}.intersection(rgrps)) or "standesfuehrer" in rids
        ),
    ),
    PermissionRule(
        permission="keylist",
        description="Rollengruppe CHC oder PhilCHC",
        condition=lambda _rids, rgrps, _org, _email: bool(
            {"chc", "philchc"}.intersection(rgrps)
        ),
    ),
    PermissionRule(
        permission="p4xView",
        description=("Rollengruppe PhilCHC + Organisation VBW"),
        condition=lambda _rids, rgrps, org, _email: "philchc" in rgrps and org == "vbw",
    ),
    PermissionRule(
        permission="p4xAdmin",
        description=("Rolle 'Phil-x' + Organisation VBW"),
        condition=lambda rids, _rgrps, org, _email: (
            "phil-xxxx" in rids and org == "vbw"
        ),
    ),
    PermissionRule(
        permission="publicContentEditor",
        description="Rolle 'Internetreferent' + Organisation VBW",
        condition=lambda rids, _rgrps, org, _email: (
            "internetreferent" in rids and org == "vbw"
        ),
    ),
]

ALL_PERMISSIONS: list[str] = sorted({rule.permission for rule in PERMISSION_RULES})


def calculate_permissions(
    member: Member,
) -> list[str]:
    """
    Derives a member's active permissions
    from their roles and organization.
    """
    if DEV_SUPERUSER_ID and member.id == DEV_SUPERUSER_ID:
        return list(ALL_PERMISSIONS)

    today = datetime.now(UTC).date()
    active_role_ids: set[str] = set()
    active_role_groups: set[str] = set()

    for mr in member.member_roles:
        started = mr.startdate <= today
        not_ended = mr.enddate is None or mr.enddate > today
        if started and not_ended:
            active_role_ids.add(mr.role_id)
            if mr.role and mr.role.group:
                active_role_groups.add(mr.role.group)

    return [
        rule.permission
        for rule in PERMISSION_RULES
        if rule.condition(
            active_role_ids,
            active_role_groups,
            member.org_id,
            member.email,
        )
    ]


def get_permission_rules_display() -> list[dict[str, str]]:
    return [
        {
            "permission": rule.permission,
            "description": rule.description,
        }
        for rule in PERMISSION_RULES
    ]


def get_emails_with_permission(db: Session, permission: str) -> list[str]:
    members = (
        db.query(Member)
        .filter(
            Member.email.isnot(None),
            Member.email != "",
        )
        .all()
    )
    return [
        m.email for m in members if m.email and permission in calculate_permissions(m)
    ]
