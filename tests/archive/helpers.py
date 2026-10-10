"""Fixture-building helpers shared by the archive test suite."""

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import bcrypt

from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
from app.models.archive_permission import ArchivePermission
from app.models.archive_store_item import ArchiveStoreItem
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.role import Role
from app.models.state import State
from app.services.auth_service import create_user_session

if TYPE_CHECKING:
    import uuid


def _now() -> datetime:
    return datetime.now(UTC)


def _seed(db) -> None:
    db.add_all(
        [
            Org(id="vbw", label="VBW", sort_order=1),
            Org(id="vbn", label="VBN", sort_order=2),
            State(id="fu", label="Fux", sort_order=1),
            State(id="bi", label="Bandinhaber", sort_order=3),
        ]
    )
    db.commit()
    db.add_all(
        [
            Role(
                id="internetreferent",
                group="funktion",
                label="Internetreferent",
                sort_order=0,
            ),
            Role(
                id="x",
                group="chc",
                label="Senior",
                sort_order=1,
            ),
        ]
    )
    db.commit()


def _login_admin(db, _client) -> tuple[dict[str, str], Member]:
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="admin@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Admin",
        nachname="User",
        org_id="vbw",
        state_id="bi",
    )
    db.add(m)
    db.commit()
    db.add(
        MemberRole(
            member_id=m.id,
            role_id="internetreferent",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}, m


def _login_user(
    db,
    _client,
    org: str = "vbw",
    state: str = "fu",
) -> tuple[dict[str, str], Member]:
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email=f"{state}@{org}.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Normal",
        nachname="User",
        org_id=org,
        state_id=state,
    )
    db.add(m)
    db.commit()
    token, _, _ = create_user_session(db, m)
    return {"Authorization": f"Bearer {token}"}, m


def _make_dir(
    db,
    name: str,
    parent_id: uuid.UUID | None = None,
    perms: list[str] | None = None,
    recursive: bool = False,
) -> ArchiveDir:
    now = _now()
    d = ArchiveDir(
        name=name,
        archive_dir_id=parent_id,
        recursive_permissions=recursive,
        created_at=now,
        updated_at=now,
    )
    db.add(d)
    db.flush()
    for p in perms or []:
        parts = p.split("_")
        db.add(
            ArchivePermission(
                archive_dir_id=d.id,
                org_id=parts[0],
                state_id=parts[1],
            )
        )
    db.commit()
    return d


def _make_file(
    db,
    dir_id: uuid.UUID | None = None,
    desc: str = "test",
    extension: str = "jpg",
    mime_type: str = "image/jpeg",
    hash_suffix: str = "",
) -> ArchiveFile:
    now = _now()
    item = ArchiveStoreItem(
        name="testfile",
        extension=extension,
        mime_type=mime_type,
        size=5000,
        # dir_id is a full UUID string now, not a short int - left out here
        # since sha256_hash is varchar(64) and hash_suffix (always varied
        # by the caller) already disambiguates within a test.
        sha256_hash=f"hash_{now.timestamp()}_{hash_suffix}",
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.flush()
    f = ArchiveFile(
        archive_dir_id=dir_id,
        description=desc,
        archive_store_item_id=item.id,
        created_at=now,
        updated_at=now,
    )
    db.add(f)
    db.commit()
    return f
