from datetime import date

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.role import Role
from app.services.permission_service import calculate_permissions


def test_member_permissions_properties(db_session):
    """Tests the dynamic RBAC permission logic via the permission service."""
    # 1. Test VBN branch with 'standesfuehrer'
    user_vbn = Member(email="perms_vbn@vindobona.at", org_id="vbn")
    db_session.add(user_vbn)

    r1 = Role(id="standesfuehrer", group="it")
    db_session.add(r1)
    db_session.commit()

    mr1 = MemberRole(member=user_vbn, role=r1, startdate=date(2000, 1, 1))
    db_session.add(mr1)
    db_session.commit()
    db_session.refresh(user_vbn)

    perms_vbn = calculate_permissions(user_vbn)
    assert "standesdbVbnAdmin" in perms_vbn
    assert "standesdbExport" in perms_vbn

    # 2. Test VBW branch with 'philchc' and 'phil-xxxx'
    user_vbw = Member(email="perms_vbw@vindobona.at", org_id="vbw")
    db_session.add(user_vbw)
    db_session.commit()

    r2 = Role(id="phil-xxxx", group="philchc")
    db_session.add(r2)
    mr2 = MemberRole(member=user_vbw, role=r2, startdate=date(2000, 1, 1))
    db_session.add(mr2)
    db_session.commit()
    db_session.refresh(user_vbw)

    perms_vbw = calculate_permissions(user_vbw)
    assert "p4xView" in perms_vbw
    assert "p4xAdmin" in perms_vbw
    assert "keylist" in perms_vbw
    assert "standesdbExport" in perms_vbw
