"""The web container's ARQ pool: created on first use, shared afterwards, and
wired with the project's own job (de)serialisation so that the web container
and the worker agree on the payload format."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from arq.connections import RedisSettings

from app.core import arq_pool
from app.core.config import get_settings
from app.core.job_serialization import deserialize_job, serialize_job


@pytest.fixture(autouse=True)
def _no_pool_yet(monkeypatch) -> None:
    monkeypatch.setattr(arq_pool, "_arq_pool", None)


class TestGetArqPool:
    def test_the_pool_connects_to_valkey_with_the_project_serialisation(self) -> None:
        pool = object()

        with patch.object(
            arq_pool, "create_pool", new=AsyncMock(return_value=pool)
        ) as create_pool:
            result = asyncio.run(arq_pool.get_arq_pool())

        assert result is pool
        (redis_settings,) = create_pool.await_args.args
        expected = RedisSettings.from_dsn(get_settings().valkey_url)
        assert (redis_settings.host, redis_settings.port, redis_settings.database) == (
            expected.host,
            expected.port,
            expected.database,
        )
        assert create_pool.await_args.kwargs == {
            "job_serializer": serialize_job,
            "job_deserializer": deserialize_job,
        }

    def test_the_pool_is_created_once_and_shared(self) -> None:
        async def get_twice() -> tuple[object, object]:
            return await arq_pool.get_arq_pool(), await arq_pool.get_arq_pool()

        with patch.object(
            arq_pool, "create_pool", new=AsyncMock(return_value=object())
        ) as create_pool:
            first, second = asyncio.run(get_twice())

        assert first is second
        create_pool.assert_awaited_once()
