"""Журнал аудита (FR-031): только INSERT, таблица `audit_log` защищена от UPDATE/DELETE (T014).

`before` и `after` — JSON без секретов: пароли, хэши и ключи сюда не передаются.
"""

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import asyncpg

SYSTEM_ACTOR = "system"


def _dump(value: Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


async def write(
    conn: asyncpg.Connection,
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: str | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    at: datetime,
) -> int:
    """Добавляет запись; возвращает её id.

    `at` — время часов приложения (`Clock`), обязательно (14_tech_rulings.md, пакет 3, 3Б7): `listAudit`
    фильтрует по датам МСК, и у приложения и БД должен быть один источник времени. `DEFAULT now()` у колонки
    остаётся только страховкой для ручных вставок.
    """
    audit_id: int = await conn.fetchval(
        "INSERT INTO audit_log(at, actor, action, object_type, object_id, before, after) "
        "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb) RETURNING id",
        at,
        actor[:200],
        action,
        object_type,
        object_id,
        _dump(before),
        _dump(after),
    )
    return audit_id
