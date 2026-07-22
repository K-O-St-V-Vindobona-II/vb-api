"""Proves the CHECK constraints added in Alembic revision cb250c054945 are
actually enforced by the database, not just declared in the ORM models.
Covers one representative case per constraint *shape* (nullable numeric
bound, string length range, regex format, same-table date/amount ordering)
rather than every single one of the 34 constraints — they're all created
the same mechanical way via op.create_check_constraint, so one proof per
shape is enough evidence the mechanism works."""

import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.archive_dir import ArchiveDir
from app.models.archive_file import ArchiveFile
from app.models.archive_file_comment import ArchiveFileComment
from app.models.archive_store_item import ArchiveStoreItem
from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.p4x_account import P4xAccount
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_fee import P4xFee
from app.models.p4x_summary_order import P4xSummaryOrder
from app.models.public_gallery_image import PublicGalleryImage
from app.models.role import Role
from app.models.standesdb_image import StandesdbImage


def _seed_account_and_category(db) -> tuple[P4xAccount, P4xCategory]:
    account = P4xAccount(iban="AT611904300234573201")
    category = P4xCategory(
        name="test-category", label="Test", background_color="#fff", text_color="#000"
    )
    db.add_all([account, category])
    db.commit()
    return account, category


class TestFuzzyDateAccuracyRange:
    """members.geburtsdatum_accuracy (and the identical pattern on 9 other
    columns/tables) must stay within the 0-3 domain used by
    format_fuzzy_date()/_format_date_by_accuracy()."""

    def test_out_of_range_accuracy_is_rejected(self, db_session):
        db_session.add(Member(nachname="Test", geburtsdatum_accuracy=4))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_boundary_accuracy_of_3_is_allowed(self, db_session):
        db_session.add(Member(nachname="Test", geburtsdatum_accuracy=3))
        db_session.commit()

    def test_null_accuracy_is_allowed(self, db_session):
        db_session.add(Member(nachname="Test", geburtsdatum_accuracy=None))
        db_session.commit()


class TestMemberRoleDateOrdering:
    """members_roles.startdate must be strictly before enddate, mirroring
    the check in standesdb_service.py."""

    def _seed_member_and_role(self, db) -> tuple[Member, Role]:
        member = Member(nachname="Test")
        role = Role(id="check-test-role", label="Check Test Role")
        db.add_all([member, role])
        db.commit()
        return member, role

    def test_startdate_not_before_enddate_is_rejected(self, db_session):
        member, role = self._seed_member_and_role(db_session)
        db_session.add(
            MemberRole(
                member_id=member.id,
                role_id=role.id,
                startdate=datetime.date(2020, 6, 15),
                enddate=datetime.date(2020, 6, 15),
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_startdate_one_day_before_enddate_is_allowed(self, db_session):
        member, role = self._seed_member_and_role(db_session)
        db_session.add(
            MemberRole(
                member_id=member.id,
                role_id=role.id,
                startdate=datetime.date(2020, 6, 14),
                enddate=datetime.date(2020, 6, 15),
            )
        )
        db_session.commit()

    def test_null_enddate_is_allowed(self, db_session):
        member, role = self._seed_member_and_role(db_session)
        db_session.add(
            MemberRole(
                member_id=member.id,
                role_id=role.id,
                startdate=datetime.date(2020, 6, 14),
            )
        )
        db_session.commit()


class TestSummaryOrderDateOrdering:
    def test_end_before_start_is_rejected(self, db_session):
        member = Member(nachname="Test")
        db_session.add(member)
        db_session.commit()

        db_session.add(
            P4xSummaryOrder(
                ordered_by=member.id,
                email="test@example.com",
                summary_start=datetime.date(2020, 6, 15),
                summary_end=datetime.date(2020, 6, 14),
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestNumericLowerBounds:
    def test_negative_archive_store_item_size_is_rejected(self, db_session):
        db_session.add(
            ArchiveStoreItem(
                name="f",
                extension="txt",
                mime_type="text/plain",
                size=-1,
                sha256_hash="a" * 64,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_negative_p4x_fee_is_rejected(self, db_session):
        db_session.add(P4xFee(start=datetime.date(2020, 1, 1), fee=-1))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_negative_public_gallery_sort_order_is_rejected(self, db_session):
        db_session.add(
            PublicGalleryImage(
                sha256_hash="b" * 64,
                extension="jpg",
                content_type="image/jpeg",
                size=100,
                width=10,
                height=10,
                sort_order=-1,
                created_at=datetime.datetime.now(datetime.UTC),
                updated_at=datetime.datetime.now(datetime.UTC),
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestNullableNumericBounds:
    """orgs.order (and the identical pattern on roles/states/badges) allows
    NULL but rejects negative values once set."""

    def test_negative_order_is_rejected(self, db_session):
        db_session.add(Org(id="check-test-org", order=-1))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_null_order_is_allowed(self, db_session):
        db_session.add(Org(id="check-test-org", order=None))
        db_session.commit()


class TestStringLengthRange:
    def test_archive_dir_name_shorter_than_3_chars_is_rejected(self, db_session):
        db_session.add(ArchiveDir(name="ab"))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_archive_dir_name_of_3_chars_is_allowed(self, db_session):
        db_session.add(ArchiveDir(name="abc"))
        db_session.commit()

    def test_empty_archive_file_comment_content_is_rejected(self, db_session):
        archive_file = ArchiveFile()
        db_session.add(archive_file)
        db_session.commit()

        db_session.add(ArchiveFileComment(archive_file_id=archive_file.id, content=""))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_null_archive_file_comment_content_is_allowed(self, db_session):
        archive_file = ArchiveFile()
        db_session.add(archive_file)
        db_session.commit()

        db_session.add(
            ArchiveFileComment(archive_file_id=archive_file.id, content=None)
        )
        db_session.commit()


class TestNullablePositiveOnly:
    def test_zero_standesdb_image_width_is_rejected(self, db_session):
        db_session.add(
            StandesdbImage(
                owner_type="member", owner_id=1, sha256_hash="c" * 64, width=0
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_null_standesdb_image_width_is_allowed(self, db_session):
        db_session.add(
            StandesdbImage(
                owner_type="member", owner_id=1, sha256_hash="d" * 64, width=None
            )
        )
        db_session.commit()


class TestRegexFormat:
    def test_malformed_iban_is_rejected(self, db_session):
        db_session.add(P4xAccount(iban="not-an-iban"))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_malformed_hex_color_is_rejected(self, db_session):
        db_session.add(
            P4xCategory(
                name="bad-color", label="Bad", background_color="red", text_color="#000"
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestCrossColumnAmountOrdering:
    def test_min_amount_greater_than_max_amount_is_rejected(self, db_session):
        account, category = _seed_account_and_category(db_session)
        db_session.add(
            P4xCategoryFilter(
                name="check-test-filter",
                p4x_account_id=account.id,
                subject_mode="contains",
                subject="x",
                p4x_category_id=category.id,
                min_amount=100,
                max_amount=-77,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_min_amount_equal_to_max_amount_is_allowed(self, db_session):
        account, category = _seed_account_and_category(db_session)
        db_session.add(
            P4xCategoryFilter(
                name="check-test-filter",
                p4x_account_id=account.id,
                subject_mode="contains",
                subject="x",
                p4x_category_id=category.id,
                min_amount=50,
                max_amount=50,
            )
        )
        db_session.commit()
