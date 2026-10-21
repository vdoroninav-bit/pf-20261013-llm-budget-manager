"""Учётные записи админки (FR-030): список, создание, смена роли, отключение, сброс пароля.

- Новый пользователь и сброс пароля → `must_change_password=true`; при сбросе и отключении сессии закрываются.
- `budget_owner` обязательно привязан к подразделению; у других ролей подразделения нет.
- Нельзя оставить систему без активного администратора (`LastAdmin`, 13_design_rulings.md В6).
  Изменения пользователей сериализуются advisory-блокировкой: две параллельные операции
  не отключат двух последних admin.
- В аудит не попадают пароли и хэши: только состояние `AdminUser` и признак `password_reset`.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from bm.access.auth import hash_password
from bm.access.permissions import Role
from bm.config.errors import Conflict, Invalid, LastAdmin, NotFound
from bm.config.service import Applied, Change, apply

_COLS = "id, login, role, department_id, active, must_change_password"
_USERS_LOCK = 0x626D_7573  # «bmus»


def _out(r: asyncpg.Record) -> dict[str, Any]:
    dep = r["department_id"]
    return {
        "id": str(r["id"]),
        "login": r["login"],
        "role": r["role"],
        "department_id": None if dep is None else str(dep),
        "active": r["active"],
        "must_change_password": r["must_change_password"],
    }


async def list_users(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    return [_out(r) for r in await conn.fetch(f"SELECT {_COLS} FROM admin_user ORDER BY login")]  # noqa: S608


async def _check_department(conn: asyncpg.Connection, role: str, department_id: UUID | None) -> None:
    if role == Role.BUDGET_OWNER:
        if department_id is None:
            raise Invalid("Для владельца бюджета укажите подразделение.")
        if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM department WHERE id = $1)", department_id):
            raise Invalid("Подразделение не найдено.")
    elif department_id is not None:
        raise Invalid("Подразделение указывается только для владельца бюджета.")


async def create_user(
    conn: asyncpg.Connection,
    actor: str,
    *,
    login: str,
    password: str,
    role: str,
    department_id: UUID | None,
    now: datetime,
) -> dict[str, Any]:
    password_hash = hash_password(password)  # argon2 — вне транзакции

    async def run(c: asyncpg.Connection) -> Applied:
        await _check_department(c, role, department_id)
        row = await c.fetchrow(
            "INSERT INTO admin_user(login, argon2_hash, role, department_id, must_change_password) "  # noqa: S608 — константы
            f"VALUES ($1, $2, $3, $4, true) ON CONFLICT (login) DO NOTHING RETURNING {_COLS}",
            login,
            password_hash,
            role,
            department_id,
        )
        if row is None:
            raise Conflict(f"Пользователь «{login}» уже есть.")
        after = _out(row)
        return Applied(object_id=after["id"], after=after, result=after)

    applied = await apply(conn, Change("create", "admin_user", run), actor, at=now)
    result: dict[str, Any] = applied.result
    return result


async def update_user(
    conn: asyncpg.Connection, actor: str, user_id: UUID, patch: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    """`patch` — только переданные поля `AdminUserPatch` (role, department_id, active, reset_password)."""
    new_hash = hash_password(patch["reset_password"]) if patch.get("reset_password") else None

    async def run(c: asyncpg.Connection) -> Applied:
        await c.execute("SELECT pg_advisory_xact_lock($1)", _USERS_LOCK)
        row = await c.fetchrow(f"SELECT {_COLS} FROM admin_user WHERE id = $1 FOR UPDATE", user_id)  # noqa: S608
        if row is None:
            raise NotFound("Пользователь админки не найден.")
        before = _out(row)
        role = patch.get("role", row["role"])
        if "department_id" in patch:
            department_id = patch["department_id"]
        else:  # при смене роли с budget_owner подразделение снимается
            department_id = row["department_id"] if role == Role.BUDGET_OWNER else None
        active = patch.get("active", row["active"])
        await _check_department(c, role, department_id)

        was_admin = row["role"] == Role.ADMIN and row["active"]
        if was_admin and not (role == Role.ADMIN and active):
            others = await c.fetchval(
                "SELECT count(*) FROM admin_user WHERE role = 'admin' AND active AND id <> $1", user_id
            )
            if others == 0:
                raise LastAdmin

        row = await c.fetchrow(
            "UPDATE admin_user SET role = $2, department_id = $3, active = $4, "  # noqa: S608 — константы
            "argon2_hash = coalesce($5, argon2_hash), "
            "must_change_password = must_change_password OR $5 IS NOT NULL, "
            "failed_attempts = CASE WHEN $5 IS NULL THEN failed_attempts ELSE 0 END, "
            "locked_until = CASE WHEN $5 IS NULL THEN locked_until END "
            f"WHERE id = $1 RETURNING {_COLS}",
            user_id,
            role,
            department_id,
            active,
            new_hash,
        )
        if new_hash is not None or not active:
            await c.execute("DELETE FROM session WHERE user_id = $1", user_id)
        after: dict[str, Any] = _out(row)
        audit_after = {**after, "password_reset": True} if new_hash is not None else after
        return Applied(object_id=str(user_id), before=before, after=audit_after, result=after)

    applied = await apply(conn, Change("update", "admin_user", run), actor, at=now)
    result: dict[str, Any] = applied.result
    return result
