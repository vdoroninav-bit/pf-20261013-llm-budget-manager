"""Справочник источников провайдеров (FR-022, FR-023, T061): «источник → подразделение + способ учёта».

- у источника (provider, ref) ровно один способ учёта: повтор — `409 source_exists`;
- Anthropic: только `kind=workspace` (`cost_report` не разбивает стоимость по ключам, ADR-004) —
  иначе `422 unsupported_source_kind`;
- смена `mode` или подразделения и удаление действуют со следующей загрузки окна: статусы строк
  пересчитываются конвейером, в реестр уходит дельта;
- удаление снимает ссылку вызывающих систем на источник (сверка SC-005 по нему больше не строится).

Изменения — через `config.apply` (аудит «было/стало», `config_version`).
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import asyncpg

from bm.config.errors import Conflict, Invalid, NotFound
from bm.config.service import Applied, Change, apply

_COLS = "id, provider, kind, ref, display_name, department_id, mode"


def _out(r: asyncpg.Record) -> dict[str, Any]:
    dep = r["department_id"]
    return {
        "id": str(r["id"]),
        "provider": r["provider"],
        "kind": r["kind"],
        "ref": r["ref"],
        "display_name": r["display_name"],
        "department_id": None if dep is None else str(dep),
        "mode": r["mode"],
    }


async def list_sources(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(f"SELECT {_COLS} FROM provider_source ORDER BY provider, ref")  # noqa: S608
    return [_out(r) for r in rows]


async def _get(conn: asyncpg.Connection, source_id: UUID, *, lock: bool = False) -> asyncpg.Record:
    row = await conn.fetchrow(
        f"SELECT {_COLS} FROM provider_source WHERE id = $1" + (" FOR UPDATE" if lock else ""),  # noqa: S608
        source_id,
    )
    if row is None:
        raise NotFound("Источник не найден.")
    return row


async def _department(conn: asyncpg.Connection, value: str) -> UUID:
    try:
        dep = UUID(value)
    except ValueError as exc:
        raise Invalid("department_id — UUID подразделения.") from exc
    if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM department WHERE id = $1)", dep):
        raise Invalid("Подразделение не найдено.")
    return dep


def _check_kind(provider: str, kind: str) -> None:
    if provider == "anthropic" and kind != "workspace":
        raise Invalid(
            "Для Anthropic источник — только workspace: отчёт о расходе не разбивает стоимость по ключам.",
            code="unsupported_source_kind",
        )


async def create(conn: asyncpg.Connection, actor: str, body: Mapping[str, Any]) -> dict[str, Any]:
    _check_kind(body["provider"], body["kind"])

    async def run(c: asyncpg.Connection) -> Applied:
        dep = await _department(c, body["department_id"])
        row = await c.fetchrow(
            "INSERT INTO provider_source(provider, kind, ref, display_name, department_id, mode) "  # noqa: S608
            "VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (provider, ref) DO NOTHING "
            f"RETURNING {_COLS}",
            body["provider"],
            body["kind"],
            body["ref"],
            body.get("display_name"),
            dep,
            body["mode"],
        )
        if row is None:
            raise Conflict(
                "Источник с таким идентификатором уже есть: у источника ровно один способ учёта.",
                code="source_exists",
            )
        after = _out(row)
        return Applied(object_id=after["id"], after=after, result=after)

    applied = await apply(conn, Change("create", "provider_source", run), actor)
    result: dict[str, Any] = applied.result
    return result


async def update(
    conn: asyncpg.Connection, actor: str, source_id: UUID, patch: Mapping[str, Any]
) -> dict[str, Any]:
    async def run(c: asyncpg.Connection) -> Applied:
        old = await _get(c, source_id, lock=True)
        before = _out(old)
        display_name = patch["display_name"] if "display_name" in patch else old["display_name"]
        dep = old["department_id"]
        if patch.get("department_id") is not None:
            dep = await _department(c, patch["department_id"])
        mode = patch.get("mode") or old["mode"]
        row = await c.fetchrow(
            "UPDATE provider_source SET display_name = $2, department_id = $3, mode = $4 "  # noqa: S608
            "WHERE id = $1 "
            f"RETURNING {_COLS}",
            source_id,
            display_name,
            dep,
            mode,
        )
        after = _out(row)
        return Applied(object_id=str(source_id), before=before, after=after, result=after)

    applied = await apply(conn, Change("update", "provider_source", run), actor)
    result: dict[str, Any] = applied.result
    return result


async def delete(conn: asyncpg.Connection, actor: str, source_id: UUID) -> None:
    async def run(c: asyncpg.Connection) -> Applied:
        before = _out(await _get(c, source_id, lock=True))
        await c.execute(
            "UPDATE caller_system SET provider_source_id = NULL WHERE provider_source_id = $1", source_id
        )
        await c.execute("DELETE FROM provider_source WHERE id = $1", source_id)
        return Applied(object_id=str(source_id), before=before)

    await apply(conn, Change("delete", "provider_source", run), actor)
