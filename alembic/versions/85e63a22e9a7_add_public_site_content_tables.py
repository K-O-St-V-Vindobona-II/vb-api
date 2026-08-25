"""add public site content tables

Revision ID: 85e63a22e9a7
Revises: 9f8ff3c10cb2
Create Date: 2026-08-05 13:54:17.072491

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "85e63a22e9a7"
down_revision: str | Sequence[str] | None = "9f8ff3c10cb2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Seed content below is the exact text that was previously hardcoded in
# vb-www (AboutSection.vue, ProgrammSection.vue, TestimonialsSection.vue,
# AppNav.vue) before this migration made it admin-editable — this keeps the
# public site's content unchanged at the moment this migration runs. The
# single link in the "anfang" tab's body is converted from a real <a> tag
# to this feature's `[text](url)` mini-syntax (see parseLinkedText.ts on
# the vb-www side); a blank line between the two source <p> tags becomes
# the paragraph separator.
_ANFANG_BODY = (
    "Als Mittelschülerverbindung ist die katholische österreichische "
    "Studentenverbindung Vindobona II seit ihrer Gründung am 19.9.1928 eine "
    "Vereinigung, die sich vorwiegend an Schüler der Oberstufe und junge "
    "Studenten richtet. Mitglied werden kann jeder männliche Katholik, der "
    "eine Oberstufe besucht und die Matura anstrebt oder die Matura bereits "
    "absolviert hat. In farbstudentischer Tradition tragen die Mitglieder "
    "der Vindobona II ein rot-gold-rotes Burschenband mit grünem Vorstoß, "
    "das Fuchsenband ist gold-rot, die Deckel dunkelolivgrün. Als "
    "katholische Organisation lehnen wir jede Form des bewaffneten "
    "Zweikampfes ab — es ist den Mitgliedern der Vindobona II daher nicht "
    "gestattet, die Mensur zu fechten. Als österreichische Verbindung "
    "steht uns jeder Nationalismus und Extremismus fern. Anders als bei "
    "anderen Vereinen liegen bei Vindobona II die Vorstandsfunktionen "
    "sowie die meisten Organisationstätigkeiten in den Händen der Jungen, "
    "den sogenannten „Aktiven“.\n\n"
    "Viele Mitglieder aus der Gründungszeit unserer Verbindung waren im "
    "Widerstand aktiv. Das Projekt „Niemals Wieder“ hat es sich zur "
    "Aufgabe gemacht, katholische Farbstudenten im Widerstand zu "
    "dokumentieren und zusammenzutragen. Die Stolpersteine der "
    "Widerstandskämpfer unserer Verbindung sind "
    "[hier zu finden](https://niemalswieder.at/Stolpersteine/Steine/1368)."
)

_MKV_BODY = (
    "Vindobona II ist Teil des Mittelschülerkartellverbandes (MKV). "
    "Diesem gehören österreichweit über 160 Verbindungen mit insgesamt "
    "knapp 20.000 Mitgliedern an, was den MKV zur größten "
    "Schülerorganisation Österreichs macht. Der MKV ist wiederum Teil des "
    "Europäischen Kartellverbandes (EKV), der europaweit über 120.000 "
    "Mitglieder in rund 660 Verbindungen in 15 Ländern umfasst. Was bei "
    "Vindobona II gepflegt wird, gründet auf vier unverrückbaren "
    "Prinzipien: religio (Bekenntnis zum katholischen Glauben), patria "
    "(Bekenntnis zur Heimat Österreich), scientia (lebenslanges Lernen), "
    "amicitia (Lebensfreundschaft)."
)

_HEUTE_BODY = (
    "Nach dem Eintritt bei Vindobona II absolviert jedes Neumitglied eine "
    "Probezeit (Fuchsenzeit), in der es die Verbindung und die Verbindung "
    "ihn kennenlernen kann. Entscheidet sich das Neumitglied, dabei zu "
    "bleiben, wird es nach ein bis zwei Jahren mit der feierlichen "
    "Burschung als Vollmitglied aufgenommen. In den folgenden Jahren "
    "übernimmt das Mitglied als „Aktiver“ Vorstandspositionen und "
    "Funktionen in der Verbindung. Nach einigen Jahren — häufig nach "
    "absolviertem Studium — wird das Mitglied „philistriert“, bleibt der "
    "Verbindung jedoch sein Leben lang angehörig und verbunden. Hier "
    "treffen Schüler auf Richter, Studenten auf Politiker und "
    "Praktikanten auf Vorstandsvorsitzende — alle pflegen untereinander "
    "das „Du“."
)

_HINTS = (
    (
        "Alle Veranstaltungen beginnen, je nach Angabe, c.t. (cum tempore, 15 "
        "Minuten nach der angegebenen Zeit) oder s.t. (sine tempore, "
        "pünktlich)."
    ),
    (
        "Alle Veranstaltungen finden auf der Bude Vindobonae statt, sofern "
        "nicht anders angegeben."
    ),
    (
        "Die Convente und alle als „intern“ bezeichneten Veranstaltungen "
        "finden ohne Gäste statt."
    ),
    (
        "Bei anderen Veranstaltungen sind Gäste willkommen — wenn du uns noch "
        "nicht kennst, bitten wir um kurze Voranmeldung."
    ),
    (
        "Die „offiziellen“ und „hochoffiziellen“ Veranstaltungen finden "
        "plen.col. („in vollen Farben“) statt. Auf ein entsprechendes "
        "Erscheinungsbild ist Wert zu legen."
    ),
)


def _create_tables() -> None:
    op.create_table(
        "public_site_about_tabs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slot", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slot", name="public_site_about_tabs_slot_key"),
        sa.CheckConstraint(
            "slot IN ('anfang', 'mkv', 'heute')",
            name="public_site_about_tabs_slot_check",
        ),
        sa.CheckConstraint(
            "char_length(title) > 0", name="public_site_about_tabs_title_check"
        ),
        sa.CheckConstraint(
            "char_length(body) > 0", name="public_site_about_tabs_body_check"
        ),
    )
    op.execute(
        "CREATE TRIGGER public_site_about_tabs_set_updated_at "
        "BEFORE UPDATE ON public_site_about_tabs "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    op.create_table(
        "public_site_settings",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("about_video_heading", sa.Text(), nullable=False),
        sa.Column("about_video_youtube_id", sa.Text(), nullable=False),
        sa.Column("programm_calendar_id", sa.Text(), nullable=False),
        sa.Column("gallery_heading", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="public_site_settings_singleton_check"),
        sa.CheckConstraint(
            "char_length(about_video_heading) > 0",
            name="public_site_settings_heading_check",
        ),
        sa.CheckConstraint(
            "about_video_youtube_id ~ '^[A-Za-z0-9_-]{11}$'",
            name="public_site_settings_youtube_id_check",
        ),
        sa.CheckConstraint(
            r"programm_calendar_id ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'",
            name="public_site_settings_calendar_id_check",
        ),
        sa.CheckConstraint(
            "char_length(gallery_heading) > 0",
            name="public_site_settings_gallery_heading_check",
        ),
    )
    op.execute(
        "CREATE TRIGGER public_site_settings_set_updated_at "
        "BEFORE UPDATE ON public_site_settings "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    op.create_table(
        "public_site_programm_hints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "char_length(text) > 0", name="public_site_programm_hints_text_check"
        ),
    )
    op.create_index(
        "ix_public_site_programm_hints_sort_order",
        "public_site_programm_hints",
        ["sort_order"],
    )
    op.execute(
        "CREATE TRIGGER public_site_programm_hints_set_updated_at "
        "BEFORE UPDATE ON public_site_programm_hints "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    op.create_table(
        "public_site_quotes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "char_length(quote) > 0", name="public_site_quotes_quote_check"
        ),
        sa.CheckConstraint(
            "char_length(author) > 0", name="public_site_quotes_author_check"
        ),
    )
    op.create_index(
        "ix_public_site_quotes_sort_order", "public_site_quotes", ["sort_order"]
    )
    op.execute(
        "CREATE TRIGGER public_site_quotes_set_updated_at "
        "BEFORE UPDATE ON public_site_quotes "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )

    op.create_table(
        "public_site_social_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "platform ~ '^[a-z][a-z0-9_]*$'",
            name="public_site_social_links_platform_check",
        ),
        sa.CheckConstraint(
            "char_length(label) > 0", name="public_site_social_links_label_check"
        ),
        sa.CheckConstraint(
            "url ~* '^https?://'", name="public_site_social_links_url_check"
        ),
    )
    op.create_index(
        "ix_public_site_social_links_sort_order",
        "public_site_social_links",
        ["sort_order"],
    )
    op.execute(
        "CREATE TRIGGER public_site_social_links_set_updated_at "
        "BEFORE UPDATE ON public_site_social_links "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )


def _seed() -> None:
    about_tabs = sa.table(
        "public_site_about_tabs",
        sa.column("slot", sa.Text()),
        sa.column("title", sa.Text()),
        sa.column("body", sa.Text()),
    )
    op.bulk_insert(
        about_tabs,
        [
            {"slot": "anfang", "title": "Der Anfang", "body": _ANFANG_BODY},
            {"slot": "mkv", "title": "MKV", "body": _MKV_BODY},
            {"slot": "heute", "title": "Heute", "body": _HEUTE_BODY},
        ],
    )

    settings = sa.table(
        "public_site_settings",
        sa.column("id", sa.SmallInteger()),
        sa.column("about_video_heading", sa.Text()),
        sa.column("about_video_youtube_id", sa.Text()),
        sa.column("programm_calendar_id", sa.Text()),
        sa.column("gallery_heading", sa.Text()),
    )
    op.bulk_insert(
        settings,
        [
            {
                "id": 1,
                "about_video_heading": "Erfahre mehr über den MKV",
                "about_video_youtube_id": "Sh51ebB2G8A",
                "programm_calendar_id": (
                    "h7d2qp0jlg603cvq2aabdn2k5o@group.calendar.google.com"
                ),
                # Matches GallerySection.vue's current hardcoded <h2>.
                "gallery_heading": "Eindrücke",
            }
        ],
    )

    hints = sa.table(
        "public_site_programm_hints",
        sa.column("text", sa.Text()),
        sa.column("sort_order", sa.Integer()),
    )
    op.bulk_insert(
        hints,
        [{"text": text, "sort_order": i} for i, text in enumerate(_HINTS, start=1)],
    )

    quotes = sa.table(
        "public_site_quotes",
        sa.column("quote", sa.Text()),
        sa.column("author", sa.Text()),
        sa.column("sort_order", sa.Integer()),
    )
    op.bulk_insert(
        quotes,
        [
            {
                "quote": (
                    "Als ich das erste Mal bei einer Verbindung war, war "
                    "ich sofort begeistert und fühlte mich in der "
                    "Gemeinschaft aufgehoben."
                ),
                "author": "Ein Fuchs",
                "sort_order": 1,
            },
            {
                "quote": (
                    "Durch die Verbindung hab ich herausgefunden, was mich "
                    "interessiert."
                ),
                "author": "Ein Junger Aktiver",
                "sort_order": 2,
            },
        ],
    )

    social_links = sa.table(
        "public_site_social_links",
        sa.column("platform", sa.Text()),
        sa.column("label", sa.Text()),
        sa.column("url", sa.Text()),
        sa.column("is_enabled", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    op.bulk_insert(
        social_links,
        [
            {
                "platform": "facebook",
                "label": "Facebook",
                "url": "https://www.facebook.com/vindobona2",
                # Matches the current FACEBOOK_LINK_ENABLED = false flag
                # this migration replaces (see AppFooter.vue/AppNav.vue) -
                # the page was unreachable at the time it was disabled.
                "is_enabled": False,
                "sort_order": 1,
            },
            {
                "platform": "instagram",
                "label": "Instagram",
                # Kept exactly as it was hardcoded (http, not https) - not
                # this migration's job to silently "fix" it; editable via
                # the new admin UI going forward.
                "url": "http://www.instagram.com/vindobona2",
                "is_enabled": True,
                "sort_order": 2,
            },
        ],
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    _create_tables()
    _seed()


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("SET LOCAL lock_timeout = '5s'")
    for table in (
        "public_site_social_links",
        "public_site_quotes",
        "public_site_programm_hints",
        "public_site_settings",
        "public_site_about_tabs",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON {table}")
    op.drop_index(
        "ix_public_site_social_links_sort_order",
        table_name="public_site_social_links",
    )
    op.drop_table("public_site_social_links")
    op.drop_index("ix_public_site_quotes_sort_order", table_name="public_site_quotes")
    op.drop_table("public_site_quotes")
    op.drop_index(
        "ix_public_site_programm_hints_sort_order",
        table_name="public_site_programm_hints",
    )
    op.drop_table("public_site_programm_hints")
    op.drop_table("public_site_settings")
    op.drop_table("public_site_about_tabs")
