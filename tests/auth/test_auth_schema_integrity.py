"""Database-level guarantees of the authentication tables.

The application checks these rules too, but only the database can enforce
them against two concurrent requests: one external identity per member, a
session that always carries its timestamps, and a password-reset token that
cannot outlive its member or its e-mail address.
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import bcrypt
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.security import hash_reset_token
from app.models.auth_session import AuthSession
from app.models.enums import OauthProvider
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.members_oauth2binding import MembersOauth2Binding
from app.models.password_reset import PasswordResetToken
from app.models.role import Role
from app.services import auth_service


def _member(db, email: str) -> Member:
    member = Member(email=email, auth_locked=False)
    db.add(member)
    db.commit()
    return member


def _binding(member: Member, remote_id: str) -> MembersOauth2Binding:
    return MembersOauth2Binding(
        member_id=member.id,
        provider=OauthProvider.GOOGLE,
        remote_id=remote_id,
        remote_name="Test",
    )


class TestOauthBindingConstraints:
    def test_one_external_identity_cannot_belong_to_two_members(self, db_session):
        first = _member(db_session, "first@vindobona.at")
        second = _member(db_session, "second@vindobona.at")
        db_session.add(_binding(first, "shared-sub"))
        db_session.commit()

        db_session.add(_binding(second, "shared-sub"))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_a_member_has_at_most_one_identity_per_provider(self, db_session):
        member = _member(db_session, "single@vindobona.at")
        db_session.add(_binding(member, "sub-one"))
        db_session.commit()

        db_session.add(_binding(member, "sub-two"))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_unknown_provider_is_rejected(self, db_session):
        member = _member(db_session, "provider@vindobona.at")
        db_session.add(
            MembersOauth2Binding(
                member_id=member.id,
                provider="facebook",
                remote_id="fb-1",
                remote_name="Test",
            )
        )
        with pytest.raises(DBAPIError):
            db_session.commit()
        db_session.rollback()

    def test_timestamps_are_filled_by_the_database(self, db_session):
        member = _member(db_session, "stamps@vindobona.at")
        binding = _binding(member, "stamps-sub")
        db_session.add(binding)
        db_session.commit()

        assert binding.bound_at is not None
        assert binding.lastuse_at is not None

    def test_link_race_loser_gets_the_already_linked_answer(self, db_session):
        winner = _member(db_session, "winner@vindobona.at")
        loser = _member(db_session, "loser@vindobona.at")
        auth_service._insert_google_binding(db_session, winner.id, "race-sub", "Race")

        with pytest.raises(ValueError, match="bereits verknüpft"):
            auth_service._insert_google_binding(
                db_session, loser.id, "race-sub", "Race"
            )

        # The failed insert only rolled back its savepoint: the session is usable.
        assert db_session.query(MembersOauth2Binding).count() == 1

    @patch("app.services.auth_service.id_token.verify_oauth2_token")
    def test_link_without_a_subject_claim_is_refused(
        self, mock_verify, db_session, monkeypatch
    ):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "fake")
        member = _member(db_session, "nosub@vindobona.at")
        member.auth_password = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()
        db_session.commit()
        mock_verify.return_value = {"name": "No Subject"}

        with pytest.raises(ValueError, match="ungültig"):
            auth_service.link_google_account(
                db_session, "any-token", member.email, "secret"
            )

        assert db_session.query(MembersOauth2Binding).count() == 0


class TestSessionTimestamps:
    @pytest.mark.parametrize("column", ["created_at", "last_used_at", "updated_at"])
    def test_missing_lifetime_timestamps_are_rejected(self, db_session, column):
        member = _member(db_session, f"nulls-{column}@vindobona.at")
        # Raw SQL: the ORM leaves a None attribute out of the INSERT, which
        # would let the server default fill it in.
        statement = text(
            f"INSERT INTO sessions (member_id, jti, {column}) "  # noqa: S608
            "VALUES (:member_id, :jti, NULL)"
        )
        with pytest.raises(IntegrityError):
            db_session.execute(statement, {"member_id": member.id, "jti": "jti-null"})
        db_session.rollback()

    def test_database_supplies_the_timestamps(self, db_session):
        member = _member(db_session, "defaults@vindobona.at")
        session = AuthSession(member_id=member.id, jti="jti-defaults")
        db_session.add(session)
        db_session.commit()

        assert session.created_at is not None
        assert session.last_used_at is not None
        assert session.updated_at is not None


class TestPasswordResetTokenConstraints:
    def test_token_of_a_deleted_member_is_removed_with_it(self, db_session):
        member = _member(db_session, "gone@vindobona.at")
        db_session.add(
            PasswordResetToken(member_id=member.id, token_hash=hash_reset_token("t"))
        )
        db_session.commit()

        db_session.delete(member)
        db_session.commit()

        assert db_session.query(PasswordResetToken).count() == 0

    def test_a_member_has_at_most_one_pending_token(self, db_session):
        member = _member(db_session, "once@vindobona.at")
        db_session.add(
            PasswordResetToken(member_id=member.id, token_hash=hash_reset_token("a"))
        )
        db_session.commit()

        db_session.add(
            PasswordResetToken(member_id=member.id, token_hash=hash_reset_token("b"))
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_created_at_is_filled_by_the_database(self, db_session):
        member = _member(db_session, "stamped@vindobona.at")
        entry = PasswordResetToken(
            member_id=member.id, token_hash=hash_reset_token("t")
        )
        db_session.add(entry)
        db_session.commit()

        assert datetime.now(UTC) - entry.created_at < timedelta(minutes=1)

    def test_changing_the_email_address_deletes_the_pending_token(self, db_session):
        member = _member(db_session, "old-address@vindobona.at")
        db_session.add(
            PasswordResetToken(member_id=member.id, token_hash=hash_reset_token("t"))
        )
        db_session.commit()

        member.email = "new-address@vindobona.at"
        db_session.commit()

        assert db_session.query(PasswordResetToken).count() == 0

    def test_other_member_changes_keep_the_pending_token(self, db_session):
        member = _member(db_session, "same-address@vindobona.at")
        db_session.add(
            PasswordResetToken(member_id=member.id, token_hash=hash_reset_token("t"))
        )
        db_session.commit()

        member.vorname = "Changed"
        db_session.commit()

        assert db_session.query(PasswordResetToken).count() == 1

    def test_reset_for_a_changed_address_no_longer_works(self, db_session):
        member = _member(db_session, "mailed@vindobona.at")
        result = auth_service.process_forgot_password(db_session, member.email)
        assert result is not None
        member.email = "moved@vindobona.at"
        db_session.commit()

        for address in ("mailed@vindobona.at", "moved@vindobona.at"):
            with pytest.raises(ValueError, match="Ungültiger Token"):
                auth_service.execute_password_reset(
                    db_session, address, result[1], "a-brand-new-password"
                )


class TestDateChecksIgnoreTheSessionDateStyle:
    """The CHECK constraints compare dates as dates: `date::text` would depend
    on the session's DateStyle and reject valid intervals under `German`."""

    @pytest.fixture
    def holder(self, db_session):
        db_session.add(Role(id="datestyle-probe"))
        member = _member(db_session, "datestyle@vindobona.at")
        db_session.execute(text("SET LOCAL datestyle = 'German, DMY'"))
        return member

    def _assign(self, db, member, start: date, end: date | None) -> None:
        db.add(
            MemberRole(
                member_id=member.id,
                role_id="datestyle-probe",
                startdate=start,
                enddate=end,
            )
        )
        db.flush()

    def test_valid_interval_is_accepted_under_german_datestyle(
        self, db_session, holder
    ):
        self._assign(db_session, holder, date(2024, 1, 15), date(2024, 2, 1))

    def test_reversed_interval_is_rejected_under_german_datestyle(
        self, db_session, holder
    ):
        with pytest.raises(IntegrityError):
            self._assign(db_session, holder, date(2024, 2, 1), date(2024, 1, 15))
        db_session.rollback()
