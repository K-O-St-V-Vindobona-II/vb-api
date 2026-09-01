"""Regression tests for Slice 5 of the backend remediation plan:
apply_member_input()/apply_contact_input() used to commit the entity
change first, then commit the change-log entry separately - two commits
for one logical save. The change-log entry is now persisted (via
db.add(), no commit) before the single final commit, so a failure
writing the change log rolls back the entity change too.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from app.models.contact import Contact
from app.models.member import Member
from app.models.members_log import MembersLog
from app.models.org import Org
from app.schemas.standesdb import MemberSaveRequest
from app.services import standesdb_service


def _seed_org(db) -> None:
    db.add(Org(id="vbw", label="VBW", order=1))
    db.commit()


class TestApplyMemberInputAtomicity:
    def test_rolls_back_field_change_if_change_log_persist_fails(self, db_session):
        _seed_org(db_session)
        admin = Member(email="changelog-admin@vbw.at", org_id="vbw")
        db_session.add(admin)
        db_session.commit()

        member = Member(email="changelog-target@vbw.at", org_id="vbw", vorname="Alt")
        db_session.add(member)
        db_session.commit()

        data = MemberSaveRequest(org_id="vbw", vorname="Neu", nachname="Nachname")

        with (
            patch(
                "app.services.standesdb_service._persist_change_log",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            standesdb_service.apply_member_input(db_session, member, data, admin)

        db_session.rollback()
        db_session.refresh(member)
        assert member.vorname == "Alt"


class TestApplyContactInputAtomicity:
    def test_rolls_back_field_change_if_change_log_persist_fails(self, db_session):
        _seed_org(db_session)
        admin = Member(email="changelog-admin2@vbw.at", org_id="vbw")
        db_session.add(admin)
        db_session.commit()

        contact = Contact(kontakttyp="person", name="Alt")
        db_session.add(contact)
        db_session.commit()

        with (
            patch(
                "app.services.standesdb_service._persist_change_log",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            standesdb_service.apply_contact_input(
                db_session, contact, {"name": "Neu"}, admin
            )

        db_session.rollback()
        db_session.refresh(contact)
        assert contact.name == "Alt"


class TestPersistChangeLogSkipsTechnicalKeys:
    def test_skips_technical_keys(self, db_session):
        """_persist_change_log must never write a row for a technical/audit
        bookkeeping key, even if a caller's diff dict somehow contains one
        (defense in depth alongside the read-side filter in
        get_member_changelog/get_contact_changelog)."""
        _seed_org(db_session)
        admin = Member(email="persist-guard-admin@vbw.at", org_id="vbw")
        db_session.add(admin)
        db_session.commit()

        member = Member(email="persist-guard-target@vbw.at", org_id="vbw")
        db_session.add(member)
        db_session.commit()

        diff = {
            "vorname": {"old": "Alt", "new": "Neu"},
            "auth_lastsignal": {"old": "a", "new": "b"},
            "updated_at": {"old": "a", "new": "b"},
        }

        standesdb_service._persist_change_log(
            db_session,
            MembersLog,
            "member_id",
            member.id,
            diff=diff,
            action="update",
            modified_by=admin.id_uuid,
            modified_at=datetime.now(UTC),
        )
        db_session.commit()

        logs = (
            db_session.query(MembersLog).filter(MembersLog.member_id == member.id).all()
        )
        assert len(logs) == 1
        assert logs[0].key == "vorname"
