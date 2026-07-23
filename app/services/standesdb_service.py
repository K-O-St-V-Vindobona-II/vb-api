import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from itertools import combinations

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.models.badge import Badge
from app.models.contact import Contact
from app.models.contacts_log import ContactsLog
from app.models.enums import MemberDeliveryPreference
from app.models.key import Key
from app.models.member import Member
from app.models.member_badge import MemberBadge
from app.models.member_key import MemberKey
from app.models.member_role import MemberRole
from app.models.members_log import MembersLog
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.schemas.standesdb import (
    BadgeDetailResponse,
    BadgeEntry,
    ChangeLogEntry,
    ContactDetailResponse,
    KeyDetailResponse,
    KeyEntry,
    MemberDetailResponse,
    MemberDismissedResponse,
    MemberSaveRequest,
    RoleHistoryEntry,
    RoleHistoryResponse,
    TreeNodeResponse,
)

# --- Stats ---


def get_member_stats(db: Session) -> dict[str, dict[str, int]]:
    orgs = db.query(Org).all()
    stats: dict[str, dict[str, int]] = {
        "present": {},
        "dismissed": {},
        "dead": {},
        "dismissed_dead": {},
    }
    for org in orgs:
        base = db.query(Member).filter(Member.org_id == org.id)
        stats["present"][org.id] = base.filter(
            Member.entlassen == False,  # noqa: E712
            Member.verstorben == False,  # noqa: E712
        ).count()
        stats["dismissed"][org.id] = base.filter(
            Member.entlassen == True,  # noqa: E712
            Member.verstorben == False,  # noqa: E712
        ).count()
        stats["dead"][org.id] = base.filter(
            Member.entlassen == False,  # noqa: E712
            Member.verstorben == True,  # noqa: E712
        ).count()
        stats["dismissed_dead"][org.id] = base.filter(
            Member.entlassen == True,  # noqa: E712
            Member.verstorben == True,  # noqa: E712
        ).count()
    return stats


def get_contact_stats(db: Session) -> dict[str, int]:
    total = db.query(Contact).filter(Contact.deleted_at.is_(None)).count()
    vbw = (
        db.query(Contact)
        .filter(
            Contact.deleted_at.is_(None),
            Contact.org_id == "vbw",
        )
        .count()
    )
    vbn = (
        db.query(Contact)
        .filter(
            Contact.deleted_at.is_(None),
            Contact.org_id == "vbn",
        )
        .count()
    )
    common = total - vbw - vbn
    return {"common": common, "vbw": vbw, "vbn": vbn}


# --- Search ---


def search_members_and_contacts(db: Session, term: str) -> list[dict[str, str | int]]:
    results = []

    members = (
        db.query(Member)
        .filter(
            (Member.vorname.ilike(f"%{term}%"))
            | (Member.nachname.ilike(f"%{term}%"))
            | (Member.couleurname.ilike(f"%{term}%"))
        )
        .all()
    )
    for m in members:
        org_label = m.org_id.upper() if m.org_id else "?"
        results.append(
            {
                "type": "member",
                "id": m.id,
                "label": (f"Mitglied ({org_label}): {m.cn}"),
            }
        )

    contacts = (
        db.query(Contact)
        .filter(
            Contact.deleted_at.is_(None),
            (Contact.name.ilike(f"%{term}%"))
            | (Contact.couleurname.ilike(f"%{term}%")),
        )
        .all()
    )
    results.extend(
        {
            "type": "contact",
            "id": c.id,
            "label": f"Kontakt: {c.cn}",
        }
        for c in contacts
    )

    return results


# --- Member Detail ---


def _build_tree_node(member: Member) -> TreeNodeResponse:
    return TreeNodeResponse(
        id=member.id,
        cn=member.cn,
        gruender=member.gruender or False,
        org_id=member.org_id,
        state_id=member.state_id,
        entlassen=member.entlassen or False,
        verstorben=member.verstorben or False,
        children=[_build_tree_node(c) for c in member.children],
    )


def _build_ancestry(member: Member) -> list[dict[str, object]]:
    ancestry = []
    current = member
    while current:
        ancestry.append(
            TreeNodeResponse(
                id=current.id,
                cn=current.cn,
                gruender=current.gruender or False,
                org_id=current.org_id,
                state_id=current.state_id,
                entlassen=current.entlassen or False,
                verstorben=current.verstorben or False,
            ).model_dump()
        )
        if current.parent_id and current.parent_id != 0:
            current = current.parent
        else:
            break
    return list(reversed(ancestry))


def _build_roles_list(member: Member) -> list[RoleHistoryResponse]:
    return [
        RoleHistoryResponse(
            id=mr.role_id,
            label=mr.role.label if mr.role else None,
            group=mr.role.group if mr.role else None,
            order=(mr.role.order or 0) if mr.role else 0,
            startdate=mr.startdate,
            enddate=mr.enddate,
        )
        for mr in member.member_roles
    ]


def _build_badges_list(member: Member) -> list[BadgeDetailResponse]:
    return [
        BadgeDetailResponse(
            id=mb.badge_id,
            name=mb.badge.name or "",
            group=mb.badge.group,
            order=mb.badge.order or 0,
            presentationdate=mb.presentationdate,
            presentationdate_accuracy=mb.presentationdate_accuracy or 0,
        )
        for mb in member.member_badges
    ]


def _build_keys_list(member: Member) -> list[KeyDetailResponse]:
    return [
        KeyDetailResponse(
            id=mk.key_id,
            name=mk.key.name or "",
            presentationdate=mk.presentationdate,
            presentationdate_accuracy=mk.presentationdate_accuracy or 0,
        )
        for mk in member.member_keys
    ]


def get_member_detail(
    db: Session, member_id: int
) -> MemberDetailResponse | MemberDismissedResponse:
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )

    if member.entlassen:
        return MemberDismissedResponse(
            id=member.id,
            cn=member.cn,
            org_id=member.org_id,
        )

    parent_cn = ""
    if member.parent_id and member.parent_id != 0:
        parent = db.get(Member, member.parent_id)
        if parent:
            parent_cn = parent.cn

    tree: dict[str, object] = {
        "children": [_build_tree_node(c).model_dump() for c in member.children],
        "ancestry": _build_ancestry(member),
    }

    return MemberDetailResponse(
        id=member.id,
        cn=member.cn,
        vortitel=member.vortitel,
        vorname=member.vorname,
        nachname=member.nachname,
        nachname_geburt=member.nachname_geburt,
        nachtitel=member.nachtitel,
        couleurname=member.couleurname,
        org_id=member.org_id,
        org_label=member.org.label if member.org else None,
        state_id=member.state_id,
        state_label=member.state.label if member.state else None,
        gruender=member.gruender or False,
        entlassen=member.entlassen or False,
        verstorben=member.verstorben or False,
        grabadresse=member.grabadresse,
        parent_id=member.parent_id or 0,
        parent_cn=parent_cn,
        default_image=member.default_image,
        chroniclemail=member.chroniclemail or False,
        auth_locked=member.auth_locked if member.auth_locked is not None else True,
        email=member.email,
        email_verified_at=(
            str(member.email_verified_at) if member.email_verified_at else None
        ),
        url=member.url,
        mkv_ogv_url=member.mkv_ogv_url,
        zustellungen=member.zustellungen or MemberDeliveryPreference.DEAKTIVIERT,
        rufnummer_mobil=member.rufnummer_mobil,
        rufnummer_privat=member.rufnummer_privat,
        rufnummer_beruf=member.rufnummer_beruf,
        adresse_privat_anschrift=(member.adresse_privat_anschrift),
        adresse_privat_plz=member.adresse_privat_plz,
        adresse_privat_ort=member.adresse_privat_ort,
        adresse_privat_land=(member.adresse_privat_land),
        adresse_beruf_anschrift=(member.adresse_beruf_anschrift),
        adresse_beruf_plz=member.adresse_beruf_plz,
        adresse_beruf_ort=member.adresse_beruf_ort,
        adresse_beruf_land=member.adresse_beruf_land,
        arbeitgeber=member.arbeitgeber,
        taetigkeit=member.taetigkeit,
        mitgliedschaften=member.mitgliedschaften,
        verbandchargen=member.verbandchargen,
        anmerkungen=member.anmerkungen,
        geburtsdatum=(str(member.geburtsdatum) if member.geburtsdatum else None),
        geburtsdatum_accuracy=(member.geburtsdatum_accuracy or 0),
        aufnahmedatum=(str(member.aufnahmedatum) if member.aufnahmedatum else None),
        aufnahmedatum_accuracy=(member.aufnahmedatum_accuracy or 0),
        branderdatum=(str(member.branderdatum) if member.branderdatum else None),
        branderdatum_accuracy=(member.branderdatum_accuracy or 0),
        burschungsdatum=(
            str(member.burschungsdatum) if member.burschungsdatum else None
        ),
        burschungsdatum_accuracy=(member.burschungsdatum_accuracy or 0),
        philistrierungsdatum=(
            str(member.philistrierungsdatum) if member.philistrierungsdatum else None
        ),
        philistrierungsdatum_accuracy=(member.philistrierungsdatum_accuracy or 0),
        entlassungsdatum=(
            str(member.entlassungsdatum) if member.entlassungsdatum else None
        ),
        entlassungsdatum_accuracy=(member.entlassungsdatum_accuracy or 0),
        sterbedatum=(str(member.sterbedatum) if member.sterbedatum else None),
        sterbedatum_accuracy=(member.sterbedatum_accuracy or 0),
        roles_history=_build_roles_list(member),
        badges=_build_badges_list(member),
        keys=_build_keys_list(member),
        tree=tree,
    )


# Treat all "empty" states as equivalent for change detection
_EMPTY_VALUES = {None, False, "", "deaktiviert"}


def _values_differ(old_val: object, new_val: object) -> bool:
    if old_val == new_val:
        return False
    return not (old_val in _EMPTY_VALUES and new_val in _EMPTY_VALUES)


def _serialize_log_value(val: object) -> str | None:
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return json.dumps(val, ensure_ascii=False, default=str)
    return str(val)


def _persist_change_log(
    db: Session,
    log_model: type,
    fk_field: str,
    entity_id: int,
    diff: dict[str, dict[str, object]],
    action: str,
    modified_by: int,
    modified_at: datetime,
) -> None:
    for key, change in diff.items():
        db.add(
            log_model(
                **{fk_field: entity_id},
                action=action,
                key=key,
                old=_serialize_log_value(change["old"]),
                new=_serialize_log_value(change["new"]),
                modified_by=modified_by,
                modified_at=modified_at,
            )
        )


# --- Member Save ---


def _normalize_member_input(input_dict: dict[str, object]) -> None:
    # The API contract uses 0 as the "no parent" sentinel (matches how
    # MemberDetailResponse serializes it back out via `parent_id or 0`),
    # but parent_id is a nullable self-referencing FK — 0 is never a
    # valid member id and violates the FK constraint once enforced (see
    # scripts/migration_archive/sqlite2pg.py's _fix_known_legacy_data_issues
    # for the same issue in migrated legacy data).
    if input_dict.get("parent_id") == 0:
        input_dict["parent_id"] = None

    if input_dict["entlassen"] or input_dict["verstorben"]:
        input_dict["auth_locked"] = True
        input_dict["zustellungen"] = "deaktiviert"
        input_dict["chroniclemail"] = False

    if not input_dict.get("email"):
        input_dict["auth_locked"] = True

    if not input_dict["entlassen"]:
        input_dict["entlassungsdatum"] = None
        input_dict["entlassungsdatum_accuracy"] = 0

    if not input_dict["verstorben"]:
        input_dict["sterbedatum"] = None
        input_dict["sterbedatum_accuracy"] = 0
        input_dict["grabadresse"] = None


def _apply_field_changes(
    member: Member,
    input_dict: dict[str, object],
) -> dict[str, dict[str, object]]:
    diff: dict[str, dict[str, object]] = {}
    for field, new_val in input_dict.items():
        old_val = getattr(member, field, None)
        if _values_differ(old_val, new_val):
            diff[field] = {"old": old_val, "new": new_val}
            setattr(member, field, new_val)
        elif old_val != new_val:
            setattr(member, field, new_val)
    return diff


def apply_member_input(
    db: Session,
    member: Member,
    data: MemberSaveRequest,
    current_user: Member,
) -> dict[str, dict[str, object]]:
    now = datetime.now(UTC)
    is_new = sa_inspect(member).transient

    badges_entries = data.badges
    keys_entries = data.keys
    roles_entries = data.roles_history

    input_dict: dict[str, object] = data.model_dump(
        exclude={"roles_history", "badges", "keys"}
    )
    _normalize_member_input(input_dict)

    if "email" in input_dict and member.email != input_dict["email"]:
        member.email_verified_at = None

    diff = _apply_field_changes(member, input_dict)

    member.modified_at = now
    member.modified_by = current_user.id

    db.add(member)
    db.flush()

    _sync_badges(db, member, badges_entries, diff)
    _sync_keys(db, member, keys_entries, diff)

    validate_roles_history(db, roles_entries, member.org_id or "", member.id)
    _sync_roles(db, member, roles_entries, diff)

    db.commit()
    db.refresh(member)

    if diff:
        _persist_change_log(
            db,
            MembersLog,
            "member_id",
            member.id,
            diff,
            "create" if is_new else "update",
            current_user.id,
            now,
        )
        db.commit()

    return diff


def _sync_badges(
    db: Session,
    member: Member,
    badges_input: list[BadgeEntry],
    diff: dict[str, dict[str, object]],
) -> None:
    def _badge_sort_key(x: dict[str, int | str | None]) -> int:
        val = x.get("id", 0)
        return int(str(val)) if val is not None else 0

    old: list[dict[str, int | str | None]] = sorted(
        [
            {
                "id": mb.badge_id,
                "presentationdate": (
                    str(mb.presentationdate) if mb.presentationdate else None
                ),
                "presentationdate_accuracy": (mb.presentationdate_accuracy or 0),
            }
            for mb in member.member_badges
        ],
        key=_badge_sort_key,
    )
    new: list[dict[str, int | str | None]] = sorted(
        [
            {
                "id": b.id,
                "presentationdate": (
                    str(b.presentationdate) if b.presentationdate else None
                ),
                "presentationdate_accuracy": b.presentationdate_accuracy,
            }
            for b in badges_input
        ],
        key=_badge_sort_key,
    )

    if old != new:
        diff["badges"] = {"old": old, "new": new}

    db.query(MemberBadge).filter(MemberBadge.member_id == member.id).delete()
    db.flush()

    for b in badges_input:
        db.add(
            MemberBadge(
                member_id=member.id,
                badge_id=b.id,
                presentationdate=b.presentationdate,
                presentationdate_accuracy=b.presentationdate_accuracy,
            )
        )


def _sync_keys(
    db: Session,
    member: Member,
    keys_input: list[KeyEntry],
    diff: dict[str, dict[str, object]],
) -> None:
    def _key_sort_key(x: dict[str, int | str | None]) -> int:
        val = x.get("id", 0)
        return int(str(val)) if val is not None else 0

    old: list[dict[str, int | str | None]] = sorted(
        [
            {
                "id": mk.key_id,
                "presentationdate": (
                    str(mk.presentationdate) if mk.presentationdate else None
                ),
                "presentationdate_accuracy": (mk.presentationdate_accuracy or 0),
            }
            for mk in member.member_keys
        ],
        key=_key_sort_key,
    )
    new: list[dict[str, int | str | None]] = sorted(
        [
            {
                "id": k.id,
                "presentationdate": (
                    str(k.presentationdate) if k.presentationdate else None
                ),
                "presentationdate_accuracy": k.presentationdate_accuracy,
            }
            for k in keys_input
        ],
        key=_key_sort_key,
    )

    if old != new:
        diff["keys"] = {"old": old, "new": new}

    db.query(MemberKey).filter(MemberKey.member_id == member.id).delete()
    db.flush()

    for k in keys_input:
        db.add(
            MemberKey(
                member_id=member.id,
                key_id=k.id,
                presentationdate=k.presentationdate,
                presentationdate_accuracy=k.presentationdate_accuracy,
            )
        )


def _sync_roles(
    db: Session,
    member: Member,
    roles_input: list[RoleHistoryEntry],
    diff: dict[str, dict[str, object]],
) -> None:
    old: list[dict[str, str | None]] = sorted(
        [
            {
                "id": mr.role_id,
                "startdate": str(mr.startdate),
                "enddate": (str(mr.enddate) if mr.enddate else None),
            }
            for mr in member.member_roles
        ],
        key=lambda x: (x.get("id") or "", x.get("startdate") or ""),
    )
    new: list[dict[str, str | None]] = sorted(
        [
            {
                "id": r.id,
                "startdate": str(r.startdate),
                "enddate": (str(r.enddate) if r.enddate else None),
            }
            for r in roles_input
        ],
        key=lambda x: (x.get("id") or "", x.get("startdate") or ""),
    )

    if old != new:
        diff["roles_history"] = {
            "old": old,
            "new": new,
        }

    db.query(MemberRole).filter(MemberRole.member_id == member.id).delete()
    db.flush()

    for r in roles_input:
        db.add(
            MemberRole(
                member_id=member.id,
                role_id=r.id,
                startdate=r.startdate,
                enddate=r.enddate,
            )
        )


# --- Validation Helpers ---


def validate_member_org(data: MemberSaveRequest, current_user: Member) -> None:
    if data.org_id != current_user.org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("Du kannst nur Mitglieder deiner eigenen Organisation verwalten."),
        )


def validate_member_uniqueness(
    db: Session,
    data: MemberSaveRequest,
    exclude_id: int | None = None,
) -> None:
    q = db.query(Member).filter(
        func.lower(func.coalesce(Member.vorname, "")) == func.lower(data.vorname or ""),
        func.lower(func.coalesce(Member.nachname, ""))
        == func.lower(data.nachname or ""),
        func.lower(func.coalesce(Member.couleurname, ""))
        == func.lower(data.couleurname or ""),
    )
    if exclude_id:
        q = q.filter(Member.id != exclude_id)
    if q.first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Ein Mitglied mit diesem Namen existiert bereits."),
        )


def validate_parent_id(
    db: Session,
    parent_id: int,
    org_id: str,
    member_id: int | None = None,
) -> None:
    if parent_id == 0:
        return

    parent = db.get(Member, parent_id)
    if not parent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ungültiges Leibverhältnis.",
        )

    if parent.org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ungültiges Leibverhältnis.",
        )

    if member_id and parent_id == member_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ungültiges Leibverhältnis.",
        )

    if member_id:
        child_ids = db.query(Member.id).filter(Member.parent_id == member_id).all()
        if parent_id in [c[0] for c in child_ids]:
            raise HTTPException(
                status_code=(status.HTTP_400_BAD_REQUEST),
                detail="Ungültiges Leibverhältnis.",
            )


def _validate_state_ref(db: Session, state_id: str | None) -> None:
    if not state_id:
        return
    if not db.query(State).filter_by(id=state_id).first():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Ungültiger Status.",
        )


def _validate_ids_exist(
    db: Session,
    model: type,
    entries: list[RoleHistoryEntry] | list[BadgeEntry] | list[KeyEntry],
    label: str,
) -> None:
    valid_ids = {row.id for row in db.query(model).all()}
    for entry in entries:
        if entry.id not in valid_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{label}: {entry.id}",
            )


def validate_member_references(
    db: Session,
    data: "MemberSaveRequest",
) -> None:
    _validate_state_ref(db, data.state_id)
    _validate_ids_exist(db, Role, data.roles_history, "Ungültige Rolle")
    _validate_ids_exist(db, Badge, data.badges, "Ungültiges Abzeichen")
    _validate_ids_exist(db, Key, data.keys, "Ungültiger Schlüssel")


def _ranges_overlap(
    start1: date,
    end1: date | None,
    start2: date,
    end2: date | None,
) -> bool:
    if end1 and end2:
        return start1 < end2 and end1 > start2
    if end1 and not end2:
        return end1 > start2
    if not end1 and end2:
        return start1 < end2
    # Two open-ended ranges always overlap
    return True


def _format_date(d: date | None) -> str:
    if not d:
        return "laufend"
    months = [
        "",
        "Jänner",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ]
    return f"{d.day}. {months[d.month]} {d.year}"


def validate_roles_history(  # noqa: C901, PLR0912
    db: Session,
    roles_input: list[RoleHistoryEntry] | list[dict[str, object]],
    org_id: str,
    member_id: int | None,
) -> None:
    if not roles_input:
        return

    entries = [
        e if isinstance(e, RoleHistoryEntry) else RoleHistoryEntry.model_validate(e)
        for e in roles_input
    ]

    role_labels: dict[str, str] = {}
    all_roles = db.query(Role).all()
    for r in all_roles:
        role_labels[r.id] = r.label or r.id

    errors: list[str] = []

    entry_labels: dict[int, str] = {}
    for idx, entry in enumerate(entries):
        label = entry.label or role_labels.get(entry.id, entry.id)
        entry_labels[idx] = label
        if entry.enddate and entry.startdate >= entry.enddate:
            errors.append(f'Startdatum von Rolle "{label}" muss vor Enddatum liegen.')

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=errors,
        )

    by_role: dict[str, list[tuple[int, RoleHistoryEntry]]] = {}
    for idx, entry in enumerate(entries):
        by_role.setdefault(entry.id, []).append((idx, entry))

    for role_group in by_role.values():
        if len(role_group) < 2:
            continue
        for (idx_a, a), (_idx_b, b) in combinations(role_group, 2):
            if _ranges_overlap(
                a.startdate,
                a.enddate,
                b.startdate,
                b.enddate,
            ):
                errors.append(
                    f'Die Zeiträume für die Rolle "{entry_labels[idx_a]}"'
                    " überschneiden sich."
                )
                break

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=errors,
        )

    for idx, entry in enumerate(entries):
        # Check if role is already held by another member in overlapping period
        query = (
            db.query(MemberRole)
            .join(Member, Member.id == MemberRole.member_id)
            .filter(
                MemberRole.role_id == entry.id,
                Member.org_id == org_id,
            )
        )
        if member_id:
            query = query.filter(MemberRole.member_id != member_id)

        start = entry.startdate
        end = entry.enddate

        if end:
            query = query.filter(
                ((MemberRole.startdate < end) & (MemberRole.enddate > start))
                | ((MemberRole.startdate < end) & (MemberRole.enddate.is_(None)))
            )
        else:
            query = query.filter(
                (MemberRole.enddate > start) | (MemberRole.enddate.is_(None))
            )

        for overlap in query.all():
            owner = db.get(Member, overlap.member_id)
            if not owner:
                continue
            label = entry_labels[idx]
            errors.append(
                f'Der Zeitraum für die Rolle "{label}"'
                f" ({_format_date(start)}"
                f" - {_format_date(end)})"
                f' ist durch "{owner.cn}" belegt'
                f" ({_format_date(overlap.startdate)}"
                f" - {_format_date(overlap.enddate)})."
            )
            break

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=errors,
        )


def search_parent(
    db: Session,
    member_id: int,
    term: str,
) -> list[dict[str, int | str]]:
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mitglied nicht gefunden.",
        )

    results = (
        db.query(Member)
        .filter(
            Member.org_id == member.org_id,
            Member.id != member.id,
            # NULL-safe: plain `!=` evaluates to UNKNOWN (excludes the row)
            # for parent_id IS NULL, even though "no parent" trivially
            # isn't equal to member.id and should stay a valid candidate.
            Member.parent_id.is_distinct_from(member.id),
            (Member.vorname.ilike(f"%{term}%"))
            | (Member.nachname.ilike(f"%{term}%"))
            | (Member.couleurname.ilike(f"%{term}%")),
        )
        .all()
    )

    return [{"id": m.id, "cn": m.cn} for m in results]


# --- Contact Detail ---


def get_contact_detail(db: Session, contact_id: int) -> ContactDetailResponse:
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kontakt nicht gefunden.",
        )

    return ContactDetailResponse(
        id=contact.id,
        cn=contact.cn,
        kontakttyp=contact.kontakttyp,
        anrede=contact.anrede,
        name=contact.name,
        couleurname=contact.couleurname,
        org_id=contact.org_id,
        org_label=contact.org.label if contact.org else None,
        adresse_anschrift=contact.adresse_anschrift,
        adresse_plz=contact.adresse_plz,
        adresse_ort=contact.adresse_ort,
        adresse_land=contact.adresse_land,
        zustellungen=contact.zustellungen or False,
        email=contact.email,
        rufnummer=contact.rufnummer,
        datum=(str(contact.datum) if contact.datum else None),
        datum_accuracy=contact.datum_accuracy or 0,
        default_image=contact.default_image,
        anmerkungen=contact.anmerkungen,
    )


# --- Contact Save ---


def apply_contact_input(
    db: Session,
    contact: Contact,
    data: dict[str, object],
    current_user: Member,
) -> dict[str, dict[str, object]]:
    now = datetime.now(UTC)
    is_new = sa_inspect(contact).transient
    diff: dict[str, dict[str, object]] = {}

    for field, new_val in data.items():
        old_val = getattr(contact, field, None)
        if _values_differ(old_val, new_val):
            diff[field] = {
                "old": old_val,
                "new": new_val,
            }
            setattr(contact, field, new_val)
        elif old_val != new_val:
            setattr(contact, field, new_val)

    contact.modified_at = now
    contact.modified_by = current_user.id

    db.add(contact)
    db.commit()
    db.refresh(contact)

    if diff:
        _persist_change_log(
            db,
            ContactsLog,
            "contact_id",
            contact.id,
            diff,
            "create" if is_new else "update",
            current_user.id,
            now,
        )
        db.commit()

    return diff


def soft_delete_contact(
    db: Session,
    contact: Contact,
    current_user: Member,
) -> None:
    now = datetime.now(UTC)
    contact.deleted_at = now
    contact.modified_at = now
    contact.modified_by = current_user.id
    db.add(
        ContactsLog(
            contact_id=contact.id,
            action="delete",
            key="deleted_at",
            old=None,
            new=str(now),
            modified_by=current_user.id,
            modified_at=now,
        )
    )
    db.commit()


def validate_contact_uniqueness(
    db: Session,
    name: str,
    exclude_id: int | None = None,
) -> None:
    q = db.query(Contact).filter(
        Contact.deleted_at.is_(None),
        func.lower(Contact.name) == func.lower(name),
    )
    if exclude_id:
        q = q.filter(Contact.id != exclude_id)
    if q.first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Ein Kontakt mit diesem Namen existiert bereits."),
        )


# --- Reference Data ---


def get_roles_list(
    db: Session,
    year: int | None = None,
    semester: str | None = None,
) -> dict[str, object]:
    now = datetime.now(UTC).date()
    if year and semester:
        if semester == "ws":
            start = date(year, 8, 1)
            end = date(year + 1, 1, 31)
        else:
            start = date(year, 2, 1)
            end = date(year, 7, 31)
    else:
        semester = "ss" if 2 <= now.month <= 7 else "ws"
        year = now.year
        start = None
        end = None

    roles = db.query(Role).order_by(Role.order).all()

    result = []
    for role in roles:
        query = db.query(MemberRole).join(Member).filter(MemberRole.role_id == role.id)
        if start and end:
            query = query.filter(
                MemberRole.startdate < end,
                (MemberRole.enddate > start) | (MemberRole.enddate.is_(None)),
            )
        else:
            query = query.filter(
                MemberRole.startdate < now,
                (MemberRole.enddate > now) | (MemberRole.enddate.is_(None)),
            )
        assignments = query.order_by(MemberRole.startdate).all()

        vbw = next(
            (
                {
                    "id": a.member.id,
                    "cn": a.member.cn,
                    "startdate": a.startdate,
                    "enddate": a.enddate,
                }
                for a in assignments
                if a.member.org_id == "vbw"
            ),
            None,
        )
        vbn = next(
            (
                {
                    "id": a.member.id,
                    "cn": a.member.cn,
                    "startdate": a.startdate,
                    "enddate": a.enddate,
                }
                for a in assignments
                if a.member.org_id == "vbn"
            ),
            None,
        )
        result.append(
            {
                "label": role.label,
                "group": role.group,
                "vbw": vbw,
                "vbn": vbn,
            }
        )

    return {
        "semester": semester,
        "year": year,
        "roles": result,
    }


def get_reference_data(db: Session) -> dict[str, object]:
    return {
        "orgs": db.query(Org).order_by(Org.order).all(),
        "states": (db.query(State).order_by(State.order).all()),
        "roles": (db.query(Role).order_by(Role.order).all()),
        "badges": (db.query(Badge).order_by(Badge.order).all()),
        "keys": db.query(Key).all(),
    }


def _build_keys_data(
    db: Session,
) -> tuple[list[str | None], list[dict[str, object]]]:
    """Build key list data used by both API and download."""
    all_keys = db.query(Key).all()
    key_names: list[str | None] = [k.name for k in all_keys]

    members = (
        db.query(Member)
        .filter(Member.member_keys.any())
        .order_by(Member.nachname)
        .all()
    )

    key_id_to_name = {k.id: k.name for k in all_keys}
    result: list[dict[str, object]] = []
    for m in members:
        held = {
            key_id_to_name[mk.key_id]
            for mk in m.member_keys
            if mk.key_id in key_id_to_name
        }
        result.append(
            {
                "id": m.id,
                "nachname": m.nachname,
                "vorname": m.vorname,
                "keys": {name: name in held for name in key_names},
            }
        )

    return key_names, result


def get_keys_list(db: Session) -> dict[str, object]:
    key_names, members = _build_keys_data(db)
    return {"key_names": key_names, "members": members}


def generate_keys_download(db: Session) -> bytes:
    key_names, members = _build_keys_data(db)
    lines: list[str] = []
    for m in members:
        keys_map = m.get("keys")
        held = [
            str(name)
            for name in key_names
            if isinstance(keys_map, dict) and keys_map.get(name)
        ]
        lines.append(
            f"{m.get('nachname', '')}, {m.get('vorname', '')}: {', '.join(held)}"
        )
    return "\n".join(lines).encode("utf-8")


# --- Changelog ---


def _changelog_name_map(
    db: Session,
    log_entries: Sequence[MembersLog | ContactsLog],
) -> dict[int, str]:
    ids = {e.modified_by for e in log_entries if e.modified_by}
    if not ids:
        return {}
    rows = (
        db.query(Member.id, Member.vorname, Member.nachname)
        .filter(Member.id.in_(ids))
        .all()
    )
    return {r.id: f"{r.vorname or ''} {r.nachname or ''}".strip() for r in rows}


def get_member_changelog(db: Session, member_id: int) -> list[ChangeLogEntry]:
    """Return the change history for a member."""
    logs = (
        db.query(MembersLog)
        .filter(MembersLog.member_id == member_id)
        .order_by(MembersLog.modified_at.desc())
        .limit(200)
        .all()
    )
    names = _changelog_name_map(db, logs)
    return [
        ChangeLogEntry(
            id=e.id,
            modified_at=e.modified_at,
            modified_by_name=names.get(e.modified_by) if e.modified_by else None,
            action=e.action,
            key=e.key,
            old=e.old,
            new=e.new,
        )
        for e in logs
    ]


def get_contact_changelog(db: Session, contact_id: int) -> list[ChangeLogEntry]:
    """Return the change history for a contact."""
    logs = (
        db.query(ContactsLog)
        .filter(ContactsLog.contact_id == contact_id)
        .order_by(ContactsLog.modified_at.desc())
        .limit(200)
        .all()
    )
    names = _changelog_name_map(db, logs)
    return [
        ChangeLogEntry(
            id=e.id,
            modified_at=e.modified_at,
            modified_by_name=names.get(e.modified_by) if e.modified_by else None,
            action=e.action,
            key=e.key,
            old=e.old,
            new=e.new,
        )
        for e in logs
    ]
