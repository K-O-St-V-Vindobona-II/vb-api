import re
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.base import StrictInputModel

# [text](url) - the only "formatting" allowed in about-tab body text. Only
# http(s) links are accepted; this is defense-in-depth on top of the
# frontend parser (vb-www/src/utils/parseLinkedText.ts), which independently
# refuses to render anything else as a link.
_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

_YOUTUBE_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube(?:-nocookie)?\.com/(?:watch\?v=|embed/|shorts/|v/))"
    r"([A-Za-z0-9_-]{11})"
)
_BARE_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Matches the public_site_settings.programm_calendar_id CHECK constraint -
# Google Calendar ids look like an email address.
_CALENDAR_ID_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_PLATFORM_SLUG_PATTERN = r"^[a-z][a-z0-9_]*$"


def _require_http_url(v: str) -> str:
    if not v.startswith(("http://", "https://")):
        msg = "Link muss mit http:// oder https:// beginnen."
        raise ValueError(msg)
    return v


# --- About tabs ---------------------------------------------------------


class AboutTabContent(BaseModel):
    title: str
    body: str

    model_config = ConfigDict(from_attributes=True)


class AboutTabsResponse(BaseModel):
    """Public shape: the 3 fixed slots as named fields, not a list - their
    order/identity is fixed (see PublicSiteAboutTab.KNOWN_SLOTS), so there's
    nothing to iterate."""

    anfang: AboutTabContent
    mkv: AboutTabContent
    heute: AboutTabContent


class AboutTabAdminResponse(BaseModel):
    """Admin shape: includes `slot` so the admin UI can iterate all three
    as a list of forms."""

    slot: str
    title: str
    body: str

    model_config = ConfigDict(from_attributes=True)


class AboutTabUpdateRequest(StrictInputModel):
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=6000)

    @field_validator("body")
    @classmethod
    def links_must_be_http(cls, v: str) -> str:
        for match in _LINK_PATTERN.finditer(v):
            _require_http_url(match.group(2))
        return v


# --- Site settings (video + calendar) -----------------------------------


class SiteSettingsResponse(BaseModel):
    about_video_heading: str
    about_video_youtube_id: str
    programm_calendar_id: str
    gallery_heading: str

    model_config = ConfigDict(from_attributes=True)


class SiteSettingsUpdateRequest(StrictInputModel):
    about_video_heading: str = Field(min_length=1, max_length=200)
    # Admin pastes a normal YouTube link (watch/share/embed/shorts) - this
    # gets normalized to the bare 11-character video id by the validator
    # below before it ever reaches the service layer.
    youtube_url: str = Field(min_length=1, max_length=500)
    # Admin pastes either the raw Calendar id (as shown in Google
    # Calendar's "Integrate calendar" settings) or a full embed URL -
    # both are accepted, normalized to the bare id below.
    calendar_id: str = Field(min_length=1, max_length=500)
    gallery_heading: str = Field(min_length=1, max_length=200)

    @field_validator("youtube_url")
    @classmethod
    def extract_youtube_id(cls, v: str) -> str:
        match = _YOUTUBE_ID_RE.search(v)
        if match:
            return match.group(1)
        if _BARE_YOUTUBE_ID_RE.match(v.strip()):
            return v.strip()
        msg = "Kein gültiger YouTube-Link erkannt."
        raise ValueError(msg)

    @field_validator("calendar_id")
    @classmethod
    def extract_calendar_id(cls, v: str) -> str:
        candidate = v.strip()
        if candidate.startswith(("http://", "https://")):
            src_values = parse_qs(urlparse(candidate).query).get("src")
            if not src_values:
                msg = "Kein 'src'-Parameter im Kalender-Link gefunden."
                raise ValueError(msg)
            candidate = src_values[0]
        if not _CALENDAR_ID_RE.match(candidate):
            msg = "Ungültige Kalender-ID."
            raise ValueError(msg)
        return candidate


# --- Programm hints -------------------------------------------------------


class ProgrammHintResponse(BaseModel):
    id: int
    # The DB/model column is `content` (see PublicSiteProgrammHint - the DB
    # column is named "text", which would shadow sqlalchemy.text() as a
    # model attribute name), aliased back to the natural API field name.
    text: str = Field(validation_alias="content")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ProgrammHintRequest(StrictInputModel):
    """Shared body shape for both creating and editing a hint."""

    text: str = Field(min_length=1, max_length=300)


# --- Quotes ---------------------------------------------------------------


class QuoteResponse(BaseModel):
    id: int
    quote: str
    author: str

    model_config = ConfigDict(from_attributes=True)


class QuoteRequest(StrictInputModel):
    """Shared body shape for both creating and editing a quote."""

    quote: str = Field(min_length=1, max_length=500)
    author: str = Field(min_length=1, max_length=100)


# --- Social links -----------------------------------------------------------


class SocialLinkResponse(BaseModel):
    """Public shape - only ever the enabled links, already sorted."""

    id: int
    platform: str
    label: str
    url: str

    model_config = ConfigDict(from_attributes=True)


class SocialLinkAdminResponse(BaseModel):
    id: int
    platform: str
    label: str
    url: str
    is_enabled: bool

    model_config = ConfigDict(from_attributes=True)


class SocialLinkCreateRequest(StrictInputModel):
    # platform is a stable lookup slug, only settable at creation - see
    # PublicSiteSocialLink's docstring for why it's a free slug, not a
    # fixed CHECK-enum.
    platform: str = Field(min_length=1, max_length=40, pattern=_PLATFORM_SLUG_PATTERN)
    label: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=1, max_length=500)
    is_enabled: bool = True

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        return _require_http_url(v)


class SocialLinkUpdateRequest(StrictInputModel):
    label: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=1, max_length=500)
    is_enabled: bool = True

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        return _require_http_url(v)


# --- Combined public response ----------------------------------------------


class SiteContentResponse(BaseModel):
    about_tabs: AboutTabsResponse
    settings: SiteSettingsResponse
    programm_hints: list[ProgrammHintResponse]
    quotes: list[QuoteResponse]
    social_links: list[SocialLinkResponse]
