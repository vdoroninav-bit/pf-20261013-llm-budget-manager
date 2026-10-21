"""Первый старт на пустой БД: параллельный `migrate()` (CI 92b3414, первый `make smoke` после `make up`).

Два воркера api (`python -m bm.serve`, UVICORN_WORKERS=2) и worker стартуют одновременно и каждый вызывает
`migrate()`. Раньше `CREATE TABLE IF NOT EXISTS schema_migrations` шёл до advisory-блокировки, и на пустой схеме
PostgreSQL отвечал одному из процессов `UniqueViolationError … pg_type_typname_nsp_index` (гонка
`IF NOT EXISTS` в каталоге). Воркер падал в lifespan, uvicorn останавливал весь api. Весь DDL раннера должен
идти под блокировкой.
"""

import asyncio

import asyncpg
import pytest

from bm.db import migrate as migrate_mod
from tests import conftest

pytestmark = pytest.mark.db

PARALLEL = 4
ROUNDS = 15


async def _empty_schema(conn: asyncpg.Connection) -> None:
    await conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")


async def test_migrate_runs_no_ddl_before_advisory_lock(db: asyncpg.Connection) -> None:
    # Детерминированно: блокировку миграций держит «другой процесс» — migrate() не должен трогать схему.
    await _empty_schema(db)
    await db.execute("SELECT pg_advisory_lock($1)", migrate_mod._LOCK_KEY)
    other = await asyncpg.connect(conftest.DATABASE_URL)
    try:
        task = asyncio.create_task(migrate_mod.migrate(other))
        await asyncio.sleep(0.3)
        assert not task.done()
        assert await db.fetchval("SELECT to_regclass('schema_migrations')") is None, (
            "migrate() создаёт schema_migrations до advisory-блокировки — гонка первого старта"
        )
        await db.execute("SELECT pg_advisory_unlock($1)", migrate_mod._LOCK_KEY)
        applied = await asyncio.wait_for(task, timeout=30)
        assert applied and applied[0] == "0001_init"
    finally:
        await other.close()


async def test_parallel_first_migrate_on_empty_schema(db: asyncpg.Connection) -> None:
    conns = [await asyncpg.connect(conftest.DATABASE_URL) for _ in range(PARALLEL)]
    expected = [v for v, _ in migrate_mod.available()]
    try:
        for _ in range(ROUNDS):
            await _empty_schema(db)
            results = await asyncio.gather(*(migrate_mod.migrate(c) for c in conns), return_exceptions=True)
            errors = [r for r in results if isinstance(r, BaseException)]
            assert not errors, f"параллельный первый migrate() упал: {errors[0]!r}"
            applied = [r for r in results if not isinstance(r, BaseException)]
            # миграции применил ровно один процесс, остальные увидели готовую схему
            assert sorted(map(len, applied)) == [0] * (PARALLEL - 1) + [len(expected)]
            versions = await db.fetchval("SELECT array_agg(version ORDER BY version) FROM schema_migrations")
            assert versions == expected
    finally:
        for c in conns:
            await c.close()
