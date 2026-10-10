"""Time indexes on the two log tables, and the texts of the site settings."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.public_site_settings import PublicSiteSettings
from app.models.public_site_social_link import PublicSiteSocialLink
from app.models.request_log import RequestLog
from app.models.sent_email import SentEmail


def _indexes(db_session, table):
    return {
        row[0]
        for row in db_session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = :t"),
            {"t": table},
        )
    }


def _plan(db_session, query: str) -> str:
    return "\n".join(row[0] for row in db_session.execute(text("EXPLAIN " + query)))


def test_the_log_tables_have_time_indexes(db_session):
    assert {
        "ix_request_logs_created_at",
        "ix_request_logs_member_id_created_at",
    } <= _indexes(db_session, "request_logs")
    assert "ix_sent_emails_created_at" in _indexes(db_session, "sent_emails")


def test_the_member_index_is_covered_by_the_combined_one(db_session):
    assert "ix_request_logs_member_id" not in _indexes(db_session, "request_logs")


def test_the_models_declare_the_time_indexes():
    request_indexes = {
        index.name: [column.name for column in index.columns]
        for index in RequestLog.__table__.indexes
    }

    assert request_indexes["ix_request_logs_created_at"] == ["created_at"]
    assert request_indexes["ix_request_logs_member_id_created_at"] == [
        "member_id",
        "created_at",
    ]
    assert [index.name for index in SentEmail.__table__.indexes] == [
        "ix_sent_emails_created_at"
    ]


def test_a_month_of_the_request_log_is_read_through_the_index(db_session):
    db_session.execute(
        text(
            "INSERT INTO request_logs (client_ip, client_ips, request_method, "
            "request_path, response_status, memory_usage, created_at) "
            "SELECT '127.0.0.1', '[]'::jsonb, 'GET', '/x', 200, 0, "
            "now() - (g || ' minutes')::interval FROM generate_series(1, 60000) g"
        )
    )
    db_session.execute(text("ANALYZE request_logs"))

    plan = _plan(
        db_session,
        "SELECT created_at FROM request_logs "
        "WHERE created_at >= now() - interval '2 hours' AND created_at < now()",
    )

    assert "Seq Scan" not in plan


@pytest.mark.parametrize("column", ["about_video_heading", "gallery_heading"])
@pytest.mark.parametrize("blank", [" ", "\t", " \t\n", "\r\n"])
def test_a_heading_of_white_space_is_refused(db_session, column, blank):
    with pytest.raises(IntegrityError, match="check constraint"):
        db_session.execute(
            text(f"UPDATE public_site_settings SET {column} = :v"),  # noqa: S608
            {"v": blank},
        )


@pytest.mark.parametrize("column", ["about_video_heading", "gallery_heading"])
def test_a_normal_heading_is_accepted(db_session, column):
    db_session.execute(
        text(f"UPDATE public_site_settings SET {column} = 'Überschrift'")  # noqa: S608
    )
    db_session.commit()


@pytest.mark.parametrize("blank", [" ", "\t", " \t\n"])
def test_a_social_link_label_of_white_space_is_refused(db_session, blank):
    db_session.add(
        PublicSiteSocialLink(
            platform="instagram",
            label=blank,
            url="https://example.at",
            sort_order=1,
            is_enabled=True,
        )
    )

    with pytest.raises(IntegrityError, match="check constraint"):
        db_session.commit()


def test_a_normal_social_link_label_is_accepted(db_session):
    db_session.add(
        PublicSiteSocialLink(
            platform="instagram",
            label="Instagram",
            url="https://example.at",
            sort_order=1,
            is_enabled=True,
        )
    )

    db_session.commit()


def test_the_models_declare_the_white_space_checks():
    def checks(model):
        return {
            c.name: str(c.sqltext)
            for c in model.__table__.constraints
            if c.__class__.__name__ == "CheckConstraint"
        }

    settings = checks(PublicSiteSettings)
    assert settings["public_site_settings_heading_check"] == (
        "about_video_heading ~ '[^[:space:]]'"
    )
    assert settings["public_site_settings_gallery_heading_check"] == (
        "gallery_heading ~ '[^[:space:]]'"
    )
    assert checks(PublicSiteSocialLink)["public_site_social_links_label_check"] == (
        "label ~ '[^[:space:]]'"
    )
