"""Pure unit tests for Pydantic validators in app/schemas/standesdb.py."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.standesdb import (
    ContactSaveRequest,
    ImageUpdateRequest,
    MemberSaveRequest,
    _ensure_utc,
)

# ---------------------------------------------------------------------------
# _ensure_utc helper
# ---------------------------------------------------------------------------


class TestEnsureUtc:
    def test_naive_datetime_gets_utc(self) -> None:
        naive = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)
        result = _ensure_utc(naive)
        assert result is not None
        assert result.tzinfo is UTC

    def test_aware_datetime_unchanged(self) -> None:
        aware = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        result = _ensure_utc(aware)
        assert result is aware

    def test_none_stays_none(self) -> None:
        assert _ensure_utc(None) is None


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
