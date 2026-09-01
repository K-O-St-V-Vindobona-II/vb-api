"""Tests for public site content: about tabs, MKV video + programm calendar
settings, programm hints, quotes, social links — public read (bundled into
one /public/site-content response) + admin CRUD.

All 5 tables are seeded by the alembic migration itself (see
85e63a22e9a7_add_public_site_content_tables.py), so every test already
starts with that baseline content present (3 about tabs, 1 settings row,
5 hints, 2 quotes, 2 social links with only "instagram" enabled) — tests
build on top of that seed rather than starting from empty tables.
"""

import uuid
from datetime import date

import bcrypt
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.member import Member
from app.models.member_role import MemberRole
from app.models.org import Org
from app.models.public_site_about_tab import PublicSiteAboutTab
from app.models.public_site_programm_hint import PublicSiteProgrammHint
from app.models.public_site_quote import PublicSiteQuote
from app.models.public_site_settings import SETTINGS_ROW_ID, PublicSiteSettings
from app.models.public_site_social_link import PublicSiteSocialLink
from app.models.role import Role
from app.services import about_tabs_service
from app.services.auth_service import create_user_session

ADMIN_PREFIX = "/api/public-content-admin"


def _seed(db):
    db.add_all(
        [
            Org(id="vbw", label="VBW", order=1),
            Role(
                id="internetreferent",
                group="funktion",
                label="Internetreferent",
                order=1,
            ),
        ]
    )
    db.commit()


def _admin(db):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="admin@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Admin",
        nachname="User",
        org_id="vbw",
    )
    db.add(m)
    db.commit()
    db.add(
        MemberRole(
            member_id=m.id_uuid,
            role_id="internetreferent",
            startdate=date(2000, 1, 1),
            enddate=None,
        )
    )
    db.commit()
    return m


def _nonadmin(db):
    hashed = bcrypt.hashpw(b"pw", bcrypt.gensalt()).decode()
    m = Member(
        email="user@vbw.at",
        auth_password=hashed,
        auth_locked=False,
        vorname="Normal",
        nachname="User",
        org_id="vbw",
    )
    db.add(m)
    db.commit()
    return m


def _headers(db, member):
    token, _, _ = create_user_session(db, member)
    return {"Authorization": f"Bearer {token}"}


def _link_id(db, platform: str) -> str:
    """Look up a seeded social link's (now UUID) id by its stable platform
    slug - the migration to a UUID primary key (see
    61330e9e0ca8_public_site_social_links_id_to_uuid.py) means the seed
    rows' ids are no longer the predictable 1/2 an autoincrement sequence
    would assign."""
    link = (
        db.query(PublicSiteSocialLink)
        .filter(PublicSiteSocialLink.platform == platform)
        .one()
    )
    return str(link.id)


def _hint_id(db, sort_order: int) -> str:
    """Look up a seeded programm hint's (now UUID) id by its stable
    sort_order - same rationale as _link_id (see
    ad4d9aeff7b8_public_site_content_ids_to_uuid.py)."""
    hint = (
        db.query(PublicSiteProgrammHint)
        .filter(PublicSiteProgrammHint.sort_order == sort_order)
        .one()
    )
    return str(hint.id)


def _quote_id(db, author: str) -> str:
    """Look up a seeded quote's (now UUID) id by its stable author - same
    rationale as _link_id (see
    ad4d9aeff7b8_public_site_content_ids_to_uuid.py)."""
    quote = db.query(PublicSiteQuote).filter(PublicSiteQuote.author == author).one()
    return str(quote.id)


class TestPublicSiteContent:
    def test_returns_seeded_content(self, client, db_session):
        resp = client.get("/api/public/site-content")
        assert resp.status_code == 200
        data = resp.json()

        assert data["about_tabs"]["anfang"]["title"] == "Der Anfang"
        assert data["about_tabs"]["mkv"]["title"] == "MKV"
        assert data["about_tabs"]["heute"]["title"] == "Heute"
        assert "[hier zu finden]" in data["about_tabs"]["anfang"]["body"]

        assert data["settings"]["about_video_youtube_id"] == "Sh51ebB2G8A"
        assert data["settings"]["programm_calendar_id"] == (
            "h7d2qp0jlg603cvq2aabdn2k5o@group.calendar.google.com"
        )
        assert data["settings"]["gallery_heading"] == "Eindrücke"

        assert len(data["programm_hints"]) == 5
        assert len(data["quotes"]) == 2

        # Facebook is seeded disabled, only Instagram is enabled.
        assert len(data["social_links"]) == 1
        assert data["social_links"][0]["platform"] == "instagram"

    def test_response_never_cacheable(self, client, db_session):
        resp = client.get("/api/public/site-content")
        assert resp.headers["Cache-Control"] == "no-store"

    def test_query_count_does_not_scale_with_row_count(
        self, client, db_session, count_queries
    ):
        """One query per content type (about_tabs, settings, hints, quotes,
        social_links) — adding more hints/quotes/social_links must not add
        more queries (no per-row lazy-loading)."""
        with count_queries() as small:
            resp_small = client.get("/api/public/site-content")

        db_session.add_all(
            [
                PublicSiteProgrammHint(content=f"Hinweis {i}", sort_order=100 + i)
                for i in range(10)
            ]
            + [
                PublicSiteQuote(
                    quote=f"Zitat {i}", author=f"Autor {i}", sort_order=100 + i
                )
                for i in range(10)
            ]
            + [
                PublicSiteSocialLink(
                    platform=f"platform{i}",
                    label=f"Platform {i}",
                    url="https://example.com",
                    is_enabled=True,
                    sort_order=100 + i,
                )
                for i in range(10)
            ]
        )
        db_session.commit()

        with count_queries() as large:
            resp_large = client.get("/api/public/site-content")

        assert resp_small.status_code == 200
        assert resp_large.status_code == 200
        assert len(resp_large.json()["quotes"]) == 12
        assert large.count == small.count


class TestAboutTabsAdmin:
    def test_list_requires_authentication(self, client, db_session):
        resp = client.get(f"{ADMIN_PREFIX}/about-tabs")
        assert resp.status_code == 401

    def test_list_requires_permission(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _nonadmin(db_session))
        resp = client.get(f"{ADMIN_PREFIX}/about-tabs", headers=headers)
        assert resp.status_code == 403

    def test_list_returns_three_tabs_in_fixed_order(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.get(f"{ADMIN_PREFIX}/about-tabs", headers=headers)
        assert resp.status_code == 200
        slots = [tab["slot"] for tab in resp.json()]
        assert slots == ["anfang", "mkv", "heute"]

    def test_update_changes_title_and_body(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/about-tabs/mkv",
            headers=headers,
            json={"title": "Der MKV", "body": "Neuer Text."},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "slot": "mkv",
            "title": "Der MKV",
            "body": "Neuer Text.",
        }

    def test_update_title_too_long_rejected(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/about-tabs/anfang",
            headers=headers,
            json={"title": "x" * 101, "body": "Text."},
        )
        assert resp.status_code == 422

    def test_update_invalid_slot_rejected(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/about-tabs/nichtvorhanden",
            headers=headers,
            json={"title": "x", "body": "y"},
        )
        assert resp.status_code == 422

    def test_update_body_with_javascript_link_rejected(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/about-tabs/heute",
            headers=headers,
            json={
                "title": "Heute",
                "body": "Klick [hier](javascript:alert(1)) für mehr.",
            },
        )
        assert resp.status_code == 422

    def test_update_body_with_http_link_accepted(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/about-tabs/heute",
            headers=headers,
            json={
                "title": "Heute",
                "body": "Mehr [hier](https://vindobona2.at) lesen.",
            },
        )
        assert resp.status_code == 200


class TestAboutTabsServiceEdgeCases:
    def test_get_tab_or_404_raises_for_unknown_slot(self, db_session):
        # Unreachable via HTTP (the router's slot path param is a Literal
        # of the 3 known slots), but get_tab_or_404() is still defensive
        # at the service layer, matching every other *_or_404() in this
        # codebase - covered directly here.
        with pytest.raises(HTTPException) as exc_info:
            about_tabs_service.get_tab_or_404(db_session, "nichtvorhanden")
        assert exc_info.value.status_code == 404


class TestSiteSettingsAdmin:
    def test_get_returns_seeded_values(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.get(f"{ADMIN_PREFIX}/settings", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["about_video_youtube_id"] == "Sh51ebB2G8A"
        assert resp.json()["gallery_heading"] == "Eindrücke"

    def test_update_changes_gallery_heading(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/settings",
            headers=headers,
            json={
                "about_video_heading": "Erfahre mehr über den MKV",
                "youtube_url": "Sh51ebB2G8A",
                "calendar_id": "abc@group.calendar.google.com",
                "gallery_heading": "Bildergalerie",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["gallery_heading"] == "Bildergalerie"

    def test_update_empty_gallery_heading_rejected(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/settings",
            headers=headers,
            json={
                "about_video_heading": "Titel",
                "youtube_url": "abcdefghijk",
                "calendar_id": "abc@group.calendar.google.com",
                "gallery_heading": "",
            },
        )
        assert resp.status_code == 422

    def test_get_requires_permission(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _nonadmin(db_session))
        resp = client.get(f"{ADMIN_PREFIX}/settings", headers=headers)
        assert resp.status_code == 403

    @pytest.mark.parametrize(
        ("youtube_url", "expected_id"),
        [
            ("https://www.youtube.com/watch?v=abcdefghijk", "abcdefghijk"),
            ("https://youtu.be/abcdefghijk", "abcdefghijk"),
            ("https://www.youtube.com/embed/abcdefghijk", "abcdefghijk"),
            ("https://www.youtube.com/shorts/abcdefghijk", "abcdefghijk"),
            ("abcdefghijk", "abcdefghijk"),
        ],
    )
    def test_update_normalizes_youtube_url(
        self, client, db_session, youtube_url, expected_id
    ):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/settings",
            headers=headers,
            json={
                "about_video_heading": "Titel",
                "youtube_url": youtube_url,
                "calendar_id": "abc@group.calendar.google.com",
                "gallery_heading": "Eindrücke",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["about_video_youtube_id"] == expected_id

    def test_update_invalid_youtube_url_rejected(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/settings",
            headers=headers,
            json={
                "about_video_heading": "Titel",
                "youtube_url": "https://example.com/not-youtube",
                "calendar_id": "abc@group.calendar.google.com",
                "gallery_heading": "Eindrücke",
            },
        )
        assert resp.status_code == 422

    def test_update_extracts_calendar_id_from_embed_url(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/settings",
            headers=headers,
            json={
                "about_video_heading": "Titel",
                "youtube_url": "abcdefghijk",
                "calendar_id": (
                    "https://calendar.google.com/calendar/embed?src="
                    "abc%40group.calendar.google.com&ctz=Europe%2FVienna"
                ),
                "gallery_heading": "Eindrücke",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["programm_calendar_id"] == "abc@group.calendar.google.com"

    def test_update_invalid_calendar_id_rejected(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/settings",
            headers=headers,
            json={
                "about_video_heading": "Titel",
                "youtube_url": "abcdefghijk",
                "calendar_id": "not-a-calendar-id",
                "gallery_heading": "Eindrücke",
            },
        )
        assert resp.status_code == 422


class TestProgrammHintsAdmin:
    def test_list_returns_seeded_hints(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.get(f"{ADMIN_PREFIX}/programm-hints", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 5

    def test_create_requires_permission(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _nonadmin(db_session))
        resp = client.post(
            f"{ADMIN_PREFIX}/programm-hints", headers=headers, json={"text": "Neu"}
        )
        assert resp.status_code == 403

    def test_create_appends_after_seeded_rows(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.post(
            f"{ADMIN_PREFIX}/programm-hints", headers=headers, json={"text": "Neu"}
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["text"] == "Neu"

        db_session.expire_all()
        assert (
            db_session.get(PublicSiteProgrammHint, uuid.UUID(data["id"])).sort_order
            == 6
        )

    def test_create_text_too_long_rejected(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.post(
            f"{ADMIN_PREFIX}/programm-hints",
            headers=headers,
            json={"text": "x" * 301},
        )
        assert resp.status_code == 422

    def test_update_changes_text(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        hint_id = _hint_id(db_session, 1)
        resp = client.put(
            f"{ADMIN_PREFIX}/programm-hints/{hint_id}",
            headers=headers,
            json={"text": "Geändert."},
        )
        assert resp.status_code == 200
        assert resp.json() == {"id": hint_id, "text": "Geändert."}

    def test_update_not_found(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/programm-hints/{uuid.uuid4()}",
            headers=headers,
            json={"text": "x"},
        )
        assert resp.status_code == 404

    def test_move_up_swaps_sort_order(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        second_id = uuid.UUID(_hint_id(db_session, 2))
        first_id = uuid.UUID(_hint_id(db_session, 1))
        resp = client.post(
            f"{ADMIN_PREFIX}/programm-hints/{second_id}/move",
            headers=headers,
            json={"direction": "up"},
        )
        assert resp.status_code == 200

        db_session.expire_all()
        assert db_session.get(PublicSiteProgrammHint, second_id).sort_order == 1
        assert db_session.get(PublicSiteProgrammHint, first_id).sort_order == 2

    def test_move_up_at_top_is_noop(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        first_id = uuid.UUID(_hint_id(db_session, 1))
        resp = client.post(
            f"{ADMIN_PREFIX}/programm-hints/{first_id}/move",
            headers=headers,
            json={"direction": "up"},
        )
        assert resp.status_code == 200
        db_session.expire_all()
        assert db_session.get(PublicSiteProgrammHint, first_id).sort_order == 1

    def test_delete_removes_row(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        fifth_id = _hint_id(db_session, 5)
        resp = client.delete(
            f"{ADMIN_PREFIX}/programm-hints/{fifth_id}", headers=headers
        )
        assert resp.status_code == 204
        assert db_session.get(PublicSiteProgrammHint, uuid.UUID(fifth_id)) is None

    def test_delete_requires_permission(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _nonadmin(db_session))
        resp = client.delete(
            f"{ADMIN_PREFIX}/programm-hints/{uuid.uuid4()}", headers=headers
        )
        assert resp.status_code == 403


class TestQuotesAdmin:
    def test_list_returns_seeded_quotes(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.get(f"{ADMIN_PREFIX}/quotes", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 2
        assert resp.json()[0]["author"] == "Ein Fuchs"

    def test_create_appends_after_seeded_rows(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.post(
            f"{ADMIN_PREFIX}/quotes",
            headers=headers,
            json={"quote": "Ein neues Zitat.", "author": "Jemand"},
        )
        assert resp.status_code == 201
        data = resp.json()

        db_session.expire_all()
        assert db_session.get(PublicSiteQuote, uuid.UUID(data["id"])).sort_order == 3

    def test_create_missing_author_rejected(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.post(
            f"{ADMIN_PREFIX}/quotes",
            headers=headers,
            json={"quote": "Ein Zitat ohne Autor."},
        )
        assert resp.status_code == 422

    def test_update_changes_quote_and_author(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        quote_id = _quote_id(db_session, "Ein Fuchs")
        resp = client.put(
            f"{ADMIN_PREFIX}/quotes/{quote_id}",
            headers=headers,
            json={"quote": "Geändertes Zitat.", "author": "Neuer Autor"},
        )
        assert resp.status_code == 200
        assert resp.json()["quote"] == "Geändertes Zitat."

    def test_move_down_swaps_sort_order(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        fuchs_id = uuid.UUID(_quote_id(db_session, "Ein Fuchs"))
        aktiver_id = uuid.UUID(_quote_id(db_session, "Ein Junger Aktiver"))
        resp = client.post(
            f"{ADMIN_PREFIX}/quotes/{fuchs_id}/move",
            headers=headers,
            json={"direction": "down"},
        )
        assert resp.status_code == 200
        db_session.expire_all()
        assert db_session.get(PublicSiteQuote, fuchs_id).sort_order == 2
        assert db_session.get(PublicSiteQuote, aktiver_id).sort_order == 1

    def test_move_down_at_bottom_is_noop(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        aktiver_id = uuid.UUID(_quote_id(db_session, "Ein Junger Aktiver"))
        resp = client.post(
            f"{ADMIN_PREFIX}/quotes/{aktiver_id}/move",
            headers=headers,
            json={"direction": "down"},
        )
        assert resp.status_code == 200
        db_session.expire_all()
        assert db_session.get(PublicSiteQuote, aktiver_id).sort_order == 2

    def test_delete_removes_row(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        fuchs_id = uuid.UUID(_quote_id(db_session, "Ein Fuchs"))
        resp = client.delete(f"{ADMIN_PREFIX}/quotes/{fuchs_id}", headers=headers)
        assert resp.status_code == 204
        assert db_session.get(PublicSiteQuote, fuchs_id) is None


class TestSocialLinksAdmin:
    def test_list_returns_both_seeded_links(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.get(f"{ADMIN_PREFIX}/social-links", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        platforms = {link["platform"]: link["is_enabled"] for link in data}
        assert platforms == {"facebook": False, "instagram": True}

    def test_create_new_platform(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.post(
            f"{ADMIN_PREFIX}/social-links",
            headers=headers,
            json={
                "platform": "linkedin",
                "label": "LinkedIn",
                "url": "https://www.linkedin.com/company/vindobona2",
                "is_enabled": True,
            },
        )
        assert resp.status_code == 201
        data = resp.json()

        db_session.expire_all()
        assert (
            db_session.get(PublicSiteSocialLink, uuid.UUID(data["id"])).sort_order == 3
        )

    def test_create_invalid_platform_slug_rejected(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.post(
            f"{ADMIN_PREFIX}/social-links",
            headers=headers,
            json={
                "platform": "LinkedIn",  # uppercase - violates the slug pattern
                "label": "LinkedIn",
                "url": "https://www.linkedin.com/company/vindobona2",
                "is_enabled": True,
            },
        )
        assert resp.status_code == 422

    def test_create_non_http_url_rejected(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.post(
            f"{ADMIN_PREFIX}/social-links",
            headers=headers,
            json={
                "platform": "mail",
                "label": "Mail",
                "url": "mailto:vindoboneninfo@gmail.com",
                "is_enabled": True,
            },
        )
        assert resp.status_code == 422

    def test_update_toggles_enabled_and_changes_public_visibility(
        self, client, db_session
    ):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))

        # Facebook starts disabled (seed) - not in the public response yet.
        before = client.get("/api/public/site-content").json()
        assert "facebook" not in {s["platform"] for s in before["social_links"]}

        resp = client.put(
            f"{ADMIN_PREFIX}/social-links/{_link_id(db_session, 'facebook')}",
            headers=headers,
            json={
                "label": "Facebook",
                "url": "https://www.facebook.com/vindobona2",
                "is_enabled": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["is_enabled"] is True

        after = client.get("/api/public/site-content").json()
        assert "facebook" in {s["platform"] for s in after["social_links"]}

    def test_update_rejects_platform_field(self, client, db_session):
        # SocialLinkUpdateRequest has no `platform` field - StrictInputModel's
        # extra="forbid" must reject an attempt to change it post-creation.
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        resp = client.put(
            f"{ADMIN_PREFIX}/social-links/{_link_id(db_session, 'instagram')}",
            headers=headers,
            json={
                "platform": "twitter",
                "label": "Instagram",
                "url": "http://www.instagram.com/vindobona2",
                "is_enabled": True,
            },
        )
        assert resp.status_code == 422

    def test_move_swaps_sort_order(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        instagram_id = _link_id(db_session, "instagram")
        resp = client.post(
            f"{ADMIN_PREFIX}/social-links/{instagram_id}/move",
            headers=headers,
            json={"direction": "up"},
        )
        assert resp.status_code == 200
        db_session.expire_all()
        assert (
            db_session.query(PublicSiteSocialLink)
            .filter_by(platform="instagram")
            .one()
            .sort_order
            == 1
        )
        assert (
            db_session.query(PublicSiteSocialLink)
            .filter_by(platform="facebook")
            .one()
            .sort_order
            == 2
        )

    def test_delete_removes_row(self, client, db_session):
        _seed(db_session)
        headers = _headers(db_session, _admin(db_session))
        facebook_id = _link_id(db_session, "facebook")
        resp = client.delete(
            f"{ADMIN_PREFIX}/social-links/{facebook_id}", headers=headers
        )
        assert resp.status_code == 204
        assert (
            db_session.query(PublicSiteSocialLink)
            .filter_by(platform="facebook")
            .first()
            is None
        )


class TestSocialLinkUuidDefault:
    """Guards the UUID-PK migration's central assumption (see
    61330e9e0ca8_public_site_social_links_id_to_uuid.py): every insert
    goes through the ORM instance, so `default=uuid.uuid7` on the model
    fires without ever needing a server-side default."""

    def test_id_defaults_to_a_valid_uuid7(self, db_session):
        link = PublicSiteSocialLink(
            platform="mastodon",
            label="Mastodon",
            url="https://mastodon.social/@vindobona2",
            is_enabled=True,
            sort_order=99,
        )
        db_session.add(link)
        db_session.flush()

        assert isinstance(link.id, uuid.UUID)
        assert link.id.version == 7


class TestAboutTabUuidDefault:
    """Same guard as TestSocialLinkUuidDefault, for the second UUID-PK
    migration slice (see ad4d9aeff7b8_public_site_content_ids_to_uuid.py).
    slot is unique and CHECK-constrained to the 3 known values, so this
    replaces one of the already-seeded rows rather than adding a 4th."""

    def test_id_defaults_to_a_valid_uuid7(self, db_session):
        db_session.query(PublicSiteAboutTab).filter_by(slot="anfang").delete()
        tab = PublicSiteAboutTab(slot="anfang", title="Der Anfang", body="Text.")
        db_session.add(tab)
        db_session.flush()

        assert isinstance(tab.id, uuid.UUID)
        assert tab.id.version == 7


class TestProgrammHintUuidDefault:
    def test_id_defaults_to_a_valid_uuid7(self, db_session):
        hint = PublicSiteProgrammHint(content="Ein neuer Hinweis.", sort_order=99)
        db_session.add(hint)
        db_session.flush()

        assert isinstance(hint.id, uuid.UUID)
        assert hint.id.version == 7


class TestQuoteUuidDefault:
    def test_id_defaults_to_a_valid_uuid7(self, db_session):
        quote = PublicSiteQuote(quote="Ein Zitat.", author="Jemand", sort_order=99)
        db_session.add(quote)
        db_session.flush()

        assert isinstance(quote.id, uuid.UUID)
        assert quote.id.version == 7


class TestSiteSettingsSingleton:
    """Guards the UUID-PK migration's singleton-specific pattern (see
    5c9769a76def_public_site_settings_id_to_uuid.py): unlike every other
    migrated table, this row's id is a fixed literal (SETTINGS_ROW_ID),
    not a runtime `uuid.uuid7()` default, so the guard here checks that
    literal instead of a default factory."""

    def test_settings_row_id_addresses_the_seeded_row(self, db_session):
        assert db_session.get(PublicSiteSettings, SETTINGS_ROW_ID) is not None

    def test_singleton_check_rejects_a_second_row(self, db_session):
        other = PublicSiteSettings(
            id=uuid.uuid4(),
            about_video_heading="Titel",
            about_video_youtube_id="abcdefghijk",
            programm_calendar_id="abc@group.calendar.google.com",
            gallery_heading="Eindrücke",
        )
        db_session.add(other)
        with pytest.raises(IntegrityError):
            db_session.flush()
