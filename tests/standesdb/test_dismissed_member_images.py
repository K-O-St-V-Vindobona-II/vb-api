"""A dismissed member is a stub in the member detail; their images must not be
reachable through the image read routes either, while an administrator can still
remove a photograph."""

import uuid

import pytest

from tests.standesdb.test_image_gallery import (
    _admin,
    _headers,
    _make_jpeg,
    _nonadmin,
    _seed,
    _target_member,
)


def _member_with_image(client, db, admin_headers, *, dismissed: bool):
    member = _target_member(db)
    resp = client.post(
        f"/api/standesdb/members/{member.id}/images",
        headers=admin_headers,
        files={"file": ("test.jpg", _make_jpeg(), "image/jpeg")},
    )
    assert resp.status_code == 201
    member.entlassen = dismissed
    db.commit()
    return member, resp.json()["id"]


READ_ROUTES = [
    "/api/standesdb/members/{member}/images",
    "/api/standesdb/members/{member}/images/{image}/url",
    "/api/standesdb/members/{member}/images/{image}/url?thumb=true",
    "/api/standesdb/members/{member}/images/{image}/download",
]


class TestDismissedMemberImages:
    @pytest.mark.parametrize("route", READ_ROUTES)
    @pytest.mark.parametrize("as_admin", [True, False])
    def test_the_read_routes_treat_a_dismissed_member_as_absent(
        self, client, db_session, route, as_admin
    ):
        _seed(db_session)
        admin = _admin(db_session)
        admin_headers = _headers(client, db_session, admin)
        member, image = _member_with_image(
            client, db_session, admin_headers, dismissed=True
        )
        headers = (
            admin_headers
            if as_admin
            else _headers(client, db_session, _nonadmin(db_session))
        )

        resp = client.get(route.format(member=member.id, image=image), headers=headers)

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Mitglied nicht gefunden."

    @pytest.mark.parametrize("route", READ_ROUTES)
    def test_the_read_routes_do_not_find_an_unknown_member(
        self, client, db_session, route
    ):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))

        resp = client.get(
            route.format(member=uuid.uuid4(), image=uuid.uuid4()), headers=headers
        )

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Mitglied nicht gefunden."

    @pytest.mark.parametrize("route", READ_ROUTES)
    def test_the_read_routes_serve_an_active_member(self, client, db_session, route):
        _seed(db_session)
        admin = _admin(db_session)
        headers = _headers(client, db_session, admin)
        member, image = _member_with_image(client, db_session, headers, dismissed=False)

        resp = client.get(route.format(member=member.id, image=image), headers=headers)

        assert resp.status_code == 200

    def test_the_list_of_an_active_member_names_the_image(self, client, db_session):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))
        member, image = _member_with_image(client, db_session, headers, dismissed=False)

        resp = client.get(f"/api/standesdb/members/{member.id}/images", headers=headers)

        assert resp.json()["owner"]["id"] == str(member.id)
        assert [i["id"] for i in resp.json()["images"]] == [image]

    def test_the_detail_of_a_dismissed_member_stays_a_stub(self, client, db_session):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))
        member, _ = _member_with_image(client, db_session, headers, dismissed=True)

        resp = client.get(f"/api/standesdb/members/{member.id}", headers=headers)

        assert resp.status_code == 200
        assert set(resp.json()) == {"id", "cn", "org_id", "dataprotection"}

    def test_an_administrator_can_still_remove_the_photograph(self, client, db_session):
        _seed(db_session)
        headers = _headers(client, db_session, _admin(db_session))
        member, image = _member_with_image(client, db_session, headers, dismissed=True)

        resp = client.delete(
            f"/api/standesdb/members/{member.id}/images/{image}", headers=headers
        )

        assert resp.status_code == 204
