"""Каталог моделей, история цен и курсы USD/₽ (FR-015, FR-019).

- История цен неизменяема: новая цена — новая строка с `valid_from` не в прошлом;
  списанное не пересчитывается.
- USD-цену нельзя сохранить, пока не введён ни один курс (`422 fx_rate_missing`, 12_arch_decision.md §6 п. 1).
- `current_price` — последняя цена с `valid_from <= now`; если все цены в будущем — ближайшая из них.
"""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from bm.config.errors import Conflict, Invalid, NotFound
from bm.config.service import Applied, Change, apply
from bm.domain.periods import MSK

_MODEL_COLS = (
    "id, provider, kind, supports_tools, supports_json, max_context, "
    "default_in, default_out, tie_order, active"
)
_SORT = {"id": "id", "provider": "provider", "tie_order": "tie_order", "created_at": "created_at"}
# Поле API → столбец БД для ModelPatch.
_PATCH_COLUMNS = {
    "supports_tools": "supports_tools",
    "supports_json": "supports_json",
    "max_context": "max_context",
    "default_input_tokens": "default_in",
    "default_output_tokens": "default_out",
    "tie_order": "tie_order",
    "active": "active",
}


def price_str(d: Decimal | None) -> str | None:
    """numeric(18,6) → `PriceDecimal` без лишних нулей: 10.000000 → "10", 0.750000 → "0.75"."""
    if d is None:
        return None
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _price_out(r: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "model_id": r["model_id"],
        "valid_from": r["valid_from"].astimezone(MSK).isoformat(),
        "input_per_1k": price_str(r["in_per_1k"]),
        "output_per_1k": price_str(r["out_per_1k"]),
        "cached_input_per_1k": price_str(r["cached_per_1k"]),
        "currency": r["currency"],
    }


def _model_out(r: asyncpg.Record, price: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "provider": r["provider"],
        "kind": r["kind"],
        "supports_tools": r["supports_tools"],
        "supports_json": r["supports_json"],
        "max_context": r["max_context"],
        "default_input_tokens": r["default_in"],
        "default_output_tokens": r["default_out"],
        "tie_order": r["tie_order"],
        "active": r["active"],
        "current_price": price,
    }


async def _current_prices(
    conn: asyncpg.Connection, model_ids: list[str], now: datetime
) -> dict[str, dict[str, Any]]:
    rows = await conn.fetch(
        "SELECT DISTINCT ON (model_id) "
        "id, model_id, valid_from, in_per_1k, out_per_1k, cached_per_1k, currency "
        "FROM model_price WHERE model_id = ANY($1::text[]) "
        "ORDER BY model_id, (valid_from <= $2) DESC, "
        "CASE WHEN valid_from <= $2 THEN valid_from END DESC, valid_from ASC",
        model_ids,
        now,
    )
    return {r["model_id"]: _price_out(r) for r in rows}


async def list_models(
    conn: asyncpg.Connection, *, now: datetime, page: int, page_size: int, sort: str | None, order: str
) -> tuple[list[dict[str, Any]], int]:
    if sort is not None and sort not in _SORT:
        raise Invalid(f"Сортировка возможна по полям: {', '.join(_SORT)}.")
    total: int = await conn.fetchval("SELECT count(*) FROM model")
    direction = "DESC" if order == "desc" else "ASC"
    rows = await conn.fetch(
        f"SELECT {_MODEL_COLS} FROM model ORDER BY {_SORT[sort or 'id']} {direction}, id "  # noqa: S608
        "LIMIT $1 OFFSET $2",
        page_size,
        (page - 1) * page_size,
    )
    prices = await _current_prices(conn, [r["id"] for r in rows], now)
    return [_model_out(r, prices[r["id"]]) for r in rows], total


async def get_model(
    conn: asyncpg.Connection, model_id: str, now: datetime, *, lock: bool = False
) -> dict[str, Any]:
    row = await conn.fetchrow(
        f"SELECT {_MODEL_COLS} FROM model WHERE id = $1" + (" FOR UPDATE" if lock else ""),  # noqa: S608
        model_id,
    )
    if row is None:
        raise NotFound(f"Модели «{model_id}» нет в каталоге.")
    return _model_out(row, (await _current_prices(conn, [model_id], now))[model_id])


async def _check_price(conn: asyncpg.Connection, price: Mapping[str, Any], now: datetime) -> datetime:
    valid_from: datetime = price.get("valid_from") or now
    if valid_from < now:
        raise Invalid("Дата начала действия цены не может быть в прошлом: списанное не пересчитывается.")
    if price["currency"] == "USD" and not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM fx_rate)"):
        raise Invalid(
            "Для цены в USD нужен курс USD/₽: введите курс месяца в разделе «Курсы».", code="fx_rate_missing"
        )
    return valid_from


async def _insert_price(
    conn: asyncpg.Connection, model_id: str, price: Mapping[str, Any], valid_from: datetime
) -> asyncpg.Record:
    try:
        row: asyncpg.Record = await conn.fetchrow(
            "INSERT INTO model_price(model_id, valid_from, in_per_1k, out_per_1k, cached_per_1k, currency) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "RETURNING id, model_id, valid_from, in_per_1k, out_per_1k, cached_per_1k, currency",
            model_id,
            valid_from,
            Decimal(price["input_per_1k"]),
            Decimal(price["output_per_1k"]),
            None if price.get("cached_input_per_1k") is None else Decimal(price["cached_input_per_1k"]),
            price["currency"],
        )
    except asyncpg.UniqueViolationError as exc:
        raise Invalid("Цена с такой датой начала действия уже есть.") from exc
    return row


async def create_model(
    conn: asyncpg.Connection, actor: str, body: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    async def run(c: asyncpg.Connection) -> Applied:
        valid_from = await _check_price(c, body["price"], now)
        inserted = await c.fetchval(
            "INSERT INTO model(id, provider, kind, supports_tools, supports_json, max_context, default_in, "
            "default_out, tie_order) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
            "ON CONFLICT (id) DO NOTHING RETURNING id",
            body["id"],
            body["provider"],
            body["kind"],
            body["supports_tools"],
            body["supports_json"],
            body["max_context"],
            body["default_input_tokens"],
            body["default_output_tokens"],
            body.get("tie_order", 100),
        )
        if inserted is None:
            raise Conflict(f"Модель «{body['id']}» уже есть в каталоге.")
        await _insert_price(c, body["id"], body["price"], valid_from)
        after = await get_model(c, body["id"], now)
        return Applied(object_id=body["id"], after=after, result=after)

    applied = await apply(conn, Change("create", "model", run), actor, at=now)
    result: dict[str, Any] = applied.result
    return result


async def update_model(
    conn: asyncpg.Connection, actor: str, model_id: str, patch: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    async def run(c: asyncpg.Connection) -> Applied:
        before = await get_model(c, model_id, now, lock=True)
        fields = [k for k in _PATCH_COLUMNS if k in patch]
        if fields:
            sets = ", ".join(f"{_PATCH_COLUMNS[k]} = ${i + 2}" for i, k in enumerate(fields))
            await c.execute(
                f"UPDATE model SET {sets} WHERE id = $1",  # noqa: S608 — столбцы из белого списка
                model_id,
                *(patch[k] for k in fields),
            )
        after = await get_model(c, model_id, now)
        return Applied(object_id=model_id, before=before, after=after, result=after)

    applied = await apply(conn, Change("update", "model", run), actor, at=now)
    result: dict[str, Any] = applied.result
    return result


async def list_prices(conn: asyncpg.Connection, model_id: str) -> list[dict[str, Any]]:
    """История цен по убыванию `valid_from`.

    У неизвестной модели — пустой список: 404 у listModelPrices в контракте не объявлен.
    """
    rows = await conn.fetch(
        "SELECT id, model_id, valid_from, in_per_1k, out_per_1k, cached_per_1k, currency FROM model_price "
        "WHERE model_id = $1 ORDER BY valid_from DESC",
        model_id,
    )
    return [_price_out(r) for r in rows]


async def create_price(
    conn: asyncpg.Connection, actor: str, model_id: str, price: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    async def run(c: asyncpg.Connection) -> Applied:
        if await c.fetchval("SELECT id FROM model WHERE id = $1 FOR UPDATE", model_id) is None:
            # 404 у createModelPrice в контракте не объявлен — неизвестная модель как ошибка значения.
            raise Invalid(f"Модели «{model_id}» нет в каталоге.", code="not_found")
        valid_from = await _check_price(c, price, now)
        row = await _insert_price(c, model_id, price, valid_from)
        after = _price_out(row)
        return Applied(object_id=model_id, after=after, result=after)

    applied = await apply(conn, Change("create", "model_price", run), actor, at=now)
    result: dict[str, Any] = applied.result
    return result


# ---------------------------------------------------------------- курсы USD/₽


def _fx_out(r: asyncpg.Record) -> dict[str, Any]:
    return {
        "month": r["month"].strftime("%Y-%m"),
        "usd_rub": format(r["usd_rub"], "f"),
        "entered_by": r["entered_by"],
        "entered_at": r["entered_at"].astimezone(MSK).isoformat(),
    }


async def list_fx(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch("SELECT month, usd_rub, entered_by, entered_at FROM fx_rate ORDER BY month DESC")
    return [_fx_out(r) for r in rows]


def parse_month(month: str) -> date:
    year, mon = month.split("-")
    return date(int(year), int(mon), 1)


async def put_fx(
    conn: asyncpg.Connection, actor: str, month: str, usd_rub: str, now: datetime
) -> dict[str, Any]:
    """Курс месяца: применяется к ещё не обработанным данным; прошлые суммы не пересчитываются."""
    rate = Decimal(usd_rub)
    if rate <= 0:
        raise Invalid("Курс должен быть больше нуля.")
    first = parse_month(month)

    async def run(c: asyncpg.Connection) -> Applied:
        old = await c.fetchrow(
            "SELECT month, usd_rub, entered_by, entered_at FROM fx_rate WHERE month = $1 FOR UPDATE", first
        )
        row = await c.fetchrow(
            "INSERT INTO fx_rate(month, usd_rub, entered_by, entered_at) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (month) DO UPDATE SET usd_rub = EXCLUDED.usd_rub, entered_by = EXCLUDED.entered_by, "
            "entered_at = EXCLUDED.entered_at RETURNING month, usd_rub, entered_by, entered_at",
            first,
            rate,
            actor,
            now,
        )
        after = _fx_out(row)
        return Applied(
            object_id=month, before=None if old is None else _fx_out(old), after=after, result=after
        )

    applied = await apply(conn, Change("put", "fx_rate", run), actor, at=now)
    result: dict[str, Any] = applied.result
    return result
