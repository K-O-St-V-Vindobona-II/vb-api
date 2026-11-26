"""A date and its accuracy arrive in agreement: an unknown date keeps no
value, a missing value is unknown, and the accuracy stays within 0 to 3."""

import json
import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.standesdb import (
    BadgeEntry,
    ContactSaveRequest,
    KeyEntry,
    MemberSaveRequest,
)

_FUZZY = (
    "geburtsdatum",
    "aufnahmedatum",
    "branderdatum",
    "burschungsdatum",
    "philistrierungsdatum",
    "entlassungsdatum",
    "sterbedatum",
)


def _member(**fields: object) -> MemberSaveRequest:
    payload: dict[str, object] = {
        "vorname": "Max",
        "nachname": "Muster",
        "org_id": "vbw",
        "zustellungen": "deaktiviert",
    }
    payload.update(fields)
    return MemberSaveRequest.model_validate_json(_json(payload))


def _json(payload: dict[str, object]) -> str:
    return json.dumps(payload, default=str)


class TestMemberDates:
    @pytest.mark.parametrize("field", _FUZZY)
    def test_an_unknown_date_keeps_no_value(self, field):
        request = _member(**{field: "2021-01-01", f"{field}_accuracy": 0})

        assert getattr(request, field) is None
        assert getattr(request, f"{field}_accuracy") == 0

    @pytest.mark.parametrize("field", _FUZZY)
    def test_a_missing_value_is_unknown(self, field):
        request = _member(**{field: None, f"{field}_accuracy": 3})

        assert getattr(request, f"{field}_accuracy") == 0

    def test_a_known_date_stays_as_entered(self):
        request = _member(geburtsdatum="1990-05-03", geburtsdatum_accuracy=2)

        assert request.geburtsdatum == date(1990, 5, 3)
        assert request.geburtsdatum_accuracy == 2


class TestContactDate:
    def test_an_unknown_date_keeps_no_value(self):
        request = ContactSaveRequest.model_validate_json(
            _json(
                {
                    "kontakttyp": "person",
                    "name": "Firma",
                    "datum": "2020-01-01",
                    "datum_accuracy": 0,
                }
            )
        )

        assert (request.datum, request.datum_accuracy) == (None, 0)


class TestBadgeAndKeyEntries:
    @pytest.mark.parametrize("model", [BadgeEntry, KeyEntry])
    def test_an_unknown_date_keeps_no_value(self, model):
        entry = model(
            id=uuid.uuid4(),
            presentationdate=date(2020, 1, 1),
            presentationdate_accuracy=0,
        )

        assert (entry.presentationdate, entry.presentationdate_accuracy) == (None, 0)

    @pytest.mark.parametrize("model", [BadgeEntry, KeyEntry])
    def test_a_missing_value_is_unknown(self, model):
        entry = model(
            id=uuid.uuid4(), presentationdate=None, presentationdate_accuracy=2
        )

        assert entry.presentationdate_accuracy == 0

    @pytest.mark.parametrize("model", [BadgeEntry, KeyEntry])
    @pytest.mark.parametrize("accuracy", [-1, 4])
    def test_the_accuracy_stays_within_zero_to_three(self, model, accuracy):
        with pytest.raises(ValidationError):
            model(
                id=uuid.uuid4(),
                presentationdate=date(2020, 1, 1),
                presentationdate_accuracy=accuracy,
            )

    def test_the_frontend_may_still_send_name_and_group(self):
        """The form sends the display fields of an entry along; they must not
        make the save fail."""
        entry = BadgeEntry.model_validate(
            {
                "id": str(uuid.uuid4()),
                "name": "Fuxenband",
                "group": "jubelband",
                "presentationdate": "2020-05-17",
                "presentationdate_accuracy": 3,
            }
        )

        assert entry.presentationdate == date(2020, 5, 17)
