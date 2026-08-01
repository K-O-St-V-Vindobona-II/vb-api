import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.member import Member

logger = logging.getLogger(__name__)

_settings = get_settings()
_app_environment = _settings.app_environment
_raw_dev_superuser_id: int = _settings.dev_superuser_id
if _raw_dev_superuser_id and _app_environment != "development":
    logger.warning(
        "DEV_SUPERUSER_ID is set but will be IGNORED outside development "
        "(current APP_ENVIRONMENT=%s). Remove it from this stage's env file.",
        _app_environment,
    )
# Only active in development — existing check already short-circuits for 0.
DEV_SUPERUSER_ID: int = (
    _raw_dev_superuser_id if _app_environment == "development" else 0
)


class PermissionRuleDisplay(TypedDict):
    permission: str
    description: str
    cns: list[str]


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
        description=("Rolle 'Internetreferent' oder 'Archivar 2' + Organisation VBW"),
        condition=lambda rids, _rgrps, org, _email: (
            org == "vbw" and bool({"internetreferent", "archivar2"}.intersection(rids))
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
        description=("Rolle 'Philisterkassier' + Organisation VBW"),
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


def _active_roles(member: Member) -> tuple[set[str], set[str]]:
    """Currently active role IDs and role groups for a member (today between
    startdate and enddate)."""
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

    return active_role_ids, active_role_groups


def calculate_permissions(
    member: Member,
) -> list[str]:
    """
    Derives a member's effective, active permissions from their roles and
    organization. DEV_SUPERUSER_ID grants every permission regardless of
    roles — a development-only convenience, never active outside
    APP_ENVIRONMENT=development (see module-level derivation above).
    """
    if DEV_SUPERUSER_ID and member.id == DEV_SUPERUSER_ID:
        return list(ALL_PERMISSIONS)

    active_role_ids, active_role_groups = _active_roles(member)
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


def _group_members_by_rule_condition(db: Session) -> dict[str, list[Member]]:
    """Loads every member once and, per member, evaluates each rule's
    condition directly (not via calculate_permissions()) — deliberately
    bypasses the DEV_SUPERUSER_ID blanket-access shortcut, since this backs
    the permission-setup display of who actually satisfies each rule's
    condition, not who has effective access.
    """
    members = db.query(Member).all()
    holders: dict[str, list[Member]] = {
        permission: [] for permission in ALL_PERMISSIONS
    }
    for member in members:
        active_role_ids, active_role_groups = _active_roles(member)
        for rule in PERMISSION_RULES:
            if rule.condition(
                active_role_ids, active_role_groups, member.org_id, member.email
            ):
                holders[rule.permission].append(member)
    return holders


def get_permission_rules_display(db: Session) -> list[PermissionRuleDisplay]:
    holders = _group_members_by_rule_condition(db)
    return [
        {
            "permission": rule.permission,
            "description": rule.description,
            "cns": sorted(member.cn for member in holders[rule.permission]),
        }
        for rule in PERMISSION_RULES
    ]


def get_dev_superuser_cn(db: Session) -> str | None:
    """CN of the DEV_SUPERUSER_ID member, if the bypass is active — used to
    surface a one-time notice on the permission-setup page that this member
    has blanket access in the dev environment, independent of any rule's
    condition."""
    if not DEV_SUPERUSER_ID:
        return None
    dev_superuser = db.get(Member, DEV_SUPERUSER_ID)
    return dev_superuser.cn if dev_superuser else None


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
