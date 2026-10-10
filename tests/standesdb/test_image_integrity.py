"""One owner cannot hold the same file twice or two default images."""

import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import Headers

from app.models.contact import Contact
from app.models.enums import ContactType
from app.models.member import Member
from app.models.org import Org
from app.models.standesdb_image import StandesdbImage
from app.models.state import State
from app.services import image_service


def _png(color=(10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, "PNG")
    return buffer.getvalue()


def _upload(db_session, owner_type, owner_id, content):
    storage = MagicMock()
    storage.exists.return_value = True
    file = UploadFile(
        file=io.BytesIO(content),
        filename="a.png",
        headers=Headers({"content-type": "image/png"}),
    )
    return image_service.upload_image(
        db_session,
        owner_type,
        owner_id,
        file,
        description=None,
        created_by=None,
        storage=storage,
    )


@pytest.fixture
def member(db_session):
    db_session.add_all(
        [
            Org(id="vbw", label="V", sort_order=1),
            State(id="up", label="U", sort_order=1),
        ]
    )
    member = Member(vorname="A", nachname="B", org_id="vbw", state_id="up")
    db_session.add(member)
    db_session.commit()
    return member


@pytest.fixture
def contact(db_session):
    contact = Contact(name="Kontakt", kontakttyp=ContactType.PERSON)
    db_session.add(contact)
    db_session.commit()
    return contact


class TestUpload:
    def test_the_same_image_twice_for_one_member_is_refused(self, db_session, member):
        _upload(db_session, "member", member.id, _png())

        with pytest.raises(HTTPException) as exc:
            _upload(db_session, "member", member.id, _png())

        assert exc.value.status_code == 422
        assert exc.value.detail == "Dieses Bild ist bereits vorhanden."
        assert db_session.query(StandesdbImage).count() == 1

    def test_the_same_image_twice_for_one_contact_is_refused(self, db_session, contact):
        _upload(db_session, "contact", contact.id, _png())

        with pytest.raises(HTTPException) as exc:
            _upload(db_session, "contact", contact.id, _png())

        assert exc.value.status_code == 422
        assert db_session.query(StandesdbImage).count() == 1

    def test_the_refused_duplicate_is_not_stored_in_the_bucket(
        self, db_session, member
    ):
        _upload(db_session, "member", member.id, _png())
        storage = MagicMock()
        storage.exists.return_value = False
        file = UploadFile(
            file=io.BytesIO(_png()),
            filename="a.png",
            headers=Headers({"content-type": "image/png"}),
        )

        with pytest.raises(HTTPException):
            image_service.upload_image(
                db_session,
                "member",
                member.id,
                file,
                description=None,
                created_by=None,
                storage=storage,
            )

        storage.upload.assert_not_called()

    def test_another_owner_may_hold_the_same_image(self, db_session, member, contact):
        _upload(db_session, "member", member.id, _png())
        _upload(db_session, "contact", contact.id, _png())

        assert db_session.query(StandesdbImage).count() == 2

    def test_a_deleted_image_can_be_uploaded_again(self, db_session, member):
        first = _upload(db_session, "member", member.id, _png())
        image_service.delete_image(db_session, first)

        again = _upload(db_session, "member", member.id, _png())

        assert again.is_default is True
        assert db_session.query(StandesdbImage).count() == 2

    def test_first_image_is_the_default_and_a_second_one_is_not(
        self, db_session, member
    ):
        first = _upload(db_session, "member", member.id, _png((1, 2, 3)))
        second = _upload(db_session, "member", member.id, _png((4, 5, 6)))

        assert (first.is_default, second.is_default) == (True, False)

    def test_setting_another_default_moves_it(self, db_session, member):
        first = _upload(db_session, "member", member.id, _png((1, 2, 3)))
        second = _upload(db_session, "member", member.id, _png((4, 5, 6)))

        image_service.update_image(db_session, second, None, set_default=True)

        db_session.refresh(first)
        assert (first.is_default, second.is_default) == (False, True)


class TestConcurrentDuplicate:
    """A double click sends the same file twice: both requests pass the check
    before either row exists, and the database refuses the second row."""

    def test_the_database_refusal_is_answered_like_the_check(self, db_session, member):
        _upload(db_session, "member", member.id, _png())
        real_check = image_service._refuse_duplicate
        calls = []

        def check_misses_the_first_time(active_images, sha256):
            calls.append(sha256)
            if len(calls) > 1:
                real_check(active_images, sha256)

        with (
            patch.object(
                image_service, "_refuse_duplicate", check_misses_the_first_time
            ),
            pytest.raises(HTTPException) as exc,
        ):
            _upload(db_session, "member", member.id, _png())

        assert exc.value.status_code == 422
        assert exc.value.detail == "Dieses Bild ist bereits vorhanden."
        assert db_session.query(StandesdbImage).count() == 1

    def test_another_integrity_error_is_not_hidden(self, db_session, member):
        failure = IntegrityError("INSERT", {}, Exception("other"))

        with (
            patch.object(db_session, "commit", side_effect=failure),
            pytest.raises(IntegrityError),
        ):
            _upload(db_session, "member", member.id, _png())


class TestDatabaseRules:
    def test_the_database_refuses_the_same_hash_for_one_member(
        self, db_session, member
    ):
        db_session.add(StandesdbImage(owner_member_id=member.id, sha256_hash="a" * 64))
        db_session.commit()

        db_session.add(StandesdbImage(owner_member_id=member.id, sha256_hash="a" * 64))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_the_database_refuses_the_same_hash_for_one_contact(
        self, db_session, contact
    ):
        db_session.add(
            StandesdbImage(owner_contact_id=contact.id, sha256_hash="a" * 64)
        )
        db_session.commit()

        db_session.add(
            StandesdbImage(owner_contact_id=contact.id, sha256_hash="a" * 64)
        )
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_a_deleted_image_does_not_block_the_same_file(self, db_session, member):
        db_session.add(StandesdbImage(owner_member_id=member.id, sha256_hash="a" * 64))
        db_session.commit()
        db_session.execute(text("UPDATE standesdb_images SET deleted_at = now()"))
        db_session.commit()

        db_session.add(StandesdbImage(owner_member_id=member.id, sha256_hash="a" * 64))
        db_session.commit()

    def test_two_default_images_for_one_member_are_refused(self, db_session, member):
        db_session.add(
            StandesdbImage(
                owner_member_id=member.id, sha256_hash="a" * 64, is_default=True
            )
        )
        db_session.commit()

        db_session.add(
            StandesdbImage(
                owner_member_id=member.id, sha256_hash="b" * 64, is_default=True
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_two_default_images_for_one_contact_are_refused(self, db_session, contact):
        db_session.add(
            StandesdbImage(
                owner_contact_id=contact.id, sha256_hash="a" * 64, is_default=True
            )
        )
        db_session.commit()

        db_session.add(
            StandesdbImage(
                owner_contact_id=contact.id, sha256_hash="b" * 64, is_default=True
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_a_deleted_default_does_not_block_a_new_one(self, db_session, member):
        db_session.add(
            StandesdbImage(
                owner_member_id=member.id, sha256_hash="a" * 64, is_default=True
            )
        )
        db_session.commit()
        db_session.execute(text("UPDATE standesdb_images SET deleted_at = now()"))
        db_session.commit()

        db_session.add(
            StandesdbImage(
                owner_member_id=member.id, sha256_hash="b" * 64, is_default=True
            )
        )
        db_session.commit()

    def test_every_owner_may_have_one_default(self, db_session, member, contact):
        db_session.add_all(
            [
                StandesdbImage(
                    owner_member_id=member.id, sha256_hash="a" * 64, is_default=True
                ),
                StandesdbImage(
                    owner_contact_id=contact.id, sha256_hash="a" * 64, is_default=True
                ),
            ]
        )

        db_session.commit()
