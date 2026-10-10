"""Pure unit tests for shared Pydantic primitives in app/schemas/base.py."""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, Field, ValidationError

from app.schemas import auth as auth_schemas
from app.schemas.base import (
    StrictInputModel,
    TrimmedInputModel,
    UtcDatetime,
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
# UtcDatetime — end-to-end through Pydantic validation
# ---------------------------------------------------------------------------


class _Model(BaseModel):
    at: UtcDatetime | None


class TestUtcDatetimeField:
    def test_naive_input_becomes_tz_aware(self) -> None:
        naive = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC).replace(tzinfo=None)
        result = _Model(at=naive)
        assert result.at is not None
        assert result.at.tzinfo is not None

    def test_aware_input_round_trips(self) -> None:
        aware = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        result = _Model(at=aware)
        assert result.at == aware

    def test_none_stays_none(self) -> None:
        assert _Model(at=None).at is None

    def test_serializes_with_utc_offset(self) -> None:
        # Pydantic's JSON encoder renders a UTC-aware datetime with the "Z"
        # shorthand rather than "+00:00" - both are valid ISO8601.
        aware = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        dumped = _Model(at=aware).model_dump(mode="json")
        assert dumped["at"] is not None
        assert dumped["at"].endswith(("Z", "+00:00"))


# ---------------------------------------------------------------------------
# TrimmedInputModel - strict input that also trims surrounding whitespace
# ---------------------------------------------------------------------------


class _Trimmed(TrimmedInputModel):
    name: str = Field(min_length=1, max_length=5)
    nick: str | None = None


class TestTrimmedInputModel:
    def test_surrounding_whitespace_is_removed(self) -> None:
        model = _Trimmed(name="  Hi\n", nick=" \tx ")
        assert (model.name, model.nick) == ("Hi", "x")

    def test_blank_value_fails_the_minimum_length(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            _Trimmed(name="   ")

    def test_length_limit_applies_to_the_trimmed_value(self) -> None:
        assert _Trimmed(name="  12345  ").name == "12345"
        with pytest.raises(ValidationError, match="at most 5 characters"):
            _Trimmed(name="123456")

    def test_inner_whitespace_is_kept(self) -> None:
        assert _Trimmed(name="a  b").name == "a  b"

    def test_stays_strict_about_types_and_unknown_keys(self) -> None:
        with pytest.raises(ValidationError):
            _Trimmed(name=5)
        with pytest.raises(ValidationError, match="extra"):
            _Trimmed(name="ok", other="x")

    def test_is_a_strict_input_model(self) -> None:
        assert issubclass(TrimmedInputModel, StrictInputModel)
        assert TrimmedInputModel.model_config["str_strip_whitespace"] is True
        assert StrictInputModel.model_config.get("str_strip_whitespace") is None

    def test_credential_requests_are_never_trimmed(self) -> None:
        credential_models = [
            cls
            for cls in vars(auth_schemas).values()
            if isinstance(cls, type)
            and issubclass(cls, StrictInputModel)
            and cls.__module__ == auth_schemas.__name__
        ]
        assert credential_models
        assert not [
            m.__name__ for m in credential_models if issubclass(m, TrimmedInputModel)
        ]
