"""Every way linking a Google account to a member can end."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import bcrypt
import pytest

from app.core.rate_limit import limiter
from app.models.enums import OauthProvider
from app.models.member import Member
from app.models.members_oauth2binding import MembersOauth2Binding

LINK_URL = "/api/auth/google/link"
PASSWORD = "secret"
ALREADY_LINKED = "Dieser Account oder dieses Google-Konto ist bereits verknüpft."


@pytest.fixture(autouse=True)
def _google_client(monkeypatch):
    limiter._storage.reset()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake-client-id")


def _member(db, email: str) -> Member:
    member = Member(
        email=email,
        auth_password=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode(),
        auth_locked=False,
    )
    db.add(member)
    db.commit()
    return member


def _bind(db, member: Member, google_id: str, **fields) -> MembersOauth2Binding:
    binding = MembersOauth2Binding(
        member_id=member.id,
        provider=OauthProvider.GOOGLE,
        remote_id=google_id,
        remote_name="Existing",
        **fields,
    )
    db.add(binding)
    db.commit()
    return binding


def _link(client, email: str, password: str = PASSWORD):
    return client.post(
        LINK_URL, json={"credential": "token", "email": email, "password": password}
    )


@patch("app.services.auth_service.id_token.verify_oauth2_token")
class TestLinkEnds:
    def test_a_new_link_is_stored_with_the_google_name(
        self, mock_verify, client, db_session
    ):
        member = _member(db_session, "new.link@vindobona.at")
        mock_verify.return_value = {"sub": "g-new", "name": "Google Name"}

        response = _link(client, member.email)

        binding = db_session.query(MembersOauth2Binding).one()
        assert response.status_code == 200
        assert (binding.member_id, binding.remote_id, binding.remote_name) == (
            member.id,
            "g-new",
            "Google Name",
        )

    def test_linking_the_same_identity_again_signs_in_and_keeps_one_binding(
        self, mock_verify, client, db_session
    ):
        member = _member(db_session, "again@vindobona.at")
        long_ago = datetime.now(UTC) - timedelta(days=30)
        binding = _bind(db_session, member, "g-same", lastuse_at=long_ago)
        mock_verify.return_value = {"sub": "g-same", "name": "Existing"}

        response = _link(client, member.email)

        db_session.expire_all()
        assert response.status_code == 200
        assert db_session.query(MembersOauth2Binding).count() == 1
        assert binding.lastuse_at > long_ago

    def test_a_google_account_of_another_member_is_refused(
        self, mock_verify, client, db_session
    ):
        owner = _member(db_session, "owner@vindobona.at")
        _bind(db_session, owner, "g-taken")
        member = _member(db_session, "wants.it@vindobona.at")
        mock_verify.return_value = {"sub": "g-taken", "name": "Taken"}

        response = _link(client, member.email)

        assert response.status_code == 401
        assert response.json()["detail"] == ALREADY_LINKED
        assert (
            db_session.query(MembersOauth2Binding)
            .filter_by(member_id=member.id)
            .count()
            == 0
        )

    def test_a_member_with_another_google_account_is_refused(
        self, mock_verify, client, db_session
    ):
        member = _member(db_session, "has.one@vindobona.at")
        _bind(db_session, member, "g-first")
        mock_verify.return_value = {"sub": "g-second", "name": "Second"}

        response = _link(client, member.email)

        assert response.status_code == 401
        assert response.json()["detail"] == ALREADY_LINKED
        remote_ids = [b.remote_id for b in db_session.query(MembersOauth2Binding)]
        assert remote_ids == ["g-first"]

    def test_a_wrong_password_is_refused_before_google_is_asked(
        self, mock_verify, client, db_session
    ):
        member = _member(db_session, "wrong.password@vindobona.at")

        response = _link(client, member.email, password="not-the-password")

        assert response.status_code == 401
        mock_verify.assert_not_called()

    def test_an_unknown_address_is_refused_before_google_is_asked(
        self, mock_verify, client
    ):
        response = _link(client, "nobody@vindobona.at")

        assert response.status_code == 401
        mock_verify.assert_not_called()
