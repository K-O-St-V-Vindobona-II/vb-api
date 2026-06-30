"""Tests for scheduled jobs — logic verification without email sending."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from app.core.scheduler import (
    _compute_anniversaries,
    _format_date_de,
    _parse_month_day,
    _parse_year,
    job_refresh_category_filter_hits,
)
from app.models.member import Member
from app.models.org import Org
from app.models.p4x_category import P4xCategory
from app.models.p4x_category_filter import P4xCategoryFilter
from app.models.p4x_category_filter_hit import (
    P4xCategoryFilterHit,
)
from app.models.p4x_transaction import P4xTransaction
from app.models.role import Role
from app.models.state import State


def _seed_base(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Org(id="vbn", label="VBN", order=2),
            State(id="fu", label="Fux", order=1),
            State(id="up", label="Urphilister", order=2),
            Role(
                id="phil-x",
                group="philchc",
                label="Phil-x",
                order=1,
            ),
        ]
    )
    db.commit()


class TestHelpers:
    def test_parse_month_day(self):
        assert _parse_month_day("1990-06-15") == (6, 15)
        assert _parse_month_day("2000-12-01") == (12, 1)

    def test_parse_year(self):
        assert _parse_year("1990-06-15") == 1990

    def test_format_date_de(self):
        assert _format_date_de(date(2026, 1, 5)) == "5. 1. 2026"


class TestRefreshCategoryFilterHits:
    def test_refresh_clears_and_reapplies(
        self,
        db_session,
    ):
        _seed_base(db_session)
        cat = P4xCategory(
            name="test",
            label="Test",
            background_color="#000",
            text_color="#fff",
        )
        db_session.add(cat)
        db_session.commit()

        db_session.add(
            P4xTransaction(
                sha256hash="abc123",
                p4x_account_id=1,
                booking=datetime.now(UTC).date(),
                valuation=datetime.now(UTC).date(),
                amount=100,
                subject="Mitgliedsbeitrag Test",
                iban="AT001234",
                deleted_at=None,
            )
        )
        db_session.commit()

        cf = P4xCategoryFilter(
            name="test-filter",
            p4x_account_id=1,
            p4x_category_id=cat.id,
            subject="Mitgliedsbeitrag",
            subject_mode="contains",
        )
        db_session.add(cf)
        db_session.commit()

        with (
            patch(
                "app.core.scheduler.SessionLocal",
                return_value=db_session,
            ),
            patch.object(db_session, "close"),
        ):
            job_refresh_category_filter_hits()

        hits = db_session.query(P4xCategoryFilterHit).all()
        assert len(hits) == 1
        assert hits[0].p4x_category_filter_id == cf.id


class TestComputeAnniversaries:
    def test_finds_birthday_in_coming_week(
        self,
        db_session,
    ):
        _seed_base(db_session)

        today = datetime.now(UTC).date()
        dow = today.isoweekday()
        week_start = today + timedelta(
            days=(8 - dow) % 7,
        )
        target = week_start + timedelta(days=2)

        m = Member(
            email="test@vbw.at",
            vorname="Test",
            nachname="User",
            couleurname="Testikus",
            org_id="vbw",
            state_id="fu",
            geburtsdatum=date(
                1990,
                target.month,
                target.day,
            ),
            geburtsdatum_accuracy=3,
            entlassen=False,
            verstorben=False,
        )
        db_session.add(m)
        db_session.commit()

        result = _compute_anniversaries(db_session)
        assert "vbw" in result
        assert "lebend" in result["vbw"]
        assert "geburtsdatum" in result["vbw"]["lebend"]
        entries = result["vbw"]["lebend"]["geburtsdatum"]
        assert len(entries) == 1
        assert entries[0]["cn"] == m.cn

    def test_ignores_low_accuracy(
        self,
        db_session,
    ):
        _seed_base(db_session)

        today = datetime.now(UTC).date()
        dow = today.isoweekday()
        week_start = today + timedelta(
            days=(8 - dow) % 7,
        )
        target = week_start

        m = Member(
            email="test@vbw.at",
            vorname="Test",
            nachname="User",
            org_id="vbw",
            state_id="fu",
            geburtsdatum=date(
                1990,
                target.month,
                target.day,
            ),
            geburtsdatum_accuracy=1,
            entlassen=False,
        )
        db_session.add(m)
        db_session.commit()

        result = _compute_anniversaries(db_session)
        assert not result

    def test_deceased_member_in_verstorben(
        self,
        db_session,
    ):
        _seed_base(db_session)

        today = datetime.now(UTC).date()
        dow = today.isoweekday()
        week_start = today + timedelta(
            days=(8 - dow) % 7,
        )
        target = week_start + timedelta(days=1)

        m = Member(
            email="test@vbw.at",
            vorname="Test",
            nachname="User",
            couleurname="Testikus",
            org_id="vbw",
            state_id="fu",
            geburtsdatum=date(
                1950,
                target.month,
                target.day,
            ),
            geburtsdatum_accuracy=3,
            entlassen=False,
            verstorben=True,
        )
        db_session.add(m)
        db_session.commit()

        result = _compute_anniversaries(db_session)
        assert "vbw" in result
        assert "verstorben" in result["vbw"]


class TestBackupJobRegistration:
    def test_backup_job_registered_by_default(self):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from app.core.scheduler import _next_backup_run, job_db_backup

        sched = AsyncIOScheduler()
        sched.add_job(
            job_db_backup,
            "interval",
            days=7,
            start_date=_next_backup_run(3),
            id="db_backup",
            replace_existing=True,
        )
        ids = [j.id for j in sched.get_jobs()]
        assert "db_backup" in ids

    def test_backup_job_absent_when_disabled(self):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        sched = AsyncIOScheduler()
        # When BACKUP_ENABLED=false, no job is added — empty scheduler has no db_backup
        ids = [j.id for j in sched.get_jobs()]
        assert "db_backup" not in ids
