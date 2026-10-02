"""JSON serialisation for jobs on the ARQ queue.

arq defaults to pickle for job arguments and results. Unpickling executes code
chosen by whoever wrote the bytes, so anyone who can write to Valkey could run
code in the worker. JSON cannot carry code. The web pool and the worker must
use the same pair, so both import it from here.
"""

import json
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID


def _encode_unsupported(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    msg = f"Job payload of type {type(value).__name__} is not JSON serialisable"
    raise TypeError(msg)


def serialize_job(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, default=_encode_unsupported).encode("utf-8")


def deserialize_job(raw: bytes) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(raw))
