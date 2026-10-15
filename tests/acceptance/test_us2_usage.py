"""T030: приёмочные тесты сценария 2 — отчёт о фактическом расходе и остатки (10_tech_spec.md, 2.1–2.6).

`POST /api/v1/usage`, `POST /api/v1/usage/batch`, `GET /api/v1/balance`; ответы проверяются по контракту.
Сценарий 2.5 (3 × 50 одновременных запросов) — tests/integration/test_concurrency.py (T020).
Истечение резервов вызывается так же, как это делает worker: `app.state.budget.expire_reserves(now)`.

Каталог (tests/fixtures/qa.py): A = 10 + 50 ₽ за 1k (1 000 / 400 → 30 ₽), C = 1 + 10 ₽ (→ 5 ₽).
Написаны до реализации (T031): xfail(strict=True) на уровне модуля.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from tests.conftest import START
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.qa import (
    TODAY,
    balance,
    call_record,
    catalog,
    decide,
    decision_body,
    expire_reserves,
    key_headers,
    ledger_total,
    limit_of,
    report,
    reserved,
    spend,
    usage_body,
)
from tests.fixtures.seed import Emp, Seed

pytestmark = [
    pytest.mark.db,
    pytest.mark.xfail(
        strict=True,
        raises=(AssertionError, ImportError),
        reason="T031: settle, пакет, истечение резервов и баланс ещё не реализованы",
    ),
]


@dataclass
class World:
    dep: UUID
    emp: Emp
    caller_id: UUID
    key: str


@pytest.fixture
async def w(seed: Seed) -> World:
    await catalog(seed)
    dep = await seed.department(
        "Маркетинг", limits={"day": 1_000_000, "month": 10_000_000}, allowed=("A", "C")
    )
    emp = await seed.employee("ivanov", department=dep, limits={"day": 50_000})
    caller_id, key = await seed.caller_key("gateway")
    return World(dep, emp, caller_id, key)


async def _both_ledgers(db: asyncpg.Connection, w: World) -> tuple[int, int]:
    return await ledger_total(db, "employee", w.emp.id), await ledger_total(db, "department", w.dep)


# ---------------------------------------------------------------- 2.1 точная сумма


async def test_2_1_exact_charge_and_reserve_released(
    client: httpx.AsyncClient, w: World, db: asyncpg.Connection
) -> None:
    d = await decide(client, w.key, decision_body(model="A", input_tokens=2000, max_output_tokens=1000))
    assert d["reserve_rub"] == "70.00"  # 20 + 50
    assert await reserved(db, "department", w.dep) == 7000
    r = await report(client, w.key, usage_body(d["decision_id"], 1000, 400))
    assert r["status"] == "charged" and r["amount_rub"] == "30.00"
    assert r["decision_id"] == d["decision_id"] and r["flags"] == []
    assert r["expense_date"] == TODAY.isoformat()
    # резерв снят целиком, факт — в реестре сотрудника и подразделения
    assert await reserved(db, "department", w.dep) == 0
    assert await reserved(db, "employee", w.emp.id) == 0
    assert await _both_ledgers(db, w) == (3000, 3000)
    row = await call_record(db, d["decision_id"])
    assert (row["status"], row["fact_kop"], row["tokens_in"], row["tokens_out"]) == (
        "settled",
        3000,
        1000,
        400,
    )


async def test_2_1_cached_tokens_without_cached_price_use_input_price(
    client: httpx.AsyncClient, w: World
) -> None:
    d = await decide(client, w.key, decision_body(model="A"))
    r = await report(client, w.key, usage_body(d["decision_id"], 1000, 400, cached=500))
    assert r["amount_rub"] == "35.00"  # 10 + 20 + 0.5 × 10


# ---------------------------------------------------------------- 2.2 идемпотентность


async def test_2_2_duplicate_by_decision_id(
    client: httpx.AsyncClient, w: World, db: asyncpg.Connection
) -> None:
    d = await decide(client, w.key, decision_body(model="A"))
    first = await report(client, w.key, usage_body(d["decision_id"], 1000, 400))
    again = await report(client, w.key, usage_body(d["decision_id"], 5000, 5000))  # новый report_id
    assert first["status"] == "charged"
    assert again["status"] == "duplicate" and "duplicate" in again["flags"]
    assert again["amount_rub"] == "30.00"  # сумма первой обработки, а не новая
    assert await _both_ledgers(db, w) == (3000, 3000)


async def test_2_2_duplicate_by_report_id(
    client: httpx.AsyncClient, w: World, db: asyncpg.Connection
) -> None:
    body = usage_body(None, 1000, 400, login="ivanov", model="A", occurred_at=START)
    first = await report(client, w.key, body)
    again = await report(client, w.key, body)
    assert (first["status"], again["status"]) == ("charged", "duplicate")
    assert await _both_ledgers(db, w) == (3000, 3000)


# ---------------------------------------------------------------- 2.3 нет отчёта 15 минут


async def test_2_3_reserve_not_expired_before_ttl(
    app: Any, client: httpx.AsyncClient, w: World, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    d = await decide(client, w.key, decision_body(model="A"))
    clock.advance(minutes=14, seconds=59)
    assert await expire_reserves(app, clock.now()) == 0
    assert (await call_record(db, d["decision_id"]))["status"] == "open"


async def test_2_3_expired_reserve_charged_by_estimate_no_report(
    app: Any, client: httpx.AsyncClient, w: World, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    d = await decide(client, w.key, decision_body(model="A"))
    clock.advance(minutes=15, seconds=1)
    assert await expire_reserves(app, clock.now()) == 1
    assert await expire_reserves(app, clock.now()) == 0  # повтор ничего не меняет
    row = await call_record(db, d["decision_id"])
    assert row["status"] == "expired" and row["fact_kop"] == 3000
    assert "no_report" in row["flags"]
    assert await reserved(db, "department", w.dep) == 0
    assert await _both_ledgers(db, w) == (3000, 3000)
    month = limit_of(await balance(client, w.key), "department", "month")
    assert (month["spent"]["token_rub"], month["reserved_rub"]) == ("30.00", "0.00")


async def test_late_report_after_expiry_replaces_estimate(
    app: Any, client: httpx.AsyncClient, w: World, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    """12_plan.md §9 п. 2: поздний отчёт заменяет оценку фактом — дельта в реестр, флаг остаётся, повтора нет."""
    d = await decide(client, w.key, decision_body(model="A"))  # оценка 30 ₽
    clock.advance(minutes=20)
    await expire_reserves(app, clock.now())
    assert await _both_ledgers(db, w) == (3000, 3000)
    late = await report(client, w.key, usage_body(d["decision_id"], 2000, 0))  # факт 20 ₽
    assert late["status"] == "charged" and late["amount_rub"] == "20.00"
    assert "no_report" in late["flags"]
    assert await _both_ledgers(db, w) == (2000, 2000)
    again = await report(client, w.key, usage_body(d["decision_id"], 2000, 0))
    assert again["status"] == "duplicate"
    assert await _both_ledgers(db, w) == (2000, 2000)
    assert (await call_record(db, d["decision_id"]))["fact_kop"] == 2000


# ---------------------------------------------------------------- 2.4 оборванный стрим


async def test_2_4_aborted_stream_charges_only_fact(
    client: httpx.AsyncClient, w: World, db: asyncpg.Connection
) -> None:
    d = await decide(client, w.key, decision_body(model="A"))  # резерв 30 ₽
    r = await report(client, w.key, usage_body(d["decision_id"], 1000, 100, call_status="aborted"))
    assert r["status"] == "charged" and r["amount_rub"] == "15.00"  # 10 + 5
    assert await reserved(db, "department", w.dep) == 0  # остаток резерва освобождён
    assert await _both_ledgers(db, w) == (1500, 1500)
    assert (await call_record(db, d["decision_id"]))["call_status"] == "aborted"


# ---------------------------------------------------------------- 2.6 без решения (fail-open)


async def test_2_6_report_without_decision(
    client: httpx.AsyncClient, w: World, db: asyncpg.Connection
) -> None:
    r = await report(client, w.key, usage_body(None, 1000, 400, login="ivanov", model="C", occurred_at=START))
    assert r["status"] == "charged" and r["amount_rub"] == "5.00"
    assert r["decision_id"] is None and "no_decision" in r["flags"]
    assert await _both_ledgers(db, w) == (500, 500)
    row = await db.fetchrow("SELECT outcome, final_model, flags FROM call_record WHERE fact_kop = 500")
    assert row is not None and row["outcome"] == "none" and "no_decision" in row["flags"]


@pytest.mark.parametrize("missing", ["subject", "model", "occurred_at"])
async def test_2_6_report_without_decision_requires_subject_model_time(
    client: httpx.AsyncClient, w: World, missing: str
) -> None:
    body = usage_body(None, 1000, 400, login="ivanov", model="C", occurred_at=START)
    body.pop(missing)
    resp = await client.post("/api/v1/usage", json=body, headers=key_headers(w.key))
    assert resp.status_code == 422, resp.text
    assert_matches_contract(resp, "reportUsage")


async def test_2_6_report_older_than_7_days_rejected(client: httpx.AsyncClient, w: World) -> None:
    old = START - timedelta(days=8)
    resp = await client.post(
        "/api/v1/usage",
        json=usage_body(None, 1000, 400, login="ivanov", model="C", occurred_at=old),
        headers=key_headers(w.key),
    )
    assert resp.status_code == 422, resp.text
    assert_matches_contract(resp, "reportUsage")


# ---------------------------------------------------------------- решение не выполнено


async def test_decision_not_followed_charges_actual_model(
    client: httpx.AsyncClient, w: World, db: asyncpg.Connection
) -> None:
    await spend(db, "department", w.dep, 9_998_000)  # остаток месяца 20 ₽ → A перенаправляется на C
    d = await decide(client, w.key, decision_body(model="A"))
    assert d["outcome"] == "redirect" and d["model"] == "C"
    r = await report(client, w.key, usage_body(d["decision_id"], 1000, 400, actual_model="A"))
    assert r["amount_rub"] == "30.00"  # по фактической модели A
    assert "decision_not_followed" in r["flags"]
    assert "decision_not_followed" in (await call_record(db, d["decision_id"]))["flags"]


# ---------------------------------------------------------------- ошибки


async def test_unknown_decision_id_is_404(client: httpx.AsyncClient, w: World) -> None:
    resp = await client.post("/api/v1/usage", json=usage_body(str(uuid.uuid4())), headers=key_headers(w.key))
    assert resp.status_code == 404 and resp.json()["code"] == "decision_not_found"
    assert_matches_contract(resp, "reportUsage")


async def test_decision_of_other_caller_is_404(client: httpx.AsyncClient, w: World, seed: Seed) -> None:
    d = await decide(client, w.key, decision_body(model="A"))
    _, other_key = await seed.caller_key("other")
    resp = await client.post(
        "/api/v1/usage", json=usage_body(d["decision_id"]), headers=key_headers(other_key)
    )
    assert resp.status_code == 404 and resp.json()["code"] == "decision_not_found"


async def test_usage_without_key_is_401(client: httpx.AsyncClient, w: World) -> None:
    resp = await client.post("/api/v1/usage", json=usage_body(str(uuid.uuid4())))
    assert resp.status_code == 401
    assert_matches_contract(resp, "reportUsage")


# ---------------------------------------------------------------- пакет (FR-011)


async def test_batch_1000_rows_with_10_duplicates(
    client: httpx.AsyncClient, w: World, db: asyncpg.Connection
) -> None:
    now = START
    unique = [usage_body(None, 100, 0, login="ivanov", model="A", occurred_at=now) for _ in range(990)]
    items = unique + [dict(unique[i]) for i in range(10)]  # 10 повторов тех же report_id
    resp = await client.post("/api/v1/usage/batch", json={"items": items}, headers=key_headers(w.key))
    assert resp.status_code == 200, resp.text[:300]
    assert_matches_contract(resp, "reportUsageBatch")
    results = resp.json()["results"]
    assert len(results) == 1000 and sorted(x["index"] for x in results) == list(range(1000))
    assert all(x["ok"] for x in results)
    statuses = [x["result"]["status"] for x in results]
    assert statuses.count("charged") == 990 and statuses.count("duplicate") == 10
    assert await _both_ledgers(db, w) == (99_000, 99_000)  # 990 × 1 ₽
    assert await db.fetchval("SELECT count(*) FROM report_key WHERE caller_id = $1", w.caller_id) == 990


async def test_batch_rows_are_independent(
    client: httpx.AsyncClient, w: World, db: asyncpg.Connection
) -> None:
    now = START
    good = usage_body(None, 100, 0, login="ivanov", model="A", occurred_at=now)
    bad = usage_body(str(uuid.uuid4()), 100, 0)  # решения нет
    resp = await client.post("/api/v1/usage/batch", json={"items": [good, bad]}, headers=key_headers(w.key))
    assert resp.status_code == 200
    assert_matches_contract(resp, "reportUsageBatch")
    by_index = {x["index"]: x for x in resp.json()["results"]}
    assert by_index[0]["ok"] is True and by_index[1]["ok"] is False
    assert by_index[1]["error"]["code"] == "decision_not_found"
    assert await _both_ledgers(db, w) == (100, 100)


async def test_batch_over_1000_rows_is_413(client: httpx.AsyncClient, w: World) -> None:
    now = START
    items = [usage_body(None, 1, 0, login="ivanov", model="A", occurred_at=now) for _ in range(1001)]
    resp = await client.post("/api/v1/usage/batch", json={"items": items}, headers=key_headers(w.key))
    assert resp.status_code == 413 and resp.json()["code"] == "batch_too_large"
    assert_matches_contract(resp, "reportUsageBatch")


# ---------------------------------------------------------------- баланс = ручной пересчёт (FR-026)


async def test_balance_matches_manual_recompute(
    client: httpx.AsyncClient, w: World, db: asyncpg.Connection
) -> None:
    await spend(
        db, "department", w.dep, 100_000, day=TODAY - timedelta(days=1)
    )  # вчера: 1 000 ₽ подразделения
    d1 = await decide(client, w.key, decision_body(model="A"))
    await report(client, w.key, usage_body(d1["decision_id"], 1000, 400))  # факт 30 ₽
    await decide(client, w.key, decision_body(model="A"))  # открытый резерв 30 ₽
    await report(client, w.key, usage_body(None, 1000, 400, login="ivanov", model="C", occurred_at=START))

    b = await balance(client, w.key, "ivanov")
    assert b["owner_type"] == "employee" and b["owner_ref"] == w.emp.pseudonym
    assert b["department_id"] == str(w.dep)
    # ручной пересчёт: лимит − факт − резервы − импорт − подписки
    expected = {
        ("employee", "day"): ("500.00", "35.00", "30.00", "435.00"),
        ("department", "day"): ("10000.00", "35.00", "30.00", "9935.00"),
        ("department", "month"): ("100000.00", "1035.00", "30.00", "98935.00"),
    }
    got = {
        (x["owner_type"], x["period"]): (
            x["limit_rub"],
            x["spent"]["token_rub"],
            x["reserved_rub"],
            x["remaining_rub"],
        )
        for x in b["limits"]
    }
    assert got == expected
    for x in b["limits"]:
        assert (x["spent"]["import_rub"], x["spent"]["subscriptions_rub"]) == ("0.00", "0.00")
        assert x["over_limit"] is False
    assert limit_of(b, "department", "month")["resets_at"].startswith("2026-11-01T00:00:00")


async def test_balance_unknown_subject_is_404(client: httpx.AsyncClient, w: World) -> None:
    resp = await client.get("/api/v1/balance", params={"login": "ghost"}, headers=key_headers(w.key))
    assert resp.status_code == 404 and resp.json()["code"] == "subject_unknown"
    assert_matches_contract(resp, "getCallerBalance")
