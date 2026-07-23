from pydantic import BaseModel, ConfigDict


class StrictInputModel(BaseModel):
    """Base class for request/input models.

    Rejects unknown fields and disables Pydantic's lax type coercion
    (e.g. a numeric string silently becoming an int/float) — every field
    must arrive as the type it's declared with.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
