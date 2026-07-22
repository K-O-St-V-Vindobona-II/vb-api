import re
from datetime import UTC, date, datetime
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.models.enums import (
    BadgeGroup,
    ContactType,
    MemberDeliveryPreference,
    RoleGroup,
)


def _ensure_utc(v: datetime | None) -> datetime | None:
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=UTC)
    return v


UtcDatetime = Annotated[datetime, BeforeValidator(_ensure_utc)]

PHONE_REGEX = re.compile(r"^\+?[\d\s/\- ]+$")


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
    children: list["TreeNodeResponse"] = []


# --- Member Responses ---


class MemberDismissedResponse(BaseModel):
    id: int
    cn: str
    org_id: str | None = None
    dataprotection: str = "dismissed"


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
    gruender: bool = False
    entlassen: bool = False
    verstorben: bool = False
    grabadresse: str | None = None
    parent_id: int = 0
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
    geburtsdatum_accuracy: int = 0
    aufnahmedatum: str | None = None
    aufnahmedatum_accuracy: int = 0
    branderdatum: str | None = None
    branderdatum_accuracy: int = 0
    burschungsdatum: str | None = None
    burschungsdatum_accuracy: int = 0
    philistrierungsdatum: str | None = None
    philistrierungsdatum_accuracy: int = 0
    entlassungsdatum: str | None = None
    entlassungsdatum_accuracy: int = 0
    sterbedatum: str | None = None
    sterbedatum_accuracy: int = 0

    roles_history: list[RoleHistoryResponse] = []
    badges: list[BadgeDetailResponse] = []
    keys: list[KeyDetailResponse] = []
    tree: dict[str, object] = {}


# --- Member Save Request ---


class MemberSaveRequest(BaseModel):
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

    geburtsdatum: date | None = None
    geburtsdatum_accuracy: int = Field(default=0, ge=0, le=3)
    aufnahmedatum: date | None = None
    aufnahmedatum_accuracy: int = Field(default=0, ge=0, le=3)
    branderdatum: date | None = None
    branderdatum_accuracy: int = Field(default=0, ge=0, le=3)
    burschungsdatum: date | None = None
    burschungsdatum_accuracy: int = Field(default=0, ge=0, le=3)
    philistrierungsdatum: date | None = None
    philistrierungsdatum_accuracy: int = Field(default=0, ge=0, le=3)
    entlassungsdatum: date | None = None
    entlassungsdatum_accuracy: int = Field(default=0, ge=0, le=3)
    sterbedatum: date | None = None
    sterbedatum_accuracy: int = Field(default=0, ge=0, le=3)

    email: EmailStr | None = Field(default=None, max_length=128)
    url: str | None = Field(default=None, max_length=128)
    mkv_ogv_url: str | None = Field(default=None, max_length=128)
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
    anmerkungen: str | None = None

    chroniclemail: bool = False
    auth_locked: bool = True

    roles_history: list[RoleHistoryEntry] = []
    badges: list[BadgeEntry] = []
    keys: list[KeyEntry] = []

    @field_validator("vortitel", "nachtitel", mode="before")
    @classmethod
    def max_32(cls, v: str | None) -> str | None:
        if v and len(v) > 32:
            msg = "Maximal 32 Zeichen."
            raise ValueError(msg)
        return v

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
        if v and len(v) > 64:
            msg = "Maximal 64 Zeichen."
            raise ValueError(msg)
        return v

    @field_validator(
        "rufnummer_mobil",
        "rufnummer_privat",
        "rufnummer_beruf",
        mode="before",
    )
    @classmethod
    def valid_phone(cls, v: str | None) -> str | None:
        if v and not PHONE_REGEX.match(v):
            msg = "Ungültiges Telefonnummernformat."
            raise ValueError(msg)
        return v

    @field_validator(
        "adresse_privat_plz",
        "adresse_beruf_plz",
        mode="before",
    )
    @classmethod
    def plz_max_8(cls, v: str | None) -> str | None:
        if v and len(v) > 8:
            msg = "PLZ maximal 8 Zeichen."
            raise ValueError(msg)
        return v

    @field_validator(
        "adresse_privat_ort",
        "adresse_privat_land",
        "adresse_beruf_ort",
        "adresse_beruf_land",
        mode="before",
    )
    @classmethod
    def ort_land_max_32(cls, v: str | None) -> str | None:
        if v and len(v) > 32:
            msg = "Maximal 32 Zeichen."
            raise ValueError(msg)
        return v

    @field_validator("url", "mkv_ogv_url", mode="before")
    @classmethod
    def valid_url(cls, v: str | None) -> str | None:
        if v and not re.match(
            r"^https?://",
            v,
            re.IGNORECASE,
        ):
            msg = "Muss mit http:// oder https:// beginnen."
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def require_nachname_or_couleurname(self) -> Self:
        if not self.nachname and not self.couleurname:
            msg = "Nachname oder Couleurname muss angegeben werden."
            raise ValueError(msg)
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
    datum_accuracy: int = 0
    default_image: int | None = None
    anmerkungen: str | None = None


class ContactSaveRequest(BaseModel):
    kontakttyp: ContactType
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
    datum: date | None = None
    datum_accuracy: int = Field(default=0, ge=0, le=3)
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


class ImageResponse(BaseModel):
    id: int
    type: str | None = None
    height: int | None = None
    width: int | None = None
    size: int | None = None
    description: str | None = None
    default: bool = False
    model_config = ConfigDict(from_attributes=True)


class ImageOwnerResponse(BaseModel):
    type: str
    id: int
    cn: str
    org_id: str | None = None
    default_image: int | None = None


class ImageGalleryResponse(BaseModel):
    owner: ImageOwnerResponse
    images: list[ImageResponse]


class ImageUpdateRequest(BaseModel):
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


# --- Search ---


class SearchResult(BaseModel):
    type: str
    id: int
    label: str


# --- Stats ---


class OrgCountResponse(BaseModel):
    vbw: int = 0
    vbn: int = 0


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


class ExportRequest(BaseModel):
    module: str
    include_disabled_delivery: bool = False
    include_dead: bool = False
    include_common_contacts: bool = False
    only_without_email: bool = False
    model_config = ConfigDict(extra="allow")
