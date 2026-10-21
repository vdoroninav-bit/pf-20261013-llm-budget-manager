"""Сотрудники под псевдонимами (FR-035): список, карточка, личные лимиты, раскрытие псевдонима.

Логин и имя наружу отдаёт только `reveal` (администратор), и каждое раскрытие пишется в аудит
(`reveal_pseudonym`). Подразделение сотрудника — по открытому назначению (`valid_to IS NULL`).
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from bm.access import audit
from bm.config import limits
from bm.config.departments import put_limits
from bm.config.errors import NotFound

_SELECT = (
    "SELECT e.id, e.pseudonym, e.active, a.department_id FROM employee e "
    "LEFT JOIN employee_assignment a ON a.employee_id = e.id AND a.valid_to IS NULL"
)


def _out(r: asyncpg.Record, lim: limits.LimitSet) -> dict[str, Any]:
    dep = r["department_id"]
    return {
        "pseudonym": r["pseudonym"],
        "department_id": None if dep is None else str(dep),
        "active": r["active"],
        "limits": lim,
    }


async def list_employees(
    conn: asyncpg.Connection, *, department_id: UUID | None, page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    cond, args = ("a.department_id = $1", [department_id]) if department_id is not None else ("true", [])
    total: int = await conn.fetchval(
        f"SELECT count(*) FROM ({_SELECT} WHERE {cond}) t",  # noqa: S608 — константы
        *args,
    )
    n = len(args)
    rows = await conn.fetch(
        f"{_SELECT} WHERE {cond} ORDER BY e.pseudonym LIMIT ${n + 1} OFFSET ${n + 2}",
        *args,
        page_size,
        (page - 1) * page_size,
    )
    lims = await limits.read_many(conn, "employee", [r["id"] for r in rows])
    return [_out(r, lims[r["id"]]) for r in rows], total


async def _row(conn: asyncpg.Connection, pseudonym: str, *, lock: bool = False) -> asyncpg.Record:
    row = await conn.fetchrow(
        f"{_SELECT} WHERE e.pseudonym = $1" + (" FOR UPDATE OF e" if lock else ""),
        pseudonym,
    )
    if row is None:
        raise NotFound("Сотрудник с таким псевдонимом не найден.")
    return row


async def department_of(conn: asyncpg.Connection, pseudonym: str) -> UUID | None:
    """Текущее подразделение сотрудника (для проверки прав владельца бюджета); NotFound — нет сотрудника."""
    dep: UUID | None = (await _row(conn, pseudonym))["department_id"]
    return dep


async def get_employee(conn: asyncpg.Connection, pseudonym: str) -> dict[str, Any]:
    row = await _row(conn, pseudonym)
    return _out(row, await limits.read(conn, "employee", row["id"]))


async def put_employee_limits(
    conn: asyncpg.Connection, actor: str, pseudonym: str, values: Mapping[str, int | None], now: datetime
) -> limits.LimitSet:
    async with conn.transaction():
        row = await _row(conn, pseudonym, lock=True)
        return await put_limits(conn, actor, "employee", row["id"], values, now)


async def reveal(conn: asyncpg.Connection, actor: str, pseudonym: str, now: datetime) -> dict[str, Any]:
    """Сопоставление псевдонима с учётной записью AD; запись в аудит в той же транзакции."""
    async with conn.transaction():
        row = await conn.fetchrow(
            "SELECT id, pseudonym, ad_login, display_name FROM employee WHERE pseudonym = $1", pseudonym
        )
        if row is None:
            raise NotFound("Сотрудник с таким псевдонимом не найден.")
        await audit.write(
            conn,
            actor=actor,
            action="reveal_pseudonym",
            object_type="employee",
            object_id=pseudonym,
            at=now,
        )
    return {"pseudonym": row["pseudonym"], "login": row["ad_login"], "display_name": row["display_name"]}
