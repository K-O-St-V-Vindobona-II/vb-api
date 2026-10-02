import json
import pickle
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from arq.jobs import deserialize_job_raw
from arq.jobs import serialize_job as arq_serialize_job

from app.core.job_serialization import deserialize_job, serialize_job


class TestJobSerialization:
    def test_round_trips_an_arq_job_payload(self) -> None:
        raw = arq_serialize_job(
            "task_send_reset_email",
            ("anna@example.org", "token"),
            {"change_type": "created"},
            1,
            1_700_000_000_000,
            serializer=serialize_job,
        )

        function, args, kwargs, tries, enqueue_time = deserialize_job_raw(
            raw, deserializer=deserialize_job
        )

        assert function == "task_send_reset_email"
        assert tuple(args) == ("anna@example.org", "token")
        assert kwargs == {"change_type": "created"}
        assert (tries, enqueue_time) == (1, 1_700_000_000_000)

    def test_writes_plain_json(self) -> None:
        raw = serialize_job({"a": ["x"]})

        assert json.loads(raw) == {"a": ["x"]}

    def test_dates_decimals_and_uuids_become_strings(self) -> None:
        raw = serialize_job(
            {
                "day": date(2001, 5, 4),
                "moment": datetime(2001, 5, 4, 12, 30, tzinfo=UTC),
                "amount": Decimal("12.50"),
                "id": UUID("0190a5e2-7c1e-7a3b-8f00-000000000001"),
            }
        )

        assert json.loads(raw) == {
            "day": "2001-05-04",
            "moment": "2001-05-04T12:30:00+00:00",
            "amount": "12.50",
            "id": "0190a5e2-7c1e-7a3b-8f00-000000000001",
        }

    def test_rejects_types_it_cannot_represent(self) -> None:
        with pytest.raises(TypeError, match="object"):
            serialize_job({"x": object()})

    def test_a_pickled_payload_is_refused_and_never_executed(self) -> None:
        executed: list[str] = []

        class Payload:
            def __reduce__(self) -> tuple[object, tuple[str]]:
                return (executed.append, ("ran",))

        with pytest.raises(ValueError, match=r"\S"):
            deserialize_job(pickle.dumps({"a": Payload()}))

        assert executed == []
