"""T030: учёт факта в БД (FR-008…FR-011, ADR-003, 12_plan.md §9 п. 3).

- расход ложится на дату решения, а не на дату отчёта (переход суток между decide и settle);
- цена — действующая на момент решения, даже если после решения вступила новая;
- факт пишется в ledger_daily сотрудника и подразделения в одной транзакции с закрытием резерва;
- идемпотентность через report_key (caller_id, report_id).

Написано до реализации (T031): xfail(strict=True) на уровне модуля.
"""

from datetime import date, timedelta
from uuid import UUID

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from tests.fixtures.qa import (
    balance,
    bump_config,
    call_record,
    catalog,
    decide,
    decision_body,
    limit_of,
    msk,
    report,
    reserved,
    usage_body,
)
from tests.fixtures.seed import Emp, Seed

pytestmark = [
    pytest.mark.db,
    pytest.mark.xfail(
        strict=True,
        raises=(AssertionError, ImportError),
        reason="T031: settle и баланс ещё не реализованы",
    ),
]


async def _world(seed: Seed) -> tuple[UUID, Emp, UUID, str]:
    await catalog(seed, only="AC")
    dep = await seed.department(
        "Маркетинг", limits={"day": 1_000_000, "month": 10_000_000}, allowed=("A", "C")
    )
    emp = await seed.employee("ivanov", department=dep, limits={"day": 50_000})
    caller_id, key = await seed.caller_key()
    return dep, emp, caller_id, key


async def _ledger(db: asyncpg.Connection) -> list[tuple[str, date, str, int]]:
    rows = await db.fetch(
        "SELECT owner_type, day, source, amount_kop FROM ledger_daily ORDER BY owner_type, day, source"
    )
    return [(r["owner_type"], r["day"], r["source"], r["amount_kop"]) for r in rows]


async def test_expense_date_is_decision_date(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    clock.set(msk(2026, 10, 14, 23, 59, 59))
    _, _, _, key = await _world(seed)
    d = await decide(client, key, decision_body(model="A"))
    clock.set(msk(2026, 10, 15, 0, 0, 5))
    r = await report(client, key, usage_body(d["decision_id"], 1000, 400))
    assert r["expense_date"] == "2026-10-14"
    assert await _ledger(db) == [
        ("department", date(2026, 10, 14), "token", 3000),
        ("employee", date(2026, 10, 14), "token", 3000),
    ]
    assert (await call_record(db, d["decision_id"]))["expense_date"] == date(2026, 10, 14)
    # новый день: дневной личный лимит 15.10 свободен
    day = limit_of(await balance(client, key, "ivanov"), "employee", "day")
    assert (day["spent"]["token_rub"], day["remaining_rub"]) == ("0.00", "500.00")
    assert day["period_start"].startswith("2026-10-15T00:00:00")


async def test_expense_date_across_month_boundary(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    clock.set(msk(2026, 10, 31, 23, 59, 50))
    _, _, _, key = await _world(seed)
    d = await decide(client, key, decision_body(model="A"))
    clock.set(msk(2026, 11, 1, 0, 0, 10))
    r = await report(client, key, usage_body(d["decision_id"], 1000, 400))
    assert r["expense_date"] == "2026-10-31"
    month = limit_of(await balance(client, key, "ivanov"), "department", "month")
    assert month["spent"]["token_rub"] == "0.00"  # ноябрь чист, расход остался в октябре


async def test_price_at_decision_time(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    _, _, _, key = await _world(seed)
    d = await decide(client, key, decision_body(model="A"))
    await seed.price("A", "20", "100", valid_from=clock.now() + timedelta(minutes=1))
    await bump_config(db)
    clock.advance(minutes=2)
    r = await report(client, key, usage_body(d["decision_id"], 1000, 400))
    assert r["amount_rub"] == "30.00"  # старая цена, действовавшая при решении
    d2 = await decide(client, key, decision_body(model="A"))
    assert d2["reserve_rub"] == "60.00"  # новые решения — по новой цене


async def test_settle_writes_both_ledgers_and_releases_both_locks(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, emp, caller_id, key = await _world(seed)
    d = await decide(client, key, decision_body(model="A"))
    assert await reserved(db, "employee", emp.id) == 3000
    assert await reserved(db, "department", dep) == 3000
    body = usage_body(d["decision_id"], 1000, 100)
    await report(client, key, body)
    assert await reserved(db, "employee", emp.id) == 0
    assert await reserved(db, "department", dep) == 0
    assert {(o, a) for o, _, _, a in await _ledger(db)} == {("employee", 1500), ("department", 1500)}
    rk = await db.fetchrow("SELECT call_record_id FROM report_key WHERE caller_id = $1", caller_id)
    assert rk is not None and str(rk["call_record_id"]) == d["decision_id"]


async def test_report_id_unique_per_caller_not_global(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    """Ключ идемпотентности — пара (ключ вызывающей системы, report_id): у разных систем report_id может совпасть."""
    await _world(seed)
    keys = [(await seed.caller_key("gw-a"))[1], (await seed.caller_key("gw-b"))[1]]
    body = usage_body(None, 1000, 400, login="ivanov", model="C", occurred_at=msk(2026, 10, 14, 12))
    results = [await report(client, k, body) for k in keys]
    assert [r["status"] for r in results] == ["charged", "charged"]
    assert await db.fetchval("SELECT count(*) FROM report_key") == 2
