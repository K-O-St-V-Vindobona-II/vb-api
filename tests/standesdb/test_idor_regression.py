"""Regression tests for Slice 6 of the backend remediation plan.

vbw and vbn deliberately share one read-only member/contact directory -
any authenticated member of either org can read the other org's entries.
This was flagged by an external review as IDOR-like, but is intentional
(see the code comments on get_member_detail/get_contact_detail). These
tests lock that intent in: a vbn member must be able to read vbw data
across every affected read endpoint, so a future accidental org-scoping
change gets caught immediately.
"""

import bcrypt

from app.models.contact import Contact
from app.models.member import Member
from app.models.org import Org
from app.services.auth_service import create_user_session


def _seed_orgs(db) -> None:
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
        ]
    )
    db.commit()


def _member(db, org_id: str, email: str) -> Member:
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email=email,
        auth_password=hashed,
        auth_locked=False,
        vorname="Test",
        nachname="User",
        org_id=org_id,
    )
    db.add(m)
    db.commit()
    return m


def _headers(db, member: Member) -> dict[str, str]:
    token, _, _ = create_user_session(db, member)
    return {"Authorization": f"Bearer {token}"}


class TestMemberDetailCrossOrgRead:
    def test_vbn_user_reads_vbw_member(self, client, db_session):
        _seed_orgs(db_session)
        vbw_target = _member(db_session, "vbw", "target@vbw.at")
        vbn_reader = _member(db_session, "vbn", "reader@vbn.at")
        headers = _headers(db_session, vbn_reader)

        resp = client.get(
            f"/api/standesdb/members/{vbw_target.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == vbw_target.id


class TestContactDetailCrossOrgRead:
    def test_vbn_user_reads_vbw_contact(self, client, db_session):
        _seed_orgs(db_session)
        vbn_reader = _member(db_session, "vbn", "reader2@vbn.at")
        headers = _headers(db_session, vbn_reader)

        contact = Contact(kontakttyp="person", name="VBW Kontakt", org_id="vbw")
        db_session.add(contact)
        db_session.commit()

        resp = client.get(
            f"/api/standesdb/contacts/{contact.id}",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == contact.id


class TestImageListCrossOrgRead:
    def test_vbn_user_lists_vbw_members_images(self, client, db_session):
        _seed_orgs(db_session)
        vbw_target = _member(db_session, "vbw", "target-img@vbw.at")
        vbn_reader = _member(db_session, "vbn", "reader-img@vbn.at")
        headers = _headers(db_session, vbn_reader)

        resp = client.get(
            f"/api/standesdb/members/{vbw_target.id}/images",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["owner"]["id"] == vbw_target.id

    def test_vbn_user_lists_vbw_contacts_images(self, client, db_session):
        _seed_orgs(db_session)
        vbn_reader = _member(db_session, "vbn", "reader-cimg@vbn.at")
        headers = _headers(db_session, vbn_reader)

        contact = Contact(kontakttyp="person", name="VBW Bildkontakt", org_id="vbw")
        db_session.add(contact)
        db_session.commit()

        resp = client.get(
            f"/api/standesdb/contacts/{contact.id}/images",
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["owner"]["id"] == contact.id


class TestSearchCrossOrgRead:
    def test_vbn_user_finds_vbw_member_in_search(self, client, db_session):
        _seed_orgs(db_session)
        db_session.add(
            Member(
                email="findme@vbw.at",
                org_id="vbw",
                vorname="Findbar",
                nachname="Sucheziel",
            )
        )
        db_session.commit()
        vbn_reader = _member(db_session, "vbn", "reader-search@vbn.at")
        headers = _headers(db_session, vbn_reader)

        resp = client.get(
            "/api/standesdb/search",
            params={"q": "Sucheziel"},
            headers=headers,
        )
        assert resp.status_code == 200
        results = resp.json()["data"]
        assert any("Sucheziel" in r.get("label", "") for r in results)
