"""Вызывающие системы и их ключи (FR-016).

Ключ `bm_<prefix>_<secret>` показывается один раз в ответе `createCaller`; в БД — только SHA-256,
в аудите — ни ключа, ни хэша. Отзыв увеличивает `config_version`, поэтому снимок api перечитывается
на следующем же запросе и ключ получает 401 (T018).
"""

import hashlib
import secrets
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from bm.config.errors import Invalid, NotFound
from bm.config.service import Applied, Change, apply
from bm.domain.periods import MSK

_COLS = (
    "id, name, key_prefix, scope_type, scope_department_id, provider_source_id, status, "
    "created_at, revoked_at"
)
_NEEDS_DEPARTMENT = frozenset({"service", "department_employees"})


def new_key() -> tuple[str, str, bytes]:
    """(ключ, префикс, SHA-256 ключа)."""
    prefix = secrets.token_hex(4)
    key = f"bm_{prefix}_{secrets.token_urlsafe(24)}"
    return key, prefix, hashlib.sha256(key.encode()).digest()


def _iso(v: datetime | None) -> str | None:
    return None if v is None else v.astimezone(MSK).isoformat()


def _out(r: asyncpg.Record) -> dict[str, Any]:
    dep = r["scope_department_id"]
    src = r["provider_source_id"]
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "key_prefix": r["key_prefix"],
        "subject_scope": {"type": r["scope_type"], "department_id": None if dep is None else str(dep)},
        "provider_source_id": None if src is None else str(src),
        "status": r["status"],
        "created_at": _iso(r["created_at"]),
        "revoked_at": _iso(r["revoked_at"]),
    }


async def list_callers(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(f"SELECT {_COLS} FROM caller_system ORDER BY created_at, id")  # noqa: S608
    return [_out(r) for r in rows]


async def _get(conn: asyncpg.Connection, caller_id: UUID) -> asyncpg.Record:
    row = await conn.fetchrow(f"SELECT {_COLS} FROM caller_system WHERE id = $1 FOR UPDATE", caller_id)  # noqa: S608
    if row is None:
        raise NotFound("Вызывающая система не найдена.")
    return row


async def _check_source(conn: asyncpg.Connection, source_id: UUID | None) -> None:
    if source_id is not None and not await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM provider_source WHERE id = $1)", source_id
    ):
        raise Invalid("Источник провайдера не найден.")


async def create_caller(
    conn: asyncpg.Connection,
    actor: str,
    *,
    name: str,
    scope_type: str,
    scope_department_id: UUID | None,
    provider_source_id: UUID | None,
    now: datetime,
) -> dict[str, Any]:
    """Возвращает `CallerCreated` (с `api_key`). Для any_employee подразделение не задаётся."""
    if scope_type in _NEEDS_DEPARTMENT:
        if scope_department_id is None:
            raise Invalid("Для области service и department_employees укажите подразделение.")
    elif scope_department_id is not None:
        raise Invalid("Для области any_employee подразделение не указывается.")
    key, prefix, digest = new_key()

    async def run(c: asyncpg.Connection) -> Applied:
        if scope_department_id is not None and not await c.fetchval(
            "SELECT EXISTS (SELECT 1 FROM department WHERE id = $1)", scope_department_id
        ):
            raise Invalid("Подразделение области ключа не найдено.")
        await _check_source(c, provider_source_id)
        row = await c.fetchrow(
            "INSERT INTO caller_system(name, key_prefix, key_sha256, scope_type, scope_department_id, "  # noqa: S608 — константы
            "provider_source_id, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7) "
            f"RETURNING {_COLS}",
            name,
            prefix,
            digest,
            scope_type,
            scope_department_id,
            provider_source_id,
            now,
        )
        after = _out(row)
        return Applied(object_id=after["id"], after=after, result=after)

    applied = await apply(conn, Change("create", "caller_system", run), actor, at=now)
    return {**applied.result, "api_key": key}


async def update_caller(
    conn: asyncpg.Connection, actor: str, caller_id: UUID, patch: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    async def run(c: asyncpg.Connection) -> Applied:
        before = _out(await _get(c, caller_id))
        if "provider_source_id" in patch:
            await _check_source(c, patch["provider_source_id"])
            await c.execute(
                "UPDATE caller_system SET provider_source_id = $2 WHERE id = $1",
                caller_id,
                patch["provider_source_id"],
            )
        if "name" in patch:
            await c.execute("UPDATE caller_system SET name = $2 WHERE id = $1", caller_id, patch["name"])
        after = _out(await _get(c, caller_id))
        return Applied(object_id=str(caller_id), before=before, after=after, result=after)

    applied = await apply(conn, Change("update", "caller_system", run), actor, at=now)
    result: dict[str, Any] = applied.result
    return result


async def revoke_caller(
    conn: asyncpg.Connection, actor: str, caller_id: UUID, now: datetime
) -> dict[str, Any]:
    """Отзыв действует немедленно. Повторный отзыв — без изменений и без записи в аудит."""
    async with conn.transaction():
        current = await _get(conn, caller_id)
        if current["status"] == "revoked":
            return _out(current)

        async def run(c: asyncpg.Connection) -> Applied:
            row = await c.fetchrow(
                "UPDATE caller_system SET status = 'revoked', revoked_at = $2 WHERE id = $1 "  # noqa: S608 — константы
                f"RETURNING {_COLS}",
                caller_id,
                now,
            )
            after = _out(row)
            return Applied(object_id=str(caller_id), before=_out(current), after=after, result=after)

        applied = await apply(conn, Change("revoke", "caller_system", run), actor, at=now)
    result: dict[str, Any] = applied.result
    return result
