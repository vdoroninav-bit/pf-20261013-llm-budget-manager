"""Пул соединений asyncpg для api и worker."""

import asyncpg


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size, command_timeout=30)
    if pool is None:  # pragma: no cover — asyncpg возвращает None только без await
        raise RuntimeError("не удалось создать пул соединений")
    return pool
