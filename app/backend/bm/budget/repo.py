"""SQL горячего пути денег (ADR-002, ADR-003). Без бизнес-логики: только чтение и запись строк.

Порядок блокировок един для всех транзакций, которые меняют резервы и реестр (`decide`, `settle`,
`expire_reserves`): сначала строки `budget_lock` в порядке (owner_type, owner_id), затем `call_record`.
Поэтому параллельные транзакции не взаимоблокируются.
"""

import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from bm.budget.snapshot import CallerKey
from bm.domain.types import Kop, PeriodKind

type Owner = tuple[str, UUID]  # (owner_type, owner_id)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_PARTITIONS_LOCK = 0x626D_7061  # «bmpa» — advisory-блокировка создания партиций


def uuid7(at: datetime) -> UUID:
    """UUID версии 7 (RFC 9562): 48 бит миллисекунд Unix-времени `at`, затем 74 случайных бита."""
    ms = (at - _EPOCH) // timedelta(milliseconds=1)
    rand = secrets.randbits(74)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76 | (rand >> 62) << 64  # версия 7 и rand_a (12 бит)
    value |= 0b10 << 62 | (rand & ((1 << 62) - 1))  # вариант RFC и rand_b (62 бита)
    return UUID(int=value)


def _unzip(owners: Iterable[Owner]) -> tuple[list[str], list[UUID]]:
    types, ids = [], []
    for t, i in owners:
        types.append(t)
        ids.append(i)
    return types, ids


async def ensure_partitions(conn: asyncpg.Connection, day: date) -> None:
    """Партиции `call_record` на месяц `day` и следующий. Параллельные вызовы сериализуются."""
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _PARTITIONS_LOCK)
        await conn.execute("SELECT bm_ensure_call_record_partitions($1)", day)


async def lock_owners(conn: asyncpg.Connection, owners: Sequence[Owner]) -> dict[Owner, Kop]:
    """Блокирует строки `budget_lock` владельцев в порядке (owner_type, owner_id); возвращает резервы."""
    types, ids = _unzip(owners)
    rows = await conn.fetch(
        "SELECT owner_type, owner_id, reserved_kop FROM budget_lock "
        "WHERE (owner_type, owner_id) IN (SELECT * FROM unnest($1::text[], $2::uuid[])) "
        "ORDER BY owner_type, owner_id FOR UPDATE",
        types,
        ids,
    )
    return {(r["owner_type"], r["owner_id"]): r["reserved_kop"] for r in rows}


async def reserved_of(conn: asyncpg.Connection, owners: Sequence[Owner]) -> dict[Owner, Kop]:
    """Открытые резервы владельцев без блокировки (для остатков)."""
    types, ids = _unzip(owners)
    rows = await conn.fetch(
        "SELECT owner_type, owner_id, reserved_kop FROM budget_lock "
        "WHERE (owner_type, owner_id) IN (SELECT * FROM unnest($1::text[], $2::uuid[]))",
        types,
        ids,
    )
    return {(r["owner_type"], r["owner_id"]): r["reserved_kop"] for r in rows}


@dataclass(frozen=True, slots=True)
class Spent:
    token: Kop = 0
    import_: Kop = 0


async def spent(
    conn: asyncpg.Connection, owners: Sequence[Owner], windows: Mapping[PeriodKind, tuple[date, date]]
) -> dict[Owner, dict[PeriodKind, Spent]]:
    """Один запрос `SUM … FILTER` по `ledger_daily`: расход владельцев за окна периодов (включительно)."""
    types, ids = _unzip(owners)
    kinds = list(windows)
    cols, args = [], [types, ids]
    for k in kinds:
        lo, hi = windows[k]
        args += [lo, hi]
        a, b = len(args) - 1, len(args)
        for source in ("token", "import"):
            cols.append(
                f"coalesce(sum(amount_kop) FILTER "
                f"(WHERE source = '{source}' AND day BETWEEN ${a} AND ${b}), 0)"
                f" AS {k.value}_{source}"
            )
    lo_all = min(w[0] for w in windows.values())
    hi_all = max(w[1] for w in windows.values())
    args += [lo_all, hi_all]
    n = len(args)
    sql = (
        f"SELECT owner_type, owner_id, {', '.join(cols)} FROM ledger_daily "  # noqa: S608 — имена колонок из перечисления
        "WHERE (owner_type, owner_id) IN (SELECT * FROM unnest($1::text[], $2::uuid[])) "
        f"AND day BETWEEN ${n - 1} AND ${n} GROUP BY owner_type, owner_id"
    )
    out: dict[Owner, dict[PeriodKind, Spent]] = {}
    for r in await conn.fetch(sql, *args):
        out[(r["owner_type"], r["owner_id"])] = {
            k: Spent(token=int(r[f"{k.value}_token"]), import_=int(r[f"{k.value}_import"])) for k in kinds
        }
    return out


async def add_reserved(conn: asyncpg.Connection, owners: Sequence[Owner], delta: Kop) -> None:
    if delta == 0 or not owners:
        return
    types, ids = _unzip(owners)
    await conn.execute(
        "UPDATE budget_lock SET reserved_kop = reserved_kop + $3 "
        "WHERE (owner_type, owner_id) IN (SELECT * FROM unnest($1::text[], $2::uuid[]))",
        types,
        ids,
        delta,
    )


async def add_ledger(
    conn: asyncpg.Connection, owners: Sequence[Owner], day: date, amount: Kop, source: str = "token"
) -> None:
    """Добавляет сумму (может быть отрицательной — дельта позднего отчёта) в суточный реестр владельцев."""
    if amount == 0 or not owners:
        return
    types, ids = _unzip(owners)
    await conn.execute(
        "INSERT INTO ledger_daily(owner_type, owner_id, day, source, amount_kop) "
        "SELECT t, i, $3, $4, $5 FROM unnest($1::text[], $2::uuid[]) AS o(t, i) "
        "ON CONFLICT (owner_type, owner_id, day, source) "
        "DO UPDATE SET amount_kop = ledger_daily.amount_kop + EXCLUDED.amount_kop",
        types,
        ids,
        day,
        source,
        amount,
    )


_CALL_COLUMNS = (
    "id",
    "caller_id",
    "report_id",
    "employee_id",
    "department_id",
    "requested_model",
    "final_model",
    "outcome",
    "code",
    "price_id",
    "fx",
    "est_in",
    "est_out",
    "est_source",
    "reserve_kop",
    "fact_kop",
    "tokens_in",
    "tokens_out",
    "tokens_cached",
    "call_status",
    "flags",
    "status",
    "expense_date",
    "created_at",
    "settled_at",
    "expires_at",
)
_INSERT_CALL = (
    f"INSERT INTO call_record({', '.join(_CALL_COLUMNS)}) "  # noqa: S608 — список колонок — константа
    f"VALUES ({', '.join(f'${i}' for i in range(1, len(_CALL_COLUMNS) + 1))})"
)


async def insert_call_record(conn: asyncpg.Connection, row: Mapping[str, Any]) -> None:
    unknown = set(row) - set(_CALL_COLUMNS)
    if unknown:  # ошибка программиста
        raise ValueError(f"лишние колонки call_record: {sorted(unknown)}")
    defaults: dict[str, Any] = {"flags": [], "reserve_kop": 0}
    values = [row.get(c, defaults.get(c)) for c in _CALL_COLUMNS]
    await conn.execute(_INSERT_CALL, *values)


async def get_call_record(
    conn: asyncpg.Connection, record_id: UUID, *, for_update: bool = False
) -> asyncpg.Record | None:
    lock = " FOR UPDATE" if for_update else ""
    row: asyncpg.Record | None = await conn.fetchrow(
        "SELECT * FROM call_record WHERE id = $1" + lock,  # noqa: S608 — суффикс — константа
        record_id,
    )
    return row


async def price_by_id(conn: asyncpg.Connection, price_id: UUID) -> asyncpg.Record | None:
    row: asyncpg.Record | None = await conn.fetchrow(
        "SELECT id, in_per_1k, out_per_1k, cached_per_1k, currency FROM model_price WHERE id = $1", price_id
    )
    return row


async def price_at(conn: asyncpg.Connection, model_id: str, at: datetime) -> asyncpg.Record | None:
    """Цена модели, действовавшая в момент `at` (история цен неизменяема)."""
    row: asyncpg.Record | None = await conn.fetchrow(
        "SELECT id, in_per_1k, out_per_1k, cached_per_1k, currency FROM model_price "
        "WHERE model_id = $1 AND valid_from <= $2 ORDER BY valid_from DESC LIMIT 1",
        model_id,
        at,
    )
    return row


async def fx_at(conn: asyncpg.Connection, month: date) -> Decimal | None:
    """Курс USD месяца `month`; нет курса месяца — последний известный до него."""
    rate: Decimal | None = await conn.fetchval(
        "SELECT usd_rub FROM fx_rate WHERE month <= $1 ORDER BY month DESC LIMIT 1", month
    )
    return rate


async def reported(conn: asyncpg.Connection, caller_id: UUID, report_id: UUID) -> UUID | None:
    """Запись расхода, уже учтённая по (ключ вызывающей системы, report_id), или None."""
    rec: UUID | None = await conn.fetchval(
        "SELECT call_record_id FROM report_key WHERE caller_id = $1 AND report_id = $2", caller_id, report_id
    )
    return rec


async def claim_report(
    conn: asyncpg.Connection, caller_id: UUID, report_id: UUID, record_id: UUID, now: datetime
) -> bool:
    """Занимает ключ идемпотентности (FR-009). False — такой (ключ, report_id) уже учтён."""
    got: UUID | None = await conn.fetchval(
        "INSERT INTO report_key(caller_id, report_id, call_record_id, created_at) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (caller_id, report_id) DO NOTHING RETURNING call_record_id",
        caller_id,
        report_id,
        record_id,
        now,
    )
    return got is not None


async def settle_call_record(
    conn: asyncpg.Connection,
    record_id: UUID,
    created_at: datetime,
    *,
    fact: Kop,
    tokens: tuple[int, int, int],
    call_status: str,
    flags: list[str],
    now: datetime,
    final_model: str | None,
) -> asyncpg.Record:
    """Факт по решению: сумма, токены, статус `settled`.

    `final_model` — фактическая модель, если вызов сделан не той моделью, что в решении."""
    row: asyncpg.Record = await conn.fetchrow(
        "UPDATE call_record SET fact_kop = $3, tokens_in = $4, tokens_out = $5, tokens_cached = $6, "
        "call_status = $7, flags = $8, status = 'settled', settled_at = $9, "
        "final_model = coalesce($10, final_model) "
        "WHERE id = $1 AND created_at = $2 RETURNING *",
        record_id,
        created_at,
        fact,
        *tokens,
        call_status,
        flags,
        now,
        final_model,
    )
    return row


async def due_reserves(conn: asyncpg.Connection, now: datetime, *, limit: int) -> list[asyncpg.Record]:
    rows: list[asyncpg.Record] = await conn.fetch(
        "SELECT id, employee_id, department_id FROM call_record "
        "WHERE status = 'open' AND expires_at <= $1 ORDER BY expires_at LIMIT $2",
        now,
        limit,
    )
    return rows


async def expire_call_record(
    conn: asyncpg.Connection, record_id: UUID, created_at: datetime, *, fact: Kop
) -> None:
    await conn.execute(
        "UPDATE call_record SET status = 'expired', fact_kop = $3, "
        "flags = CASE WHEN 'no_report' = ANY(flags) THEN flags ELSE array_append(flags, 'no_report') END "
        "WHERE id = $1 AND created_at = $2",
        record_id,
        created_at,
        fact,
    )


async def open_reserves(conn: asyncpg.Connection) -> int:
    n: int = await conn.fetchval("SELECT count(*) FROM call_record WHERE status = 'open'")
    return n


async def caller_key(conn: asyncpg.Connection, digest: bytes) -> CallerKey | None:
    r = await conn.fetchrow(
        "SELECT id, name, scope_type, scope_department_id, provider_source_id, status "
        "FROM caller_system WHERE key_sha256 = $1",
        digest,
    )
    if r is None:
        return None
    return CallerKey(
        id=r["id"],
        name=r["name"],
        scope_type=r["scope_type"],
        scope_department_id=r["scope_department_id"],
        provider_source_id=r["provider_source_id"],
        active=r["status"] == "active",
    )
