import re
from datetime import date
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.models.enums import (
    BadgeGroup,
    ContactType,
    MemberChangeRequestStatus,
    MemberDeliveryPreference,
    RoleGroup,
)
from app.schemas.base import IdLabelOption, StrictInputModel, UtcDatetime

PHONE_REGEX = re.compile(r"^\+?[\d\s/\- ]+$")

# Shared across every *_accuracy field below (see _format_date_by_accuracy in
# app/core/mailer.py, the canonical implementation of this scale): how
# precisely the paired date is actually known, not a data-quality score.
_DATE_ACCURACY_DESCRIPTION = (
    "How precisely the paired date is known: 0 = unknown (date value is "
    "meaningless), 1 = year only, 2 = month+year, 3 = full date."
)


# --- Reference Data ---


class OrgResponse(BaseModel):
    id: str
    label: str
    order: int
    model_config = ConfigDict(from_attributes=True)


class StateResponse(BaseModel):
    id: str
    label: str
    order: int
    model_config = ConfigDict(from_attributes=True)


class RoleResponse(BaseModel):
    id: str
    group: RoleGroup | None = None
    label: str | None = None
    order: int = 0
    model_config = ConfigDict(from_attributes=True)


class BadgeResponse(BaseModel):
    id: int
    name: str
    group: BadgeGroup | None = None
    order: int = 0
    model_config = ConfigDict(from_attributes=True)


class KeyResponse(BaseModel):
    id: int
    name: str
    model_config = ConfigDict(from_attributes=True)


class ReferenceDataResponse(BaseModel):
    orgs: list[OrgResponse]
    states: list[StateResponse]
    roles: list[RoleResponse]
    badges: list[BadgeResponse]
    keys: list[KeyResponse]


# --- Roles History ---


class RoleHistoryEntry(BaseModel):
    id: str
    label: str | None = None
    startdate: date
    enddate: date | None = None


class RoleHistoryResponse(BaseModel):
    id: str
    label: str | None = None
    group: str | None = None
    order: int = 0
    startdate: date
    enddate: date | None = None


# --- Badges & Keys ---


class BadgeEntry(BaseModel):
    id: int
    presentationdate: date | None = None
    presentationdate_accuracy: int = 0


class BadgeDetailResponse(BaseModel):
    id: int
    name: str
    group: str | None = None
    order: int = 0
    presentationdate: date | None = None
    presentationdate_accuracy: int = 0


class KeyEntry(BaseModel):
    id: int
    presentationdate: date | None = None
    presentationdate_accuracy: int = 0


class KeyDetailResponse(BaseModel):
    id: int
    name: str
    presentationdate: date | None = None
    presentationdate_accuracy: int = 0


# --- Keys List ---


class KeysListMember(BaseModel):
    id: int
    nachname: str | None = None
    vorname: str | None = None
    keys: dict[str, bool] = {}


class KeysListResponse(BaseModel):
    key_names: list[str]
    members: list[KeysListMember]


# --- Tree ---


class TreeNodeResponse(BaseModel):
    id: int
    cn: str
    gruender: bool = False
    org_id: str | None = None
    state_id: str | None = None
    entlassen: bool = False
    verstorben: bool = False
    children: list[TreeNodeResponse] = []


# --- Member Responses ---


class MemberDismissedResponse(BaseModel):
    """Data-minimized response for a member with entlassen=True (has left
    the fraternity) - returned by GET /members/{id} instead of the full
    MemberDetailResponse, which stops being disclosed once someone is no
    longer a member (see get_member_detail() in standesdb_service.py)."""

    id: int
    cn: str
    org_id: str | None = None
    dataprotection: str = Field(
        default="dismissed",
        description="Always 'dismissed' - a fixed marker for the frontend to "
        "distinguish this shape from MemberDetailResponse.",
    )


class MemberDetailResponse(BaseModel):
    id: int
    cn: str
    vortitel: str | None = None
    vorname: str | None = None
    nachname: str | None = None
    nachname_geburt: str | None = None
    nachtitel: str | None = None
    couleurname: str | None = None
    org_id: str | None = None
    org_label: str | None = None
    state_id: str | None = None
    state_label: str | None = None
    gruender: bool = Field(
        default=False, description="Founding member of the fraternity."
    )
    entlassen: bool = Field(
        default=False,
        description="Has formally left the fraternity - triggers "
        "MemberDismissedResponse instead of this shape on future lookups.",
    )
    verstorben: bool = Field(default=False, description="Deceased.")
    grabadresse: str | None = Field(
        default=None,
        description="Grave/burial site address, recorded for deceased members.",
    )
    parent_id: int = Field(
        default=0,
        description="id of the member who sponsored this member's admission, or 0.",
    )
    parent_cn: str = ""
    default_image: int | None = None

    chroniclemail: bool = False
    auth_locked: bool = True
    email: str | None = None
    email_verified_at: str | None = None
    url: str | None = None
    mkv_ogv_url: str | None = None

    zustellungen: MemberDeliveryPreference = MemberDeliveryPreference.DEAKTIVIERT
    rufnummer_mobil: str | None = None
    rufnummer_privat: str | None = None
    rufnummer_beruf: str | None = None

    adresse_privat_anschrift: str | None = None
    adresse_privat_plz: str | None = None
    adresse_privat_ort: str | None = None
    adresse_privat_land: str | None = None
    adresse_beruf_anschrift: str | None = None
    adresse_beruf_plz: str | None = None
    adresse_beruf_ort: str | None = None
    adresse_beruf_land: str | None = None

    arbeitgeber: str | None = None
    taetigkeit: str | None = None
    mitgliedschaften: str | None = None
    verbandchargen: str | None = None
    anmerkungen: str | None = None

    geburtsdatum: str | None = None
    geburtsdatum_accuracy: int = Field(
        default=0, description=_DATE_ACCURACY_DESCRIPTION
    )
    aufnahmedatum: str | None = None
    aufnahmedatum_accuracy: int = Field(
        default=0, description=_DATE_ACCURACY_DESCRIPTION
    )
    branderdatum: str | None = None
    branderdatum_accuracy: int = Field(
        default=0, description=_DATE_ACCURACY_DESCRIPTION
    )
    burschungsdatum: str | None = None
    burschungsdatum_accuracy: int = Field(
        default=0, description=_DATE_ACCURACY_DESCRIPTION
    )
    philistrierungsdatum: str | None = None
    philistrierungsdatum_accuracy: int = Field(
        default=0, description=_DATE_ACCURACY_DESCRIPTION
    )
    entlassungsdatum: str | None = None
    entlassungsdatum_accuracy: int = Field(
        default=0, description=_DATE_ACCURACY_DESCRIPTION
    )
    sterbedatum: str | None = None
    sterbedatum_accuracy: int = Field(default=0, description=_DATE_ACCURACY_DESCRIPTION)

    roles_history: list[RoleHistoryResponse] = []
    badges: list[BadgeDetailResponse] = []
    keys: list[KeyDetailResponse] = []
    tree: dict[str, object] = {}


class MemberSelfServiceDetailResponse(BaseModel):
    """Self-service view of a member's own Stammdaten - same field subset
    as MemberSelfServiceSaveRequest, output-shaped. Deliberately NOT a
    filtered MemberDetailResponse: no admin-only field values are put on
    the wire to the member's own client at all, same principle as
    FeeMemberSelfResponse in app/schemas/p4x.py excluding p4x_comment."""

    id: int
    cn: str
    vortitel: str | None = None
    vorname: str | None = None
    nachname: str | None = None
    nachname_geburt: str | None = None
    nachtitel: str | None = None
    couleurname: str | None = None

    email: str | None = None
    url: str | None = None
    mkv_ogv_url: str | None = None
    rufnummer_mobil: str | None = None
    rufnummer_privat: str | None = None
    rufnummer_beruf: str | None = None

    zustellungen: MemberDeliveryPreference = MemberDeliveryPreference.DEAKTIVIERT

    adresse_privat_anschrift: str | None = None
    adresse_privat_plz: str | None = None
    adresse_privat_ort: str | None = None
    adresse_privat_land: str | None = None
    adresse_beruf_anschrift: str | None = None
    adresse_beruf_plz: str | None = None
    adresse_beruf_ort: str | None = None
    adresse_beruf_land: str | None = None

    arbeitgeber: str | None = None
    taetigkeit: str | None = None
    mitgliedschaften: str | None = None
    verbandchargen: str | None = None


# --- Member Save Request ---

# Shared validator bodies, used by both MemberSaveRequest (all fields) and
# MemberSelfServiceSaveRequest (the "echte Stammdaten" subset) - every one
# of these validated fields also happens to be self-service-editable, so
# duplicating the same six checks across both models would be a real DRY
# violation, not just superficial similarity. Each model still declares its
# own @field_validator/@model_validator methods (Pydantic v2 requires the
# decorator on the class that owns the field), they just delegate to these
# module-level functions instead of repeating the logic.


def _validate_max_32(v: str | None) -> str | None:
    if v and len(v) > 32:
        msg = "Maximal 32 Zeichen."
        raise ValueError(msg)
    return v


def _validate_max_64(v: str | None) -> str | None:
    if v and len(v) > 64:
        msg = "Maximal 64 Zeichen."
        raise ValueError(msg)
    return v


def _validate_phone(v: str | None) -> str | None:
    if v and not PHONE_REGEX.match(v):
        msg = "Ungültiges Telefonnummernformat."
        raise ValueError(msg)
    return v


def _validate_plz_max_8(v: str | None) -> str | None:
    if v and len(v) > 8:
        msg = "PLZ maximal 8 Zeichen."
        raise ValueError(msg)
    return v


def _validate_ort_land_max_32(v: str | None) -> str | None:
    if v and len(v) > 32:
        msg = "Maximal 32 Zeichen."
        raise ValueError(msg)
    return v


def _validate_url(v: str | None) -> str | None:
    if v and not re.match(r"^https?://", v, re.IGNORECASE):
        msg = "Muss mit http:// oder https:// beginnen."
        raise ValueError(msg)
    return v


def _require_nachname_or_couleurname(
    nachname: str | None, couleurname: str | None
) -> None:
    if not nachname and not couleurname:
        msg = "Nachname oder Couleurname muss angegeben werden."
        raise ValueError(msg)


class MemberSaveRequest(StrictInputModel):
    vortitel: str | None = None
    vorname: str | None = None
    nachname: str | None = None
    nachname_geburt: str | None = None
    nachtitel: str | None = None
    couleurname: str | None = None
    org_id: str
    state_id: str | None = None
    gruender: bool = False
    entlassen: bool = False
    verstorben: bool = False
    parent_id: int = 0
    grabadresse: str | None = None

    # strict=False: JSON has no native date type, dates always arrive as
    # ISO strings — the model-level strict=True would otherwise reject
    # them outright (it requires an actual `date` object, not a string).
    geburtsdatum: date | None = Field(default=None, strict=False)
    geburtsdatum_accuracy: int = Field(
        default=0, ge=0, le=3, description=_DATE_ACCURACY_DESCRIPTION
    )
    aufnahmedatum: date | None = Field(default=None, strict=False)
    aufnahmedatum_accuracy: int = Field(
        default=0, ge=0, le=3, description=_DATE_ACCURACY_DESCRIPTION
    )
    branderdatum: date | None = Field(default=None, strict=False)
    branderdatum_accuracy: int = Field(
        default=0, ge=0, le=3, description=_DATE_ACCURACY_DESCRIPTION
    )
    burschungsdatum: date | None = Field(default=None, strict=False)
    burschungsdatum_accuracy: int = Field(
        default=0, ge=0, le=3, description=_DATE_ACCURACY_DESCRIPTION
    )
    philistrierungsdatum: date | None = Field(default=None, strict=False)
    philistrierungsdatum_accuracy: int = Field(
        default=0, ge=0, le=3, description=_DATE_ACCURACY_DESCRIPTION
    )
    entlassungsdatum: date | None = Field(default=None, strict=False)
    entlassungsdatum_accuracy: int = Field(
        default=0, ge=0, le=3, description=_DATE_ACCURACY_DESCRIPTION
    )
    sterbedatum: date | None = Field(default=None, strict=False)
    sterbedatum_accuracy: int = Field(
        default=0, ge=0, le=3, description=_DATE_ACCURACY_DESCRIPTION
    )

    email: EmailStr | None = Field(default=None, max_length=128)
    url: str | None = Field(default=None, max_length=128)
    mkv_ogv_url: str | None = Field(default=None, max_length=128)
    rufnummer_mobil: str | None = None
    rufnummer_privat: str | None = None
    rufnummer_beruf: str | None = None

    # strict=False: the wire value is the enum's raw string, not an actual
    # MemberDeliveryPreference instance (same reasoning as the date fields
    # above — JSON has no native enum type either).
    zustellungen: MemberDeliveryPreference = Field(
        default=MemberDeliveryPreference.DEAKTIVIERT, strict=False
    )

    adresse_privat_anschrift: str | None = None
    adresse_privat_plz: str | None = None
    adresse_privat_ort: str | None = None
    adresse_privat_land: str | None = None
    adresse_beruf_anschrift: str | None = None
    adresse_beruf_plz: str | None = None
    adresse_beruf_ort: str | None = None
    adresse_beruf_land: str | None = None

    arbeitgeber: str | None = None
    taetigkeit: str | None = None
    mitgliedschaften: str | None = None
    verbandchargen: str | None = None
    anmerkungen: str | None = None

    chroniclemail: bool = False
    auth_locked: bool = True

    roles_history: list[RoleHistoryEntry] = Field(default_factory=list)
    badges: list[BadgeEntry] = Field(default_factory=list)
    keys: list[KeyEntry] = Field(default_factory=list)

    @field_validator("vortitel", "nachtitel", mode="before")
    @classmethod
    def max_32(cls, v: str | None) -> str | None:
        return _validate_max_32(v)

    @field_validator(
        "vorname",
        "nachname",
        "nachname_geburt",
        "couleurname",
        "arbeitgeber",
        "taetigkeit",
        mode="before",
    )
    @classmethod
    def max_64(cls, v: str | None) -> str | None:
        return _validate_max_64(v)

    @field_validator(
        "rufnummer_mobil",
        "rufnummer_privat",
        "rufnummer_beruf",
        mode="before",
    )
    @classmethod
    def valid_phone(cls, v: str | None) -> str | None:
        return _validate_phone(v)

    @field_validator(
        "adresse_privat_plz",
        "adresse_beruf_plz",
        mode="before",
    )
    @classmethod
    def plz_max_8(cls, v: str | None) -> str | None:
        return _validate_plz_max_8(v)

    @field_validator(
        "adresse_privat_ort",
        "adresse_privat_land",
        "adresse_beruf_ort",
        "adresse_beruf_land",
        mode="before",
    )
    @classmethod
    def ort_land_max_32(cls, v: str | None) -> str | None:
        return _validate_ort_land_max_32(v)

    @field_validator("url", "mkv_ogv_url", mode="before")
    @classmethod
    def valid_url(cls, v: str | None) -> str | None:
        return _validate_url(v)

    @model_validator(mode="after")
    def require_nachname_or_couleurname(self) -> Self:
        _require_nachname_or_couleurname(self.nachname, self.couleurname)
        return self


class MemberSelfServiceSaveRequest(StrictInputModel):
    """Self-service subset of MemberSaveRequest - "echte Stammdaten" only.

    Deliberately excludes org_id/state_id/gruender/entlassen/verstorben/
    grabadresse/parent_id/auth_locked/chroniclemail (own dedicated
    unapproved toggle, see PATCH /members/me/chroniclemail)/anmerkungen
    (risk of being an admin-internal note, same reasoning as p4x_comment)/
    roles_history/badges/keys, as well as geburtsdatum(_accuracy) and the
    membership-milestone dates (aufnahmedatum/branderdatum/
    burschungsdatum/philistrierungsdatum) - official record, not everyday
    personal data, and not something that legitimately changes after the
    fact. All of those stay admin-only via the existing MemberSaveRequest/
    MemberEditView.vue.
    """

    vortitel: str | None = None
    vorname: str | None = None
    nachname: str | None = None
    nachname_geburt: str | None = None
    nachtitel: str | None = None
    couleurname: str | None = None

    email: EmailStr | None = Field(default=None, max_length=128)
    url: str | None = Field(default=None, max_length=128)
    mkv_ogv_url: str | None = Field(default=None, max_length=128)
    rufnummer_mobil: str | None = None
    rufnummer_privat: str | None = None
    rufnummer_beruf: str | None = None

    zustellungen: MemberDeliveryPreference = Field(
        default=MemberDeliveryPreference.DEAKTIVIERT, strict=False
    )

    adresse_privat_anschrift: str | None = None
    adresse_privat_plz: str | None = None
    adresse_privat_ort: str | None = None
    adresse_privat_land: str | None = None
    adresse_beruf_anschrift: str | None = None
    adresse_beruf_plz: str | None = None
    adresse_beruf_ort: str | None = None
    adresse_beruf_land: str | None = None

    arbeitgeber: str | None = None
    taetigkeit: str | None = None
    mitgliedschaften: str | None = None
    verbandchargen: str | None = None

    @field_validator("vortitel", "nachtitel", mode="before")
    @classmethod
    def max_32(cls, v: str | None) -> str | None:
        return _validate_max_32(v)

    @field_validator(
        "vorname",
        "nachname",
        "nachname_geburt",
        "couleurname",
        "arbeitgeber",
        "taetigkeit",
        mode="before",
    )
    @classmethod
    def max_64(cls, v: str | None) -> str | None:
        return _validate_max_64(v)

    @field_validator(
        "rufnummer_mobil",
        "rufnummer_privat",
        "rufnummer_beruf",
        mode="before",
    )
    @classmethod
    def valid_phone(cls, v: str | None) -> str | None:
        return _validate_phone(v)

    @field_validator(
        "adresse_privat_plz",
        "adresse_beruf_plz",
        mode="before",
    )
    @classmethod
    def plz_max_8(cls, v: str | None) -> str | None:
        return _validate_plz_max_8(v)

    @field_validator(
        "adresse_privat_ort",
        "adresse_privat_land",
        "adresse_beruf_ort",
        "adresse_beruf_land",
        mode="before",
    )
    @classmethod
    def ort_land_max_32(cls, v: str | None) -> str | None:
        return _validate_ort_land_max_32(v)

    @field_validator("url", "mkv_ogv_url", mode="before")
    @classmethod
    def valid_url(cls, v: str | None) -> str | None:
        return _validate_url(v)

    @model_validator(mode="after")
    def require_nachname_or_couleurname(self) -> Self:
        _require_nachname_or_couleurname(self.nachname, self.couleurname)
        return self


# --- Contact Responses ---


class ContactDetailResponse(BaseModel):
    id: int
    cn: str
    kontakttyp: ContactType
    anrede: str | None = None
    name: str
    couleurname: str | None = None
    org_id: str | None = None
    org_label: str | None = None
    adresse_anschrift: str | None = None
    adresse_plz: str | None = None
    adresse_ort: str | None = None
    adresse_land: str | None = None
    zustellungen: bool = False
    email: str | None = None
    rufnummer: str | None = None
    datum: str | None = None
    datum_accuracy: int = Field(default=0, description=_DATE_ACCURACY_DESCRIPTION)
    default_image: int | None = None
    anmerkungen: str | None = None


class ContactSaveRequest(StrictInputModel):
    # strict=False: same enum-from-raw-string reasoning as MemberSaveRequest
    # above.
    kontakttyp: ContactType = Field(strict=False)
    anrede: str | None = None
    name: str
    couleurname: str | None = None
    org_id: str | None = None
    adresse_anschrift: str | None = None
    adresse_plz: str | None = None
    adresse_ort: str | None = None
    adresse_land: str | None = None
    zustellungen: bool = False
    email: EmailStr | None = Field(default=None, max_length=128)
    rufnummer: str | None = None
    datum: date | None = Field(default=None, strict=False)
    datum_accuracy: int = Field(
        default=0, ge=0, le=3, description=_DATE_ACCURACY_DESCRIPTION
    )
    anmerkungen: str | None = None

    @field_validator("name", "couleurname", mode="before")
    @classmethod
    def name_max_64(cls, v: str | None) -> str | None:
        if v and len(v) > 64:
            msg = "Maximal 64 Zeichen."
            raise ValueError(msg)
        return v

    @field_validator("anrede", mode="before")
    @classmethod
    def anrede_max_32(cls, v: str | None) -> str | None:
        if v and len(v) > 32:
            msg = "Maximal 32 Zeichen."
            raise ValueError(msg)
        return v

    @field_validator("adresse_plz", mode="before")
    @classmethod
    def plz_max_8(cls, v: str | None) -> str | None:
        if v and len(v) > 8:
            msg = "PLZ maximal 8 Zeichen."
            raise ValueError(msg)
        return v

    @field_validator(
        "adresse_ort",
        "adresse_land",
        mode="before",
    )
    @classmethod
    def ort_land_max_32(cls, v: str | None) -> str | None:
        if v and len(v) > 32:
            msg = "Maximal 32 Zeichen."
            raise ValueError(msg)
        return v

    @field_validator("rufnummer", mode="before")
    @classmethod
    def valid_phone(cls, v: str | None) -> str | None:
        if v and not PHONE_REGEX.match(v):
            msg = "Ungültiges Telefonnummernformat."
            raise ValueError(msg)
        return v


# --- Image Responses ---


class ImageUpdateRequest(StrictInputModel):
    description: str | None = None
    default: bool = False

    @field_validator("description", mode="before")
    @classmethod
    def desc_max_100(cls, v: str | None) -> str | None:
        if v and len(v) > 100:
            msg = "Maximal 100 Zeichen."
            raise ValueError(msg)
        return v


# --- Roles List ---


class RoleMemberEntry(BaseModel):
    id: int
    cn: str
    startdate: date
    enddate: date | None = None


class RolesListEntry(BaseModel):
    label: str | None = None
    group: str | None = None
    vbw: RoleMemberEntry | None = None
    vbn: RoleMemberEntry | None = None


class RolesListResponse(BaseModel):
    semester: str
    year: int
    roles: list[RolesListEntry]


# --- Stats ---


class MemberStatsResponse(BaseModel):
    present: dict[str, int] = {}
    dismissed: dict[str, int] = {}
    dead: dict[str, int] = {}
    dismissed_dead: dict[str, int] = {}


class ContactStatsResponse(BaseModel):
    common: int = 0
    vbw: int = 0
    vbn: int = 0


class StatsResponse(BaseModel):
    member: MemberStatsResponse
    contact: ContactStatsResponse


# --- Export ---


class ChangeLogEntry(BaseModel):
    id: int
    modified_at: UtcDatetime | None
    modified_by_name: str | None
    action: str
    key: str
    old: str | None
    new: str | None


class MemberAuthActivityResponse(BaseModel):
    auth_lastlogin: UtcDatetime | None = None
    auth_lastsignal: UtcDatetime | None = None
    auth_lastlogout: UtcDatetime | None = None


# --- Member Change Requests ---


class MemberChangeRequestSummary(BaseModel):
    id: int
    member_id: int
    member_cn: str
    member_org_id: str | None = None
    field_count: int
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None


class MemberChangeRequestListResponse(BaseModel):
    items: list[MemberChangeRequestSummary]
    total: int


class ChangeRequestFieldDiff(BaseModel):
    field: str
    old: str | None
    new: str | None


class MemberChangeRequestDetailResponse(BaseModel):
    id: int
    member_id: int
    member_cn: str
    status: MemberChangeRequestStatus
    created_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None
    resolved_at: UtcDatetime | None = None
    resolved_by_name: str | None = None
    diff: list[ChangeRequestFieldDiff]
    field_decisions: dict[str, str] | None = None


class MyChangeRequestResponse(BaseModel):
    id: int
    created_at: UtcDatetime | None = None
    proposed_fields: dict[str, object]


class MemberChangeRequestDecisionRequest(StrictInputModel):
    field_decisions: dict[str, Literal["approved", "rejected"]]


class SearchResultItem(BaseModel):
    type: Literal["member", "contact"]
    id: int
    label: str


class SearchResponse(BaseModel):
    data: list[SearchResultItem]


class ParentSearchResultItem(BaseModel):
    id: int
    cn: str


class ParentSearchResponse(BaseModel):
    data: list[ParentSearchResultItem]


class ImageOwnerResponse(BaseModel):
    type: Literal["member", "contact"]
    id: int
    cn: str
    org_id: str | None
    default_image: int | None


class ImageListItem(BaseModel):
    id: int
    type: str | None
    height: int | None
    width: int | None
    size: int | None
    description: str | None
    default: bool


class ImageListResponse(BaseModel):
    owner: ImageOwnerResponse
    images: list[ImageListItem]


class ExportConfigResponse(BaseModel):
    modules: list[IdLabelOption]
    orgs: list[IdLabelOption]
    states: list[IdLabelOption]
    flags: dict[str, str]


class ExportRequest(StrictInputModel):
    module: str
    # Dynamic {org_id}_{state_id} / {org_id}_contacts matrix keys (e.g.
    # "vbw_fu") - the DB-driven org/state combinations can't be static
    # Pydantic fields, so they live in this explicit dict instead of
    # relying on extra="allow" at the top level (which would silently
    # swallow any misspelled/renamed flag too).
    selections: dict[str, bool] = Field(default_factory=dict)
    include_disabled_delivery: bool = False
    include_dead: bool = False
    include_common_contacts: bool = False
    only_without_email: bool = False
