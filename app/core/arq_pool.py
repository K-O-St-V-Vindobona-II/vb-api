"""Lazy singleton for the web container's ARQ Valkey connection pool.

Mirrors app/core/storage.py's _get_storage_singleton() exactly: created on
first actual use (not at app startup), so the test suite's module-scoped
TestClient lifespan never needs a live Valkey connection unless a test
explicitly exercises an enqueue path (those tests override get_arq_pool
via app.dependency_overrides, same pattern as get_storage).
"""

from typing import cast

from arq.connections import ArqRedis, RedisSettings, create_pool

from app.core.config import get_settings

_arq_pool: ArqRedis | None = None


async def _get_arq_pool_singleton() -> ArqRedis:
    global _arq_pool  # noqa: PLW0603 -- lazy singleton, avoids reconnecting per call
    if _arq_pool is None:
        settings = get_settings()
        _arq_pool = await create_pool(
            RedisSettings.from_dsn(cast("str", settings.valkey_url))
        )
    return _arq_pool


async def get_arq_pool() -> ArqRedis:
    return await _get_arq_pool_singleton()
