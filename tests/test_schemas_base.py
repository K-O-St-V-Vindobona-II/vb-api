"""Pure unit tests for shared Pydantic primitives in app/schemas/base.py."""

from datetime import UTC, datetime

from pydantic import BaseModel

from app.schemas.base import UtcDatetime, _ensure_utc

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
