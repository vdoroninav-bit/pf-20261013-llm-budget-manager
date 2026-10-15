"""T021: транзакция решения в БД (12_arch_decision.md §4.5, ADR-002) — то, чего не видно через HTTP.

- `decision_id` — UUIDv7, `call_record` и резерв в `budget_lock` пишутся одной транзакцией;
- реестр суммируется по окнам периодов МСК (день, неделя на стыке месяцев, месяц);
- партиция `call_record` создаётся на месяц часов приложения при старте;
- `integration-config` отдаёт ETag и 304 на `If-None-Match`;
- аудит отказа `subject_out_of_scope`.
"""

from datetime import date
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.qa import (
    call_record,
    catalog,
    decide,
    decision_body,
    key_headers,
    msk,
    post_decision,
    reserved,
    spend,
)
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db


async def _world(seed: Seed, **dep_kw: Any) -> tuple[UUID, str]:
    await catalog(seed, only="ACZ")
    dep = await seed.department("Маркетинг", allowed=("A", "C", "Z"), **dep_kw)
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key()
    return dep, key


async def test_decision_id_is_uuid7_and_record_fields(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    _, key = await _world(seed, limits={"month": 10_000_000})
    d = await decide(client, key, decision_body(model="A"))
    decision_id = UUID(d["decision_id"])
    assert decision_id.version == 7
    row = await call_record(db, d["decision_id"])
    assert row["expense_date"] == date(2026, 10, 14)
    assert row["created_at"] == clock.now()
    assert (row["est_in"], row["est_out"], row["est_source"]) == (1000, 400, "request")
    assert row["price_id"] is not None and row["fx"] == 1
    assert row["flags"] == []
    assert d["created_at"].endswith("+03:00")


async def test_reserve_goes_to_employee_and_department_locks(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, key = await _world(seed)  # лимитов нет — ограничения нет, но резерв учитывается
    d = await decide(client, key, decision_body(model="A"))
    assert d["outcome"] == "allow" and d["applied_limit"] is None
    emp_id = await db.fetchval("SELECT id FROM employee WHERE ad_login = 'ivanov'")
    assert await reserved(db, "employee", emp_id) == 3000
    assert await reserved(db, "department", dep) == 3000


async def test_deny_writes_closed_record_without_reserve(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, key = await _world(seed, limits={"month": 100})
    d = await decide(client, key, decision_body(model="A", kind="embedding"))
    assert d["outcome"] == "deny" and d["expires_at"] is None
    row = await call_record(db, d["decision_id"])
    assert (row["status"], row["reserve_kop"], row["expires_at"]) == ("closed", 0, None)
    assert await reserved(db, "department", dep) == 0


async def test_spent_is_summed_by_msk_period_windows(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    """Четверг 01.10.2026: неделя началась 28.09 (сентябрь), месяц — 01.10."""
    clock.set(msk(2026, 10, 1, 12))
    dep, key = await _world(seed, limits={"week": 100_000, "month": 100_000})
    await spend(db, "department", dep, 40_000, day=date(2026, 9, 28))  # в неделе, не в месяце
    await spend(db, "department", dep, 10_000, day=date(2026, 9, 27))  # прошлая неделя
    await spend(db, "department", dep, 20_000, day=date(2026, 10, 1), source="import")
    d = await decide(client, key, decision_body(model="A"))
    lim = d["applied_limit"]
    # неделя: 1000 − 400 − 200 = 400 ₽; месяц: 1000 − 200 = 800 ₽
    assert (lim["period"], lim["remaining_rub"]) == ("week", "400.00")


async def test_partition_created_for_clock_month_on_startup(
    db: asyncpg.Connection, seed: Seed, settings: Any
) -> None:
    from bm.main import create_app

    _, key = await _world(seed)
    clock = FrozenClock(msk(2027, 3, 10, 9))  # месяц без партиции из миграции
    app = create_app(settings, clock=clock)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8080") as c:
            d = await decide(c, key, decision_body(model="A"))
    assert d["outcome"] == "allow"
    assert await db.fetchval("SELECT to_regclass('call_record_2027_03') IS NOT NULL")


async def test_any_employee_key_cannot_use_service_subject(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    _, key = await _world(seed)
    resp = await post_decision(client, key, decision_body(login=None))
    assert resp.status_code == 403 and resp.json()["code"] == "subject_out_of_scope"
    assert_matches_contract(resp, "createDecision")


async def test_department_key_accepts_own_employee(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, _ = await _world(seed)
    _, key = await seed.caller_key("dep-gw", scope_type="department_employees", department=dep)
    d = await decide(client, key, decision_body(model="C"))
    assert d["outcome"] == "allow" and d["department_id"] == str(dep)


async def test_integration_config_etag(client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection) -> None:
    dep, key = await _world(seed)
    closed = await seed.department("Казначейство", fail_mode="closed")
    resp = await client.get("/api/v1/integration-config", headers=key_headers(key))
    assert resp.status_code == 200, resp.text
    assert_matches_contract(resp, "getIntegrationConfig")
    body = resp.json()
    assert body["timeout_ms"] == 50 and body["refresh_s"] == 60 and body["default_fail_mode"] == "open"
    assert {(x["department_id"], x["fail_mode"]) for x in body["departments"]} == {
        (str(dep), "open"),
        (str(closed), "closed"),
    }
    etag = resp.headers["ETag"]
    again = await client.get("/api/v1/integration-config", headers=key_headers(key) | {"If-None-Match": etag})
    assert again.status_code == 304 and again.headers["ETag"] == etag
    assert_matches_contract(again, "getIntegrationConfig")
    unauth = await client.get("/api/v1/integration-config")
    assert unauth.status_code == 401
    assert_matches_contract(unauth, "getIntegrationConfig")


async def test_integration_config_scoped_key_sees_own_department(
    client: httpx.AsyncClient, seed: Seed
) -> None:
    dep, _ = await _world(seed)
    await seed.department("Казначейство", fail_mode="closed")
    _, key = await seed.caller_key("svc", scope_type="service", department=dep)
    body = (await client.get("/api/v1/integration-config", headers=key_headers(key))).json()
    assert body["departments"] == [{"department_id": str(dep), "fail_mode": "open"}]
