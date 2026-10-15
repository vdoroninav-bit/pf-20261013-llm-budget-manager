"""Подразделения (FR-013): список, карточка, создание, режим политики, разрешённые модели, лимиты.

Изменения — только через `config.apply` (аудит «было/стало» и `config_version`). Состояние подразделения
в аудите и в ответе API — один и тот же словарь (схема `Department` контракта).
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import asyncpg

from bm.config import limits
from bm.config.errors import Conflict, Invalid, NotFound
from bm.config.service import Applied, Change, apply

_SORT = {"name": "d.name", "created_at": "d.created_at"}
_PATCHABLE = ("name", "policy_mode", "fail_mode", "subs_warn_pct")

_SELECT = (
    "SELECT d.id, d.name, d.ad_group, d.policy_mode, d.fail_mode, d.subs_warn_pct, "
    "coalesce((SELECT array_agg(m.model_id ORDER BY m.model_id) FROM dept_model_allow m "
    "          WHERE m.department_id = d.id), '{}') AS allowed_models, "
    "(SELECT count(*) FROM employee_assignment a JOIN employee e ON e.id = a.employee_id "
    " WHERE a.department_id = d.id AND a.valid_to IS NULL AND e.active) AS employees_count "
    "FROM department d"
)


def _out(r: asyncpg.Record, lim: limits.LimitSet) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "ad_group": r["ad_group"],
        "policy_mode": r["policy_mode"],
        "fail_mode": r["fail_mode"],
        "allowed_models": list(r["allowed_models"]),
        "subs_warn_pct": r["subs_warn_pct"],
        "limits": lim,
        "employees_count": r["employees_count"],
    }


async def list_departments(
    conn: asyncpg.Connection,
    *,
    only: UUID | None,
    q: str | None,
    page: int,
    page_size: int,
    sort: str | None,
    order: str,
) -> tuple[list[dict[str, Any]], int]:
    """`only` — фильтр владельца бюджета (его подразделение); None — все."""
    if sort is not None and sort not in _SORT:
        raise Invalid(f"Сортировка возможна по полям: {', '.join(_SORT)}.")
    where, args = ["true"], []
    if only is not None:
        args.append(only)
        where.append(f"d.id = ${len(args)}")
    if q:
        args.append(f"%{q}%")
        where.append(f"d.name ILIKE ${len(args)}")
    cond = " AND ".join(where)
    total: int = await conn.fetchval(f"SELECT count(*) FROM department d WHERE {cond}", *args)  # noqa: S608
    direction = "DESC" if order == "desc" else "ASC"
    args += [page_size, (page - 1) * page_size]
    rows = await conn.fetch(
        f"{_SELECT} WHERE {cond} ORDER BY {_SORT[sort or 'name']} {direction}, d.id "
        f"LIMIT ${len(args) - 1} OFFSET ${len(args)}",
        *args,
    )
    lims = await limits.read_many(conn, "department", [r["id"] for r in rows])
    return [_out(r, lims[r["id"]]) for r in rows], total


async def get_department(conn: asyncpg.Connection, department_id: UUID) -> dict[str, Any]:
    row = await conn.fetchrow(f"{_SELECT} WHERE d.id = $1", department_id)
    if row is None:
        raise NotFound("Подразделение не найдено.")
    return _out(row, await limits.read(conn, "department", department_id))


async def create_department(
    conn: asyncpg.Connection, actor: str, name: str, ad_group: str | None
) -> dict[str, Any]:
    """Новое подразделение — в режиме «наблюдать» (policy_mode=observe, значение по умолчанию схемы БД)."""

    async def run(c: asyncpg.Connection) -> Applied:
        try:
            dep_id: UUID = await c.fetchval(
                "INSERT INTO department(name, ad_group) VALUES ($1, $2) RETURNING id", name, ad_group
            )
        except asyncpg.UniqueViolationError as exc:
            raise Conflict(f"Подразделение с группой AD «{ad_group}» уже есть.") from exc
        after = await get_department(c, dep_id)
        return Applied(object_id=str(dep_id), after=after, result=after)

    applied = await apply(conn, Change("create", "department", run), actor)
    result: dict[str, Any] = applied.result
    return result


async def update_department(
    conn: asyncpg.Connection, actor: str, department_id: UUID, patch: Mapping[str, Any]
) -> dict[str, Any]:
    """`patch` — только переданные поля `DepartmentPatch`; `allowed_models` заменяет список целиком."""

    async def run(c: asyncpg.Connection) -> Applied:
        await _lock(c, department_id)
        before = await get_department(c, department_id)
        fields = [k for k in _PATCHABLE if k in patch]
        if fields:
            sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
            await c.execute(
                f"UPDATE department SET {sets} WHERE id = $1",  # noqa: S608 — имена полей из белого списка
                department_id,
                *(patch[k] for k in fields),
            )
        if "allowed_models" in patch:
            models = list(dict.fromkeys(patch["allowed_models"]))
            known = {
                r["id"] for r in await c.fetch("SELECT id FROM model WHERE id = ANY($1::text[])", models)
            }
            unknown = [m for m in models if m not in known]
            if unknown:
                raise Invalid(f"Нет в каталоге моделей: {', '.join(unknown[:10])}.")
            await c.execute("DELETE FROM dept_model_allow WHERE department_id = $1", department_id)
            await c.executemany(
                "INSERT INTO dept_model_allow(department_id, model_id) VALUES ($1, $2)",
                [(department_id, m) for m in models],
            )
        after = await get_department(c, department_id)
        return Applied(object_id=str(department_id), before=before, after=after, result=after)

    applied = await apply(conn, Change("update", "department", run), actor)
    result: dict[str, Any] = applied.result
    return result


async def put_limits(
    conn: asyncpg.Connection, actor: str, owner_type: str, owner_id: UUID, values: Mapping[str, int | None]
) -> limits.LimitSet:
    """Лимиты подразделения или сотрудника; существование владельца проверяет вызывающий (`_lock`)."""

    async def run(c: asyncpg.Connection) -> Applied:
        before = await limits.read(c, owner_type, owner_id)
        await limits.write(c, owner_type, owner_id, values)
        after = await limits.read(c, owner_type, owner_id)
        return Applied(
            object_id=str(owner_id), before={"limits": before}, after={"limits": after}, result=after
        )

    applied = await apply(conn, Change("update_limits", owner_type, run), actor)
    result: limits.LimitSet = applied.result
    return result


async def put_department_limits(
    conn: asyncpg.Connection, actor: str, department_id: UUID, values: Mapping[str, int | None]
) -> limits.LimitSet:
    async with conn.transaction():
        await _lock(conn, department_id)
        return await put_limits(conn, actor, "department", department_id, values)


async def _lock(conn: asyncpg.Connection, department_id: UUID) -> None:
    if await conn.fetchval("SELECT id FROM department WHERE id = $1 FOR UPDATE", department_id) is None:
        raise NotFound("Подразделение не найдено.")
