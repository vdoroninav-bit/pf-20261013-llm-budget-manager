"""Изменение настроек: `apply(conn, change, actor, at=now)` — запись, аудит и `config_version += 1`
в одной транзакции.

Все изменения справочников и политик идут только через `apply` (T042): так снимок конфигурации в api
узнаёт об изменении одним запросом к `config_version`, а журнал аудита полон (FR-031).
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from bm.access import audit


@dataclass(frozen=True, slots=True)
class Applied:
    """Итог изменения: что записать в аудит и что вернуть вызывающему (`result`)."""

    object_id: str | None
    before: Mapping[str, Any] | None = None
    after: Mapping[str, Any] | None = None
    result: Any = None


@dataclass(frozen=True, slots=True)
class Change:
    """Изменение настроек. `run` выполняет запись в БД внутри транзакции `apply` и описывает её."""

    action: str  # create | update | delete | revoke | …
    object_type: str  # department | model | caller_system | …
    run: Callable[[asyncpg.Connection], Awaitable[Applied]]


async def apply(conn: asyncpg.Connection, change: Change, actor: str, *, at: datetime) -> Applied:
    """Выполняет изменение атомарно. Исключение из `run` откатывает и запись, и аудит, и версию.

    `at` — время записи аудита по часам приложения (`Clock`), обязательно (пакет 3, 3Б7).
    """
    async with conn.transaction():
        applied = await change.run(conn)
        await audit.write(
            conn,
            actor=actor,
            action=change.action,
            object_type=change.object_type,
            object_id=applied.object_id,
            before=applied.before,
            after=applied.after,
            at=at,
        )
        await conn.execute("UPDATE config_version SET version = version + 1")
    return applied
