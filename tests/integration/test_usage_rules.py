"""T031: правила учёта факта и остатков, не покрытые приёмочными тестами QA.

- строка пакета с ошибкой схемы не мешает остальным (FR-011);
- отчёт без решения за субъекта вне `subject_scope` — 403 и запись в аудит;
- повтор отчёта старше 7 суток распознаётся как дубль, а не 422 (досылка из буфера клиента);
- остаток подразделения для ключа со scope=service;
- расход сотрудника до перевода остаётся у старого подразделения (дата расхода = occurred_at).
"""

import uuid
from datetime import date, timedelta
from uuid import UUID

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.qa import (
    balance,
    catalog,
    decide,
    decision_body,
    key_headers,
    ledger_total,
    limit_of,
    report,
    spend,
    usage_body,
)
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db


async def _world(seed: Seed) -> tuple[UUID, str]:
    await catalog(seed, only="AC")
    dep = await seed.department("Маркетинг", limits={"month": 10_000_000}, allowed=("A", "C"))
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key()
    return dep, key


async def test_batch_row_with_schema_error_is_isolated(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    dep, key = await _world(seed)
    good = usage_body(None, 1000, 400, login="ivanov", model="C", occurred_at=clock.now())
    bad = good | {"report_id": str(uuid.uuid4()), "prompt": "текст запроса"}
    resp = await client.post("/api/v1/usage/batch", json={"items": [bad, good]}, headers=key_headers(key))
    assert resp.status_code == 200, resp.text
    assert_matches_contract(resp, "reportUsageBatch")
    rows = {x["index"]: x for x in resp.json()["results"]}
    assert rows[0]["ok"] is False and rows[0]["error"]["code"] == "validation_error"
    assert "текст запроса" not in resp.text
    assert rows[1]["ok"] is True and rows[1]["result"]["amount_rub"] == "5.00"
    assert await ledger_total(db, "department", dep) == 500


async def test_batch_envelope_errors(client: httpx.AsyncClient, seed: Seed) -> None:
    _, key = await _world(seed)
    for body in ({"items": []}, {"items": [], "extra": 1}, {"rows": []}):
        resp = await client.post("/api/v1/usage/batch", json=body, headers=key_headers(key))
        assert resp.status_code == 422, body
        assert_matches_contract(resp, "reportUsageBatch")


async def test_free_report_out_of_scope_is_403_and_audited(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    await _world(seed)
    other = await seed.department("Разработка")
    caller_id, key = await seed.caller_key("dev-gw", scope_type="department_employees", department=other)
    resp = await client.post(
        "/api/v1/usage",
        json=usage_body(None, 1000, 400, login="ivanov", model="C", occurred_at=clock.now()),
        headers=key_headers(key),
    )
    assert resp.status_code == 403 and resp.json()["code"] == "subject_out_of_scope"
    assert_matches_contract(resp, "reportUsage")
    action = await db.fetchval("SELECT action FROM audit_log WHERE actor = $1", f"caller:{caller_id}")
    assert action == "forbidden_access"
    assert await db.fetchval("SELECT count(*) FROM ledger_daily") == 0


async def test_old_duplicate_is_duplicate_not_422(
    client: httpx.AsyncClient, seed: Seed, clock: FrozenClock
) -> None:
    _, key = await _world(seed)
    body = usage_body(None, 1000, 400, login="ivanov", model="C", occurred_at=clock.now())
    assert (await report(client, key, body))["status"] == "charged"
    clock.advance(days=8)
    again = await report(client, key, body)
    assert again["status"] == "duplicate" and again["amount_rub"] == "5.00"


async def test_service_key_balance_is_department_balance(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, _ = await _world(seed)
    _, svc = await seed.caller_key("batch", scope_type="service", department=dep)
    await spend(db, "department", dep, 250_000)
    d = await decide(client, svc, decision_body(login=None, model="A"))
    assert d["outcome"] == "allow"
    b = await balance(client, svc, login=None)
    assert (b["owner_type"], b["owner_ref"], b["department_id"]) == ("department", str(dep), str(dep))
    month = limit_of(b, "department", "month")
    assert (month["spent"]["token_rub"], month["reserved_rub"], month["remaining_rub"]) == (
        "2500.00",
        "30.00",
        "97470.00",
    )
    assert month["used_pct"] == "2.5" and month["subscriptions_share_pct"] == "0.0"
    # ключу сервиса нельзя спрашивать остаток сотрудника
    resp = await client.get("/api/v1/balance", params={"login": "ivanov"}, headers=key_headers(svc))
    assert resp.status_code == 403
    assert_matches_contract(resp, "getCallerBalance")


async def test_free_report_charges_department_of_expense_date(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    """Сотрудник переведён 14.10; отчёт о вызове 13.10 ложится на старое подразделение."""
    old, key = await _world(seed)
    new = await seed.department("Разработка", allowed=("C",))
    emp_id = await db.fetchval("SELECT id FROM employee WHERE ad_login = 'ivanov'")
    await db.execute(
        "UPDATE employee_assignment SET valid_to = $2 WHERE employee_id = $1", emp_id, date(2026, 10, 13)
    )
    await db.execute(
        "INSERT INTO employee_assignment(employee_id, department_id, valid_from) VALUES ($1, $2, $3)",
        emp_id,
        new,
        date(2026, 10, 14),
    )
    r = await report(
        client,
        key,
        usage_body(None, 1000, 400, login="ivanov", model="C", occurred_at=clock.now() - timedelta(days=1)),
    )
    assert r["expense_date"] == "2026-10-13"
    assert await ledger_total(db, "department", old) == 500
    assert await ledger_total(db, "department", new) == 0
