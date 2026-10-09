"""Pure unit tests for Pydantic validators in app/schemas/standesdb.py."""

from datetime import date, datetime

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas import p4x, public_gallery, standesdb
from app.schemas.standesdb import (
    MAX_ADDRESS_LINE_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_PHONE_LENGTH,
    ContactSaveRequest,
    ImageUpdateRequest,
    MemberSaveRequest,
    MemberSelfServiceSaveRequest,
)

# ---------------------------------------------------------------------------
# Helpers — minimal valid payloads
# ---------------------------------------------------------------------------


def _valid_member_data(**overrides: object) -> dict[str, object]:
    """Return the minimum valid MemberSaveRequest payload."""
    base: dict[str, object] = {
        "nachname": "Muster",
        "org_id": "vbw",
    }
    base.update(overrides)
    return base


def _valid_self_service_data(**overrides: object) -> dict[str, object]:
    """Return the minimum valid MemberSelfServiceSaveRequest payload."""
    base: dict[str, object] = {"nachname": "Muster"}
    base.update(overrides)
    return base


def _valid_contact_data(**overrides: object) -> dict[str, object]:
    """Return the minimum valid ContactSaveRequest payload."""
    base: dict[str, object] = {
        "kontakttyp": "person",
        "name": "Test Kontakt",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# MemberSaveRequest — max_32 (vortitel, nachtitel)
# ---------------------------------------------------------------------------


class TestMemberMax32:
    def test_vortitel_exactly_32_passes(self) -> None:
        data = _valid_member_data(vortitel="A" * 32)
        m = MemberSaveRequest(**data)
        assert m.vortitel == "A" * 32

    def test_vortitel_over_32_rejected(self) -> None:
        data = _valid_member_data(vortitel="A" * 33)
        with pytest.raises(ValidationError, match="32 Zeichen"):
            MemberSaveRequest(**data)

    def test_nachtitel_over_32_rejected(self) -> None:
        data = _valid_member_data(nachtitel="B" * 33)
        with pytest.raises(ValidationError, match="32 Zeichen"):
            MemberSaveRequest(**data)

    def test_vortitel_none_passes(self) -> None:
        data = _valid_member_data(vortitel=None)
        m = MemberSaveRequest(**data)
        assert m.vortitel is None


# ---------------------------------------------------------------------------
# MemberSaveRequest — max_64 (vorname, nachname, couleurname, etc.)
# ---------------------------------------------------------------------------


class TestMemberMax64:
    def test_vorname_over_64_rejected(self) -> None:
        data = _valid_member_data(vorname="X" * 65)
        with pytest.raises(ValidationError, match="64 Zeichen"):
            MemberSaveRequest(**data)

    def test_nachname_over_64_rejected(self) -> None:
        data = _valid_member_data(nachname="X" * 65)
        with pytest.raises(ValidationError, match="64 Zeichen"):
            MemberSaveRequest(**data)

    def test_nachname_geburt_over_64_rejected(self) -> None:
        data = _valid_member_data(nachname_geburt="X" * 65)
        with pytest.raises(ValidationError, match="64 Zeichen"):
            MemberSaveRequest(**data)

    def test_couleurname_over_64_rejected(self) -> None:
        data = _valid_member_data(couleurname="X" * 65)
        with pytest.raises(ValidationError, match="64 Zeichen"):
            MemberSaveRequest(**data)

    def test_arbeitgeber_over_64_rejected(self) -> None:
        data = _valid_member_data(arbeitgeber="X" * 65)
        with pytest.raises(ValidationError, match="64 Zeichen"):
            MemberSaveRequest(**data)

    def test_taetigkeit_over_64_rejected(self) -> None:
        data = _valid_member_data(taetigkeit="X" * 65)
        with pytest.raises(ValidationError, match="64 Zeichen"):
            MemberSaveRequest(**data)

    def test_vorname_exactly_64_passes(self) -> None:
        data = _valid_member_data(vorname="X" * 64)
        m = MemberSaveRequest(**data)
        assert m.vorname == "X" * 64


# ---------------------------------------------------------------------------
# MemberSaveRequest — valid_zustellungen
# ---------------------------------------------------------------------------


class TestMemberZustellungen:
    def test_adresse_privat_passes(self) -> None:
        data = _valid_member_data(zustellungen="adresse_privat")
        m = MemberSaveRequest(**data)
        assert m.zustellungen == "adresse_privat"

    def test_adresse_beruf_passes(self) -> None:
        data = _valid_member_data(zustellungen="adresse_beruf")
        m = MemberSaveRequest(**data)
        assert m.zustellungen == "adresse_beruf"

    def test_deaktiviert_passes(self) -> None:
        data = _valid_member_data(zustellungen="deaktiviert")
        m = MemberSaveRequest(**data)
        assert m.zustellungen == "deaktiviert"

    def test_invalid_value_rejected(self) -> None:
        data = _valid_member_data(zustellungen="email")
        with pytest.raises(ValidationError, match="adresse_privat"):
            MemberSaveRequest(**data)


# ---------------------------------------------------------------------------
# MemberSaveRequest — valid_phone
# ---------------------------------------------------------------------------


class TestMemberPhone:
    def test_valid_phone_international(self) -> None:
        data = _valid_member_data(rufnummer_mobil="+43 660 123 4567")
        m = MemberSaveRequest(**data)
        assert m.rufnummer_mobil == "+43 660 123 4567"

    def test_valid_phone_with_slash(self) -> None:
        data = _valid_member_data(rufnummer_privat="01/234 5678")
        m = MemberSaveRequest(**data)
        assert m.rufnummer_privat == "01/234 5678"

    def test_invalid_phone_letters_rejected(self) -> None:
        data = _valid_member_data(rufnummer_mobil="abc123")
        with pytest.raises(ValidationError, match="Telefonnummernformat"):
            MemberSaveRequest(**data)

    def test_invalid_phone_special_chars_rejected(self) -> None:
        data = _valid_member_data(rufnummer_beruf="(01) 234-5678!")
        with pytest.raises(ValidationError, match="Telefonnummernformat"):
            MemberSaveRequest(**data)

    def test_phone_none_passes(self) -> None:
        data = _valid_member_data(rufnummer_mobil=None)
        m = MemberSaveRequest(**data)
        assert m.rufnummer_mobil is None


# ---------------------------------------------------------------------------
# MemberSaveRequest — plz_max_8
# ---------------------------------------------------------------------------


class TestMemberPlz:
    def test_plz_exactly_8_passes(self) -> None:
        data = _valid_member_data(adresse_privat_plz="12345678")
        m = MemberSaveRequest(**data)
        assert m.adresse_privat_plz == "12345678"

    def test_plz_over_8_rejected(self) -> None:
        data = _valid_member_data(adresse_privat_plz="123456789")
        with pytest.raises(ValidationError, match="PLZ maximal 8"):
            MemberSaveRequest(**data)

    def test_beruf_plz_over_8_rejected(self) -> None:
        data = _valid_member_data(adresse_beruf_plz="123456789")
        with pytest.raises(ValidationError, match="PLZ maximal 8"):
            MemberSaveRequest(**data)


# ---------------------------------------------------------------------------
# MemberSaveRequest — ort_land_max_32
# ---------------------------------------------------------------------------


class TestMemberOrtLand:
    def test_privat_ort_over_32_rejected(self) -> None:
        data = _valid_member_data(adresse_privat_ort="X" * 33)
        with pytest.raises(ValidationError, match="32 Zeichen"):
            MemberSaveRequest(**data)

    def test_privat_land_over_32_rejected(self) -> None:
        data = _valid_member_data(adresse_privat_land="X" * 33)
        with pytest.raises(ValidationError, match="32 Zeichen"):
            MemberSaveRequest(**data)

    def test_beruf_ort_over_32_rejected(self) -> None:
        data = _valid_member_data(adresse_beruf_ort="X" * 33)
        with pytest.raises(ValidationError, match="32 Zeichen"):
            MemberSaveRequest(**data)

    def test_beruf_land_over_32_rejected(self) -> None:
        data = _valid_member_data(adresse_beruf_land="X" * 33)
        with pytest.raises(ValidationError, match="32 Zeichen"):
            MemberSaveRequest(**data)

    def test_ort_exactly_32_passes(self) -> None:
        data = _valid_member_data(adresse_privat_ort="X" * 32)
        m = MemberSaveRequest(**data)
        assert m.adresse_privat_ort == "X" * 32


# ---------------------------------------------------------------------------
# MemberSaveRequest — valid_url
# ---------------------------------------------------------------------------


class TestMemberUrl:
    def test_http_url_passes(self) -> None:
        data = _valid_member_data(url="http://example.com")
        m = MemberSaveRequest(**data)
        assert m.url == "http://example.com"

    def test_https_url_passes(self) -> None:
        data = _valid_member_data(url="https://example.com")
        m = MemberSaveRequest(**data)
        assert m.url == "https://example.com"

    def test_url_without_scheme_rejected(self) -> None:
        data = _valid_member_data(url="example.com")
        with pytest.raises(ValidationError, match="http://"):
            MemberSaveRequest(**data)

    def test_ftp_url_rejected(self) -> None:
        data = _valid_member_data(url="ftp://example.com")
        with pytest.raises(ValidationError, match="http://"):
            MemberSaveRequest(**data)

    def test_mkv_ogv_url_without_scheme_rejected(self) -> None:
        data = _valid_member_data(mkv_ogv_url="www.example.com")
        with pytest.raises(ValidationError, match="http://"):
            MemberSaveRequest(**data)

    def test_url_none_passes(self) -> None:
        data = _valid_member_data(url=None)
        m = MemberSaveRequest(**data)
        assert m.url is None


# ---------------------------------------------------------------------------
# MemberSaveRequest — require_nachname_or_couleurname
# ---------------------------------------------------------------------------


class TestMemberRequireNames:
    def test_nachname_only_passes(self) -> None:
        data = _valid_member_data(nachname="Muster", couleurname=None)
        m = MemberSaveRequest(**data)
        assert m.nachname == "Muster"

    def test_couleurname_only_passes(self) -> None:
        data = _valid_member_data(nachname=None, couleurname="Maxl")
        m = MemberSaveRequest(**data)
        assert m.couleurname == "Maxl"

    def test_both_present_passes(self) -> None:
        data = _valid_member_data(nachname="Muster", couleurname="Maxl")
        m = MemberSaveRequest(**data)
        assert m.nachname == "Muster"

    def test_neither_present_rejected(self) -> None:
        data = _valid_member_data(nachname=None, couleurname=None)
        with pytest.raises(ValidationError, match="Nachname oder Couleurname"):
            MemberSaveRequest(**data)


# ---------------------------------------------------------------------------
# ContactSaveRequest — valid_kontakttyp
# ---------------------------------------------------------------------------


class TestContactKontakttyp:
    def test_person_passes(self) -> None:
        data = _valid_contact_data(kontakttyp="person")
        c = ContactSaveRequest(**data)
        assert c.kontakttyp == "person"

    def test_organisation_passes(self) -> None:
        data = _valid_contact_data(kontakttyp="organisation")
        c = ContactSaveRequest(**data)
        assert c.kontakttyp == "organisation"

    def test_invalid_type_rejected(self) -> None:
        data = _valid_contact_data(kontakttyp="firma")
        with pytest.raises(ValidationError, match="person"):
            ContactSaveRequest(**data)


# ---------------------------------------------------------------------------
# ContactSaveRequest — name_max_64
# ---------------------------------------------------------------------------


class TestContactNameMax64:
    def test_name_over_64_rejected(self) -> None:
        data = _valid_contact_data(name="X" * 65)
        with pytest.raises(ValidationError, match="64 Zeichen"):
            ContactSaveRequest(**data)

    def test_couleurname_over_64_rejected(self) -> None:
        data = _valid_contact_data(couleurname="X" * 65)
        with pytest.raises(ValidationError, match="64 Zeichen"):
            ContactSaveRequest(**data)


# ---------------------------------------------------------------------------
# ContactSaveRequest — anrede_max_32
# ---------------------------------------------------------------------------


class TestContactAnrede:
    def test_anrede_over_32_rejected(self) -> None:
        data = _valid_contact_data(anrede="X" * 33)
        with pytest.raises(ValidationError, match="32 Zeichen"):
            ContactSaveRequest(**data)

    def test_anrede_exactly_32_passes(self) -> None:
        data = _valid_contact_data(anrede="X" * 32)
        c = ContactSaveRequest(**data)
        assert c.anrede == "X" * 32


# ---------------------------------------------------------------------------
# ContactSaveRequest — plz_max_8
# ---------------------------------------------------------------------------


class TestContactPlz:
    def test_plz_over_8_rejected(self) -> None:
        data = _valid_contact_data(adresse_plz="123456789")
        with pytest.raises(ValidationError, match="PLZ maximal 8"):
            ContactSaveRequest(**data)


# ---------------------------------------------------------------------------
# ContactSaveRequest — ort_land_max_32
# ---------------------------------------------------------------------------


class TestContactOrtLand:
    def test_ort_over_32_rejected(self) -> None:
        data = _valid_contact_data(adresse_ort="X" * 33)
        with pytest.raises(ValidationError, match="32 Zeichen"):
            ContactSaveRequest(**data)

    def test_land_over_32_rejected(self) -> None:
        data = _valid_contact_data(adresse_land="X" * 33)
        with pytest.raises(ValidationError, match="32 Zeichen"):
            ContactSaveRequest(**data)


# ---------------------------------------------------------------------------
# ContactSaveRequest — valid_phone
# ---------------------------------------------------------------------------


class TestContactPhone:
    def test_invalid_phone_rejected(self) -> None:
        data = _valid_contact_data(rufnummer="abc!!!")
        with pytest.raises(ValidationError, match="Telefonnummernformat"):
            ContactSaveRequest(**data)

    def test_valid_phone_passes(self) -> None:
        data = _valid_contact_data(rufnummer="+43 1 234 5678")
        c = ContactSaveRequest(**data)
        assert c.rufnummer == "+43 1 234 5678"


# ---------------------------------------------------------------------------
# ImageUpdateRequest — desc_max_100
# ---------------------------------------------------------------------------


class TestImageDescription:
    def test_description_over_100_rejected(self) -> None:
        with pytest.raises(ValidationError, match="100 Zeichen"):
            ImageUpdateRequest(description="X" * 101)

    def test_description_exactly_100_passes(self) -> None:
        req = ImageUpdateRequest(description="X" * 100)
        assert req.description == "X" * 100

    def test_description_none_passes(self) -> None:
        req = ImageUpdateRequest(description=None)
        assert req.description is None


# ---------------------------------------------------------------------------
# Non-string JSON values end in a 422, never in a TypeError
# ---------------------------------------------------------------------------

_MEMBER_TEXT_FIELDS = [
    "vortitel",
    "vorname",
    "nachname",
    "couleurname",
    "arbeitgeber",
    "rufnummer_mobil",
    "rufnummer_privat",
    "rufnummer_beruf",
    "adresse_privat_plz",
    "adresse_privat_ort",
    "url",
    "mkv_ogv_url",
]
_CONTACT_TEXT_FIELDS = [
    "name",
    "couleurname",
    "anrede",
    "adresse_plz",
    "adresse_ort",
    "rufnummer",
]
_NON_STRING_VALUES = [5, ["x"], {"a": 1}, True]


class TestNonStringValuesAreRejected:
    @pytest.mark.parametrize("value", _NON_STRING_VALUES)
    @pytest.mark.parametrize("field", _MEMBER_TEXT_FIELDS)
    def test_member_save_request(self, field: str, value: object) -> None:
        with pytest.raises(ValidationError):
            MemberSaveRequest(**_valid_member_data(**{field: value}))

    @pytest.mark.parametrize("value", _NON_STRING_VALUES)
    @pytest.mark.parametrize("field", _MEMBER_TEXT_FIELDS)
    def test_member_self_service_save_request(self, field: str, value: object) -> None:
        with pytest.raises(ValidationError):
            MemberSelfServiceSaveRequest(**_valid_self_service_data(**{field: value}))

    @pytest.mark.parametrize("value", _NON_STRING_VALUES)
    @pytest.mark.parametrize("field", _CONTACT_TEXT_FIELDS)
    def test_contact_save_request(self, field: str, value: object) -> None:
        with pytest.raises(ValidationError):
            ContactSaveRequest(**_valid_contact_data(**{field: value}))

    @pytest.mark.parametrize("value", _NON_STRING_VALUES)
    def test_image_update_request(self, value: object) -> None:
        with pytest.raises(ValidationError):
            ImageUpdateRequest(description=value)

    @pytest.mark.parametrize(
        "schema_module", [standesdb, p4x, public_gallery], ids=lambda m: m.__name__
    )
    def test_no_validator_runs_before_type_validation(self, schema_module) -> None:
        offenders = [
            f"{cls.__name__}.{name}"
            for cls in vars(schema_module).values()
            if isinstance(cls, type)
            and issubclass(cls, BaseModel)
            and cls.__module__ == schema_module.__name__
            for name, decorator in cls.__pydantic_decorators__.field_validators.items()
            if decorator.info.mode == "before"
        ]
        assert offenders == []


# ---------------------------------------------------------------------------
# Phone numbers: plain space, slash, hyphen, ASCII digits and a leading plus
# ---------------------------------------------------------------------------


class TestPhoneCharacterSet:
    @pytest.mark.parametrize(
        "number",
        ["+43 664/123-456", "0664 1234567", "01/23456", "+4366412345678", "1"],
    )
    def test_accepted_numbers(self, number: str) -> None:
        assert (
            MemberSaveRequest(
                **_valid_member_data(rufnummer_mobil=number)
            ).rufnummer_mobil
            == number
        )
        assert (
            ContactSaveRequest(**_valid_contact_data(rufnummer=number)).rufnummer
            == number
        )

    def test_trailing_line_break_is_cut_off_before_the_check(self) -> None:
        member = MemberSaveRequest(**_valid_member_data(rufnummer_mobil="0664 123\n"))
        assert member.rufnummer_mobil == "0664 123"
        contact = ContactSaveRequest(**_valid_contact_data(rufnummer="0664 123\n"))
        assert contact.rufnummer == "0664 123"

    @pytest.mark.parametrize(
        "number",
        [
            "0664\t123",
            "0664\n123",
            "٠٦٦٤١٢٣",
            "++43 664",
            "0664 123 (privat)",
            "0664" + chr(0xA0) + "123",
        ],
    )
    def test_rejected_numbers(self, number: str) -> None:
        with pytest.raises(ValidationError, match="Telefonnummernformat"):
            MemberSaveRequest(**_valid_member_data(rufnummer_privat=number))
        with pytest.raises(ValidationError, match="Telefonnummernformat"):
            MemberSelfServiceSaveRequest(
                **_valid_self_service_data(rufnummer_beruf=number)
            )
        with pytest.raises(ValidationError, match="Telefonnummernformat"):
            ContactSaveRequest(**_valid_contact_data(rufnummer=number))


# ---------------------------------------------------------------------------
# Upper bounds of the free-text fields (one case per constant, both sides)
# ---------------------------------------------------------------------------

_MEMBER_BOUNDS = [
    ("rufnummer_mobil", MAX_PHONE_LENGTH, "1"),
    ("rufnummer_privat", MAX_PHONE_LENGTH, "1"),
    ("rufnummer_beruf", MAX_PHONE_LENGTH, "1"),
    ("adresse_privat_anschrift", MAX_ADDRESS_LINE_LENGTH, "a"),
    ("adresse_beruf_anschrift", MAX_ADDRESS_LINE_LENGTH, "a"),
    ("grabadresse", MAX_ADDRESS_LINE_LENGTH, "a"),
    ("mitgliedschaften", MAX_NOTE_LENGTH, "a"),
    ("verbandchargen", MAX_NOTE_LENGTH, "a"),
    ("anmerkungen", MAX_NOTE_LENGTH, "a"),
]
_SELF_SERVICE_BOUNDS = [
    b
    for b in _MEMBER_BOUNDS
    if b[0]
    not in (
        "grabadresse",
        "anmerkungen",
    )
]
_CONTACT_BOUNDS = [
    ("rufnummer", MAX_PHONE_LENGTH, "1"),
    ("adresse_anschrift", MAX_ADDRESS_LINE_LENGTH, "a"),
    ("anmerkungen", MAX_NOTE_LENGTH, "a"),
]


class TestFreeTextBounds:
    def test_constants_match_the_agreed_limits(self) -> None:
        assert (MAX_PHONE_LENGTH, MAX_ADDRESS_LINE_LENGTH, MAX_NOTE_LENGTH) == (
            32,
            128,
            5000,
        )

    @pytest.mark.parametrize(("field", "limit", "char"), _MEMBER_BOUNDS)
    def test_member_save_request(self, field: str, limit: int, char: str) -> None:
        ok = MemberSaveRequest(**_valid_member_data(**{field: char * limit}))
        assert len(getattr(ok, field)) == limit
        with pytest.raises(ValidationError):
            MemberSaveRequest(**_valid_member_data(**{field: char * (limit + 1)}))

    @pytest.mark.parametrize(("field", "limit", "char"), _SELF_SERVICE_BOUNDS)
    def test_member_self_service_save_request(
        self, field: str, limit: int, char: str
    ) -> None:
        ok = MemberSelfServiceSaveRequest(
            **_valid_self_service_data(**{field: char * limit})
        )
        assert len(getattr(ok, field)) == limit
        with pytest.raises(ValidationError):
            MemberSelfServiceSaveRequest(
                **_valid_self_service_data(**{field: char * (limit + 1)})
            )

    @pytest.mark.parametrize(("field", "limit", "char"), _CONTACT_BOUNDS)
    def test_contact_save_request(self, field: str, limit: int, char: str) -> None:
        ok = ContactSaveRequest(**_valid_contact_data(**{field: char * limit}))
        assert len(getattr(ok, field)) == limit
        with pytest.raises(ValidationError):
            ContactSaveRequest(**_valid_contact_data(**{field: char * (limit + 1)}))


# ---------------------------------------------------------------------------
# Blank strings and surrounding whitespace
# ---------------------------------------------------------------------------


class TestBlankAndTrimmedValues:
    @pytest.mark.parametrize("blank", ["", " ", "   ", "\t", " \n "])
    def test_member_without_any_name_is_rejected(self, blank: str) -> None:
        with pytest.raises(ValidationError, match="Nachname oder Couleurname"):
            MemberSaveRequest(**_valid_member_data(nachname=blank, couleurname=blank))
        with pytest.raises(ValidationError, match="Nachname oder Couleurname"):
            MemberSaveRequest(**_valid_member_data(nachname=blank))

    def test_blank_surname_is_fine_with_a_student_name(self) -> None:
        member = MemberSaveRequest(
            **_valid_member_data(nachname="   ", couleurname="  Rex ")
        )
        assert member.couleurname == "Rex"

    @pytest.mark.parametrize("blank", ["", " ", "  \t "])
    def test_contact_name_must_not_be_blank(self, blank: str) -> None:
        with pytest.raises(ValidationError):
            ContactSaveRequest(**_valid_contact_data(name=blank))

    def test_member_values_are_trimmed(self) -> None:
        member = MemberSaveRequest(
            **_valid_member_data(nachname="  Muster\n", vorname=" Max ")
        )
        assert (member.nachname, member.vorname) == ("Muster", "Max")

    def test_self_service_values_are_trimmed(self) -> None:
        member = MemberSelfServiceSaveRequest(
            **_valid_self_service_data(vorname="  Max ", url=" https://a.at ")
        )
        assert (member.vorname, member.url) == ("Max", "https://a.at")

    def test_contact_values_are_trimmed(self) -> None:
        contact = ContactSaveRequest(**_valid_contact_data(name="  Kontakt "))
        assert contact.name == "Kontakt"

    def test_trimming_applies_before_the_length_check(self) -> None:
        member = MemberSaveRequest(**_valid_member_data(vortitel=" " + "A" * 32 + " "))
        assert member.vortitel == "A" * 32


class TestSelfServiceFieldsAreJsonNative:
    """Proposed changes are stored as JSONB without conversion, so no
    self-service field may be a date or datetime."""

    def test_no_field_holds_a_date(self):
        dumped = MemberSelfServiceSaveRequest(**_valid_self_service_data()).model_dump(
            mode="python"
        )

        assert all(not isinstance(value, date | datetime) for value in dumped.values())
