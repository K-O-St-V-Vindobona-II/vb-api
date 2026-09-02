import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict


def _ensure_utc(v: datetime | None) -> datetime | None:
    """Treats naive datetimes as UTC.

    Defensive: every timestamp column in this codebase is already
    TIMESTAMPTZ, so this should never trigger from a DB-backed value -
    it guards against a future non-DB source (e.g. a hand-built dict)
    slipping a naive datetime past validation.
    """
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=UTC)
    return v


UtcDatetime = Annotated[datetime, BeforeValidator(_ensure_utc)]


class StrictInputModel(BaseModel):
    """Base class for request/input models.

    Rejects unknown fields and disables Pydantic's lax type coercion
    (e.g. a numeric string silently becoming an int/float) — every field
    must arrive as the type it's declared with.
    """

    model_config = ConfigDict(extra="forbid", strict=True)


class MoveRequest(StrictInputModel):
    """Shared request body for every sort_order-reorderable admin list
    (gallery images, programm hints, quotes, social links) — see
    app.services.reorder_service.find_reorder_neighbor().
    """

    direction: Literal["up", "down"]


class StatusResponse(BaseModel):
    """Generic acknowledgement for write endpoints that have nothing more
    specific to return. `status` is a plain str, not a fixed Literal,
    since its value differs by endpoint ("ok", "submitted", "resolved",
    ...) - see each endpoint's own docstring for its actual values."""

    status: str


class IdLabelOption(BaseModel):
    """Shared {id, label} shape for dropdown/reference-data options (orgs,
    states, export modules, ...) across multiple schemas."""

    id: str
    label: str


class StatusIdResponse(BaseModel):
    """Acknowledgement for create endpoints that only need to report the
    new row's id alongside the status."""

    status: str
    id: uuid.UUID


class PaginatedResponse[T](BaseModel):
    """Generic items/total/page/page_size envelope, shared by every
    paginated list endpoint that doesn't need extra fields beyond that.

    Not used by PaginatedTransactions in app/schemas/p4x.py, which
    predates this and uses `per_page` instead of `page_size` on the
    wire - kept as its own class rather than retrofitted onto this one,
    to avoid a breaking field-rename for existing p4x.py consumers.
    """

    items: list[T]
    total: int
    page: int
    page_size: int
