"""Раннер миграций: файлы `migrations/NNNN_*.sql` по порядку, учёт в `schema_migrations` (ADR-003: без ORM).

Каждая миграция — одна транзакция. Параллельный запуск (api и worker) сериализуется advisory-блокировкой.
Запуск вручную: `python -m bm.db.migrate` (строка подключения — DATABASE_URL).
"""

import asyncio
import os
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_LOCK_KEY = 0x626D_6D69  # «bmmi» — ключ advisory-блокировки миграций


def available() -> list[tuple[str, Path]]:
    """(версия, путь) всех миграций по порядку; версия — имя файла без `.sql`."""
    return [(p.stem, p) for p in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))]


async def migrate(conn: asyncpg.Connection) -> list[str]:
    """Применяет недостающие миграции; возвращает список применённых версий."""
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    applied: list[str] = []
    await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
    try:
        done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        for version, path in available():
            if version in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations(version) VALUES ($1)", version)
            applied.append(version)
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)
    return applied


async def current_version(conn: asyncpg.Connection) -> str | None:
    """Последняя применённая миграция или None, если схема не создана."""
    exists = await conn.fetchval("SELECT to_regclass('schema_migrations') IS NOT NULL")
    if not exists:
        return None
    return await conn.fetchval("SELECT max(version) FROM schema_migrations")


async def _main() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        applied = await migrate(conn)
        print("применены миграции:", ", ".join(applied) if applied else "нет новых")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(_main())
