"""T071: фоновые задачи worker — уведомления (пороги по used_pct без резервов, fx_missing, reservation_no_report),
`agg_daily` и ретенция партиций `call_record`."""

from datetime import date
from typing import Any

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from bm.reports import maintenance
from tests.fixtures.qa import (
    admin_client_login,
    catalog,
    decide,
    decision_body,
    msk,
    notifications,
    report,
    spend,
    usage_body,
    worker_tick,
)
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db


async def kinds(db: asyncpg.Connection) -> list[tuple[Any, ...]]:
    rows = await db.fetch("SELECT kind, department_id, period, period_start FROM notification ORDER BY kind")
    return [tuple(r) for r in rows]


async def test_worker_has_report_jobs(app: Any) -> None:
    from bm.worker.jobs import default_jobs

    names = {j.name for j in default_jobs(app.state.budget)}
    assert {"notifications", "agg_daily", "retention"} <= names


async def test_open_reserves_do_not_trigger_threshold(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    """Пороги — по расходу без резервов (пакет 2, В1-5): 70% расхода + 30% резерва — не 80%."""
    await catalog(seed, only="A")
    dep = await seed.department("Маркетинг", limits={"month": 10_000}, allowed=("A",), policy_mode="observe")
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key()
    await spend(db, "department", dep, 7_000)
    await decide(client, key, decision_body(model="A"))  # резерв 30 ₽
    await worker_tick(app)
    assert await kinds(db) == []
    await spend(db, "department", dep, 1_000)  # 80% расхода
    await worker_tick(app)
    assert await kinds(db) == [("threshold_80", dep, "month", date(2026, 10, 1))]


async def test_zero_limit_with_spend_notifies_both(app: Any, seed: Seed, db: asyncpg.Connection) -> None:
    dep = await seed.department("Маркетинг", limits={"day": 0})
    await spend(db, "department", dep, 1)
    await worker_tick(app)
    assert {k[0] for k in await kinds(db)} == {"threshold_80", "threshold_100"}


async def test_fx_missing_once_per_month(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    await seed.model("U", currency="USD", in_per_1k="0.01", out_per_1k="0.02")
    await seed.fx(date(2026, 9, 1), "92.1")
    await worker_tick(app)
    await worker_tick(app)
    assert await kinds(db) == [("fx_missing", None, None, date(2026, 10, 1))]
    await admin_client_login(client, seed)
    (n,) = await notifications(client, "fx_missing")
    assert "октябрь 2026" in n["message"] and "92.1" in n["message"]
    clock.set(msk(2026, 11, 2, 10))
    await seed.fx(date(2026, 11, 1), "100")
    await worker_tick(app)
    assert len(await kinds(db)) == 1  # курс ноября есть — нового уведомления нет


async def test_no_fx_missing_without_usd(app: Any, seed: Seed, db: asyncpg.Connection) -> None:
    await seed.model("R")
    await worker_tick(app)
    assert await kinds(db) == []


async def test_reservation_no_report_once_per_day(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    await catalog(seed, only="A")
    dep = await seed.department("Маркетинг", allowed=("A",))
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key()
    await decide(client, key, decision_body(model="A"))
    await decide(client, key, decision_body(model="A"))
    clock.advance(minutes=16)
    await worker_tick(app)  # истечение и проверка в одном такте
    await worker_tick(app)
    assert await kinds(db) == [("reservation_no_report", dep, None, date(2026, 10, 14))]
    await admin_client_login(client, seed)
    (n,) = await notifications(client, "reservation_no_report")
    assert "2 резерв" in n["message"] and "60.00" in n["message"]


async def test_agg_daily_aggregates_closed_days(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    await catalog(seed, only="A")
    dep = await seed.department("Маркетинг", allowed=("A",))
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key()
    d = await decide(client, key, decision_body(model="A"))
    await report(client, key, usage_body(d["decision_id"], 1000, 400))
    await worker_tick(app)
    assert await db.fetchval("SELECT count(*) FROM agg_daily") == 0  # сегодняшние сутки не агрегируются
    clock.set(msk(2026, 10, 15, 1))
    await worker_tick(app)
    row = await db.fetchrow("SELECT * FROM agg_daily")
    assert (
        row["day"],
        row["department_id"],
        row["model_id"],
        row["source"],
        row["amount_kop"],
        row["tokens"],
    ) == (
        date(2026, 10, 14),
        dep,
        "A",
        "token",
        3_000,
        1_400,
    )
    await worker_tick(app)  # повтор — пересчёт, не удвоение
    assert await db.fetchval("SELECT sum(amount_kop) FROM agg_daily") == 3_000


async def test_retention_drops_old_partitions_and_keeps_aggregates(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    await seed.model("A", price_from=msk(2025, 1, 1))  # 10 + 50 ₽ за 1k
    dep = await seed.department("Маркетинг", allowed=("A",))
    await seed.employee("ivanov", department=dep, valid_from=date(2025, 1, 1))
    _, key = await seed.caller_key()
    await db.execute("SELECT bm_ensure_call_record_partitions('2025-08-01')")
    clock.set(msk(2025, 8, 20, 12))
    await app.state.budget.ensure_partitions()
    d = await decide(client, key, decision_body(model="A"))
    await report(client, key, usage_body(d["decision_id"], 1000, 400))  # 30 ₽ в августе 2025

    clock.set(msk(2026, 10, 14, 12))
    result = await maintenance.retention(app.state.pool, clock.now(), detail_months=12, agg_months=36)
    assert "call_record_2025_08" in result.dropped and "call_record_2025_09" in result.dropped
    names = {
        r["relname"]
        for r in await db.fetch(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'call_record'"
        )
    }
    assert {"call_record_2026_10", "call_record_2026_11", "call_record_2026_12"} <= names  # 2 месяца вперёд
    assert not any(n < "call_record_2025_10" for n in names)
    assert await db.fetchval("SELECT count(*) FROM report_key") == 0

    await admin_client_login(client, seed)
    rep = await client.get(
        "/api/v1/reports/spend", params={"from": "2025-08-01", "to": "2025-08-31", "group_by": "model,source"}
    )
    assert [(r["model_id"], r["source"], r["amount_rub"]) for r in rep.json()["items"]] == [
        ("A", "token", "30.00")
    ]

    clock.set(msk(2028, 9, 14, 12))
    again = await maintenance.retention(app.state.pool, clock.now(), detail_months=12, agg_months=36)
    assert again.agg_deleted == 1  # агрегаты старше 36 месяцев удалены
