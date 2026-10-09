"""The account status of a member (sign-in lock, verified address, chronicle
subscription) is shown only to the member and to the admins of the member's
organisation. The free-text note stays visible to every signed-in member."""

from datetime import UTC, datetime

import pytest

from app.models.member import Member
from tests.standesdb.test_standesdb_coverage import _admin, _headers, _seed

NOTE = "Hat den Literaturnobelpreis gewonnen"
ACCOUNT_FIELDS = ("auth_locked", "email_verified_at", "chroniclemail")


def _member(db, email: str, org: str = "vbw", locked: bool = True) -> Member:
    member = Member(
        email=email,
        vorname="Max",
        nachname="Muster",
        org_id=org,
        state_id="fu",
        auth_locked=locked,
        chroniclemail=True,
        email_verified_at=datetime(2026, 1, 2, tzinfo=UTC),
        anmerkungen=NOTE,
    )
    db.add(member)
    db.commit()
    return member


def _get(client, db, viewer: Member, target: Member) -> dict:
    response = client.get(
        f"/api/standesdb/members/{target.id}", headers=_headers(db, viewer)
    )
    assert response.status_code == 200
    return response.json()


class TestAccountFieldsAreHiddenFromOthers:
    def test_a_plain_member_does_not_see_them(self, client, db_session):
        _seed(db_session)
        target = _member(db_session, "target@vbw.at")
        viewer = _member(db_session, "viewer@vbw.at", locked=False)

        body = _get(client, db_session, viewer, target)

        assert {field: body[field] for field in ACCOUNT_FIELDS} == {
            "auth_locked": None,
            "email_verified_at": None,
            "chroniclemail": None,
        }

    def test_the_note_stays_visible_to_a_plain_member(self, client, db_session):
        _seed(db_session)
        target = _member(db_session, "target@vbw.at")
        viewer = _member(db_session, "viewer@vbw.at", locked=False)

        body = _get(client, db_session, viewer, target)

        assert body["anmerkungen"] == NOTE

    def test_the_admin_of_the_other_organisation_does_not_see_them(
        self, client, db_session
    ):
        _seed(db_session)
        target = _member(db_session, "target@vbw.at", org="vbw")
        other_admin = _admin(db_session, org="vbn")

        body = _get(client, db_session, other_admin, target)

        assert all(body[field] is None for field in ACCOUNT_FIELDS)

    def test_a_member_without_an_organisation_is_hidden_from_admins(
        self, client, db_session
    ):
        _seed(db_session)
        target = _member(db_session, "target@vbw.at")
        target.org_id = None
        db_session.commit()
        admin = _admin(db_session, org="vbw")

        body = _get(client, db_session, admin, target)

        assert all(body[field] is None for field in ACCOUNT_FIELDS)


class TestAccountFieldsAreShownToThoseWhoMaySee:
    def test_the_admin_of_the_members_organisation_sees_them(self, client, db_session):
        _seed(db_session)
        target = _member(db_session, "target@vbw.at")
        admin = _admin(db_session, org="vbw")

        body = _get(client, db_session, admin, target)

        assert body["auth_locked"] is True
        assert body["chroniclemail"] is True
        assert body["email_verified_at"] is not None

    def test_the_member_sees_their_own_status(self, client, db_session):
        _seed(db_session)
        member = _member(db_session, "me@vbw.at", locked=False)

        body = _get(client, db_session, member, member)

        assert body["auth_locked"] is False
        assert body["chroniclemail"] is True
        assert body["email_verified_at"] is not None

    @pytest.mark.parametrize("org", ["vbw", "vbn"])
    def test_each_organisation_admin_sees_their_own_members(
        self, client, db_session, org
    ):
        _seed(db_session)
        target = _member(db_session, f"target@{org}.at", org=org)
        admin = _admin(db_session, org=org)

        body = _get(client, db_session, admin, target)

        assert body["auth_locked"] is True
