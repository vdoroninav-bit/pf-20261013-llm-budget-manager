"""T071: отчёты, CSV, прослеживаемость остатка, журналы событий и аудита — на прямом засеве БД
(без конвейера импорта и API подписок, чтобы проверять только код отчётов)."""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.qa import (
    admin_client_login,
    bump_config,
    catalog,
    decide,
    decision_body,
    msk,
    report,
    session_as,
    usage_body,
)
from tests.fixtures.seed import Emp, Seed

pytestmark = pytest.mark.db

OCTOBER = {"from": "2026-10-01", "to": "2026-10-31"}


@dataclass
class W:
    a: UUID
    b: UUID
    ivanov: Emp
    petrov: Emp
    key: str
    open_decision: str


async def import_line(
    db: asyncpg.Connection,
    ref: str,
    day: date,
    kop: int,
    department: UUID | None,
    *,
    status: str = "charged",
    orig: str = "2.00",
    fetched: datetime | None = None,
) -> None:
    await db.execute(
        "INSERT INTO import_line(provider, source_ref, model, day, amount_orig, currency, fx, amount_kop, "
        "department_id, status, fetched_at) VALUES ('anthropic', $1, 'claude-x', $2, $3, 'USD', 95.5, $4, $5, $6, $7)",
        ref,
        day,
        Decimal(orig),
        kop,
        department,
        status,
        fetched or msk(2026, 10, 14, 4),
    )
    if status == "charged" and department is not None:
        await db.execute(
            "INSERT INTO ledger_daily(owner_type, owner_id, day, source, amount_kop) "
            "VALUES ('department', $1, $2, 'import', $3) ON CONFLICT (owner_type, owner_id, day, source) "
            "DO UPDATE SET amount_kop = ledger_daily.amount_kop + EXCLUDED.amount_kop",
            department,
            day,
            kop,
        )


async def world(client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection) -> W:
    await catalog(seed, only="AC")
    a = await seed.department("Маркетинг", limits={"day": 1_000_000, "month": 10_000_000}, allowed=("A", "C"))
    b = await seed.department("Разработка", limits={"month": 10_000_000}, allowed=("A",))
    ivanov = await seed.employee("ivanov", department=a, display_name="Иванов Иван")
    petrov = await seed.employee("petrov", department=b, display_name="Петров Пётр")
    _, key = await seed.caller_key()
    for login, model, tokens in (
        ("ivanov", "A", (1000, 400)),
        ("ivanov", "C", (1000, 400)),
        ("petrov", "A", (2000, 1000)),
    ):
        d = await decide(client, key, decision_body(login=login, model=model))
        await report(client, key, usage_body(d["decision_id"], *tokens))
    opened = await decide(client, key, decision_body(login="ivanov", model="A"))  # резерв 30 ₽ открыт
    await import_line(db, "wrkspc_dev", date(2026, 10, 13), 19_100, b)
    await import_line(db, "wrkspc_lost", date(2026, 10, 13), 9_550, None, status="unallocated", orig="1.00")
    await import_line(db, "wrkspc_gw", date(2026, 10, 13), 4_775, b, status="reconcile", orig="0.50")
    return W(a, b, ivanov, petrov, key, opened["decision_id"])


def money(rows: list[dict[str, Any]], *keys: str) -> dict[tuple[Any, ...], str]:
    return {tuple(r[k] for k in keys): r["amount_rub"] for r in rows}


# ---------------------------------------------------------------- getSpendReport


async def test_spend_report_groups_sources_and_unallocated(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    w = await world(client, seed, db)
    await admin_client_login(client, seed)
    resp = await client.get("/api/v1/reports/spend", params=OCTOBER | {"group_by": "department,model,source"})
    assert resp.status_code == 200, resp.text
    assert_matches_contract(resp, "getSpendReport")
    assert money(resp.json()["items"], "department_id", "model_id", "source") == {
        (str(w.a), "A", "token"): "30.00",
        (str(w.a), "C", "token"): "5.00",
        (str(w.b), "A", "token"): "70.00",
        (str(w.b), "claude-x", "import"): "191.00",
        (None, "claude-x", "import"): "95.50",  # «не распределено»; reconcile — не расход
    }
    assert resp.json()["total_rub"] == "391.50"  # открытый резерв — не расход

    un = await client.get(
        "/api/v1/reports/spend", params=OCTOBER | {"department_id": "unallocated", "group_by": "source"}
    )
    assert [(r["source"], r["amount_rub"], r["department_id"]) for r in un.json()["items"]] == [
        ("import", "95.50", None)
    ]

    by_day = await client.get(
        "/api/v1/reports/spend", params=OCTOBER | {"group_by": "day,source", "department_id": str(w.b)}
    )
    assert money(by_day.json()["items"], "period_start", "source") == {
        ("2026-10-13", "import"): "191.00",
        ("2026-10-14", "token"): "70.00",
    }
    one_row = await client.get("/api/v1/reports/spend", params=OCTOBER)
    assert [r["amount_rub"] for r in one_row.json()["items"]] == ["391.50"]

    bad = await client.get("/api/v1/reports/spend", params=OCTOBER | {"group_by": "employee"})
    assert bad.status_code == 422


async def test_late_import_is_flagged(client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection) -> None:
    dep = await seed.department("Аналитика")
    await import_line(db, "wrkspc_a", date(2026, 9, 30), 19_100, dep, fetched=msk(2026, 10, 2, 4))
    await import_line(db, "wrkspc_a", date(2026, 9, 29), 9_550, dep, fetched=msk(2026, 9, 30, 4), orig="1.00")
    await admin_client_login(client, seed)
    resp = await client.get(
        "/api/v1/reports/spend", params={"from": "2026-09-01", "to": "2026-09-30", "group_by": "day,source"}
    )
    late = {r["period_start"]: r["late"] for r in resp.json()["items"]}
    assert late == {"2026-09-29": False, "2026-09-30": True}


async def test_owner_report_is_limited_to_own_department(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    w = await world(client, seed, db)
    async with session_as(client, seed, "owner_a", role="budget_owner", department=w.a) as (owner, _):
        resp = await owner.get("/api/v1/reports/spend", params=OCTOBER | {"group_by": "department"})
        assert [(r["department_id"], r["amount_rub"]) for r in resp.json()["items"]] == [(str(w.a), "35.00")]
        for dep in ("unallocated", str(w.b)):
            denied = await owner.get("/api/v1/reports/spend", params=OCTOBER | {"department_id": dep})
            assert denied.status_code == 403
        own = await owner.get("/api/v1/reports/spend", params=OCTOBER | {"department_id": str(w.a)})
        assert own.status_code == 200


# ---------------------------------------------------------------- CSV


async def test_csv_neutralizes_formula_prefixes(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    await seed.model("-2+3", in_per_1k="10", out_per_1k="50")
    deps = [
        await seed.department(n, allowed=("-2+3",)) for n in ('=HYPERLINK("http://x")', "@SUM(A1)", "+cmd")
    ]
    for i, dep in enumerate(deps):
        await seed.employee(f"user{i}", department=dep)
    _, key = await seed.caller_key()
    for i in range(3):
        d = await decide(client, key, decision_body(login=f"user{i}", model="-2+3"))
        await report(client, key, usage_body(d["decision_id"], 1000, 400))
    await admin_client_login(client, seed)
    resp = await client.get("/api/v1/reports/spend.csv", params=OCTOBER)
    assert resp.status_code == 200
    assert_matches_contract(resp, "getSpendCsv")
    assert resp.headers["content-disposition"].startswith("attachment")
    rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig")), delimiter=";"))
    assert {r["department"] for r in rows} == {'\'=HYPERLINK("http://x")', "'@SUM(A1)", "'+cmd"}
    assert {r["model"] for r in rows} == {"'-2+3"} and {r["amount_rub"] for r in rows} == {"30.00"}


async def test_csv_unallocated_row_and_original_currency(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    await world(client, seed, db)
    await admin_client_login(client, seed)
    resp = await client.get("/api/v1/reports/spend.csv", params={"from": "2026-10-05", "to": "2026-10-31"})
    text = resp.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    lost = next(r for r in rows if r["department"] == "Не распределено")
    assert (lost["source"], lost["amount_rub"], lost["original_currency"], lost["original_amount"]) == (
        "import",
        "95.50",
        "USD",
        "1.00",
    )
    assert {r["period_start"] for r in rows} == {"2026-10-05"}  # начало месяца, но не раньше «с»
    assert "emp-" not in text and "ivanov" not in text


# ---------------------------------------------------------------- баланс и прослеживаемость


async def test_department_balance_and_items_trace_every_kopeck(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    w = await world(client, seed, db)
    await admin_client_login(client, seed)
    bal = await client.get(f"/api/v1/departments/{w.a}/balance")
    assert bal.status_code == 200, bal.text
    assert_matches_contract(bal, "getDepartmentBalance")
    month = next(x for x in bal.json()["limits"] if x["period"] == "month")
    assert (month["spent"]["token_rub"], month["reserved_rub"], month["remaining_rub"]) == (
        "35.00",
        "30.00",
        "99935.00",
    )
    assert month["used_pct"] == "0.0" and month["over_limit"] is False

    items = await client.get(f"/api/v1/departments/{w.a}/balance/items", params={"period": "month"})
    assert_matches_contract(items, "listDepartmentBalanceItems")
    by_source: dict[str, int] = {}
    for it in items.json()["items"]:
        by_source[it["source"]] = by_source.get(it["source"], 0) + int(Decimal(it["amount_rub"]) * 100)
        assert it["subject_pseudonym"] == w.ivanov.pseudonym
    assert by_source == {"token": 3_500, "reservation": 3_000}
    assert items.json()["total"] == 3

    page2 = await client.get(
        f"/api/v1/departments/{w.a}/balance/items", params={"period": "month", "page": 2, "page_size": 2}
    )
    assert len(page2.json()["items"]) == 1 and page2.json()["total"] == 3
    only = await client.get(
        f"/api/v1/departments/{w.b}/balance/items", params={"period": "month", "source": "import"}
    )
    assert [(x["source"], x["amount_rub"], x["model_id"]) for x in only.json()["items"]] == [
        ("import", "191.00", "claude-x")
    ]

    async with session_as(client, seed, "fin", role="finance_viewer") as (fin, _):
        hidden = await fin.get(f"/api/v1/departments/{w.a}/balance/items", params={"period": "month"})
        assert {x["subject_pseudonym"] for x in hidden.json()["items"]} == {None}

    missing = await client.get(f"/api/v1/departments/{UUID(int=1)}/balance")
    assert missing.status_code == 404
    assert_matches_contract(missing, "getDepartmentBalance")


async def test_balance_in_past_period_has_no_open_reserves(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    w = await world(client, seed, db)
    clock.set(msk(2026, 11, 2, 10))
    await bump_config(db)
    await admin_client_login(client, seed)
    resp = await client.get(f"/api/v1/departments/{w.a}/balance", params={"at": "2026-10-31T23:59:00+03:00"})
    month = next(x for x in resp.json()["limits"] if x["period"] == "month")
    assert (month["reserved_rub"], month["remaining_rub"]) == ("0.00", "99965.00")
    items = await client.get(
        f"/api/v1/departments/{w.a}/balance/items", params={"period": "month", "at": "2026-10-31T23:59:00"}
    )
    assert {x["source"] for x in items.json()["items"]} == {"token"}  # резерв — только в текущем периоде


async def test_subscriptions_in_report_and_items(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep = await seed.department("Аналитика", limits={"month": 10_000_000})
    emp = await seed.employee("ivanov", department=dep)
    for assignee_type, assignee in (("department", dep), ("employee", emp.id), ("unallocated", None)):
        await db.execute(
            "INSERT INTO subscription(provider, plan, seat_price, currency, seats, start_date, assignee_type, "
            "assignee_id) VALUES ('openai', 'Codex', 3100, 'RUB', 1, '2026-10-01', $1, $2)",
            assignee_type,
            assignee,
        )
    await admin_client_login(client, seed)
    rep = await client.get("/api/v1/reports/spend", params=OCTOBER | {"group_by": "department,source"})
    assert money(rep.json()["items"], "department_id", "source") == {
        (str(dep), "subscription"): "2800.00",  # 2 × 3 100 × 14 / 31 — по сегодня включительно
        (None, "subscription"): "1400.00",
    }
    items = await client.get(f"/api/v1/departments/{dep}/balance/items", params={"period": "month"})
    subs = [x for x in items.json()["items"] if x["source"] == "subscription"]
    assert sorted(x["amount_rub"] for x in subs) == ["1400.00", "1400.00"]
    assert {x["subject_pseudonym"] for x in subs} == {None, emp.pseudonym}
    bal = await client.get(f"/api/v1/departments/{dep}/balance")
    assert (
        next(x for x in bal.json()["limits"] if x["period"] == "month")["spent"]["subscriptions_rub"]
        == "2800.00"
    )


# ---------------------------------------------------------------- журналы


async def test_events_filters_and_access(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    w = await world(client, seed, db)
    unknown = await decide(client, w.key, decision_body(model="no-such"))
    await admin_client_login(client, seed)
    allr = await client.get("/api/v1/events", params=OCTOBER | {"page_size": 200})
    assert allr.status_code == 200
    assert_matches_contract(allr, "listEvents")
    assert allr.json()["total"] == 5
    ev = {e["id"]: e for e in allr.json()["items"]}
    assert ev[w.open_decision]["status"] == "open" and ev[w.open_decision]["reserve_rub"] == "30.00"
    assert ev[unknown["decision_id"]]["code"] == "model_unknown"
    settled = [e for e in allr.json()["items"] if e["status"] == "settled"]
    assert all(e["tokens"] and e["fact_rub"] and e["caller_name"] == "gateway" for e in settled)
    for params, n in (
        ({"code": "model_unknown"}, 1),
        ({"outcome": "allow"}, 4),
        ({"department_id": str(w.b)}, 1),
        ({"flag": "no_report"}, 0),
    ):
        r = await client.get("/api/v1/events", params=OCTOBER | params)
        assert r.json()["total"] == n, params
    assert (await client.get("/api/v1/events", params={"from": "2026-10-15", "to": "2026-10-31"})).json()[
        "total"
    ] == 0

    async with session_as(client, seed, "owner_b", role="budget_owner", department=w.b) as (owner, _):
        own = await owner.get("/api/v1/events", params=OCTOBER)
        assert {e["department_id"] for e in own.json()["items"]} == {str(w.b)}
        assert (
            await owner.get("/api/v1/events", params=OCTOBER | {"department_id": str(w.a)})
        ).status_code == 403
    async with session_as(client, seed, "fin", role="finance_viewer") as (fin, _):
        assert (await fin.get("/api/v1/events", params=OCTOBER)).status_code == 403


async def test_audit_lists_changes_with_before_after(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    csrf = await admin_client_login(client, seed)
    created = await client.post("/api/v1/departments", json={"name": "Маркетинг"}, headers=csrf)
    dep = created.json()["id"]
    await client.patch(f"/api/v1/departments/{dep}", json={"policy_mode": "warn"}, headers=csrf)
    # config.apply пишет аудит временем БД (now()), а не замороженными часами теста — берём широкий интервал
    resp = await client.get(
        "/api/v1/audit", params={"from": "2026-10-14", "to": "2099-12-31", "object_type": "department"}
    )
    assert resp.status_code == 200, resp.text
    assert_matches_contract(resp, "listAudit")
    items = resp.json()["items"]
    assert [e["object_id"] for e in items] == [dep, dep]
    upd = items[0]  # новые сверху
    assert upd["before"]["policy_mode"] == "observe" and upd["after"]["policy_mode"] == "warn"
    assert upd["actor"] == "root" and upd["at"] >= "2026-10-14"
    other_day = await client.get("/api/v1/audit", params={"from": "2026-10-13", "to": "2026-10-13"})
    assert other_day.json()["total"] == 0
    async with session_as(client, seed, "fin", role="finance_viewer") as (fin, _):
        assert (
            await fin.get("/api/v1/audit", params={"from": "2026-10-14", "to": "2026-10-14"})
        ).status_code == 403


async def test_reconciliation_compares_import_with_events(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    await catalog(seed, only="A")
    dep = await seed.department("Аналитика", allowed=("A",))
    await seed.employee("ivanov", department=dep)
    sid = await db.fetchval(
        "INSERT INTO provider_source(provider, kind, ref, department_id, mode) "
        "VALUES ('anthropic', 'workspace', 'wrkspc_gw', $1, 'decision_api') RETURNING id",
        dep,
    )
    caller_id, key = await seed.caller_key("gateway")
    await db.execute("UPDATE caller_system SET provider_source_id = $1 WHERE id = $2", sid, caller_id)
    d = await decide(client, key, decision_body(model="A"))
    await report(client, key, usage_body(d["decision_id"], 1000, 400))  # 30 ₽
    await import_line(db, "wrkspc_gw", date(2026, 10, 13), 4_775, dep, status="reconcile", orig="0.50")
    await admin_client_login(client, seed)
    resp = await client.get("/api/v1/reports/reconciliation", params={"month": "2026-10"})
    assert resp.status_code == 200
    assert_matches_contract(resp, "getReconciliation")
    (row,) = resp.json()["items"]
    assert (
        row["imported_rub"],
        row["decision_api_rub"],
        row["diff_rub"],
        row["diff_pct"],
        row["coverage_pct"],
    ) == (
        "47.75",
        "30.00",
        "17.75",
        "37.2",
        "62.8",
    )
    empty = await client.get("/api/v1/reports/reconciliation", params={"month": "2026-09"})
    assert empty.json()["items"][0]["imported_rub"] == "0.00" and empty.json()["items"][0]["diff_pct"] is None
    assert (
        await client.get("/api/v1/reports/reconciliation", params={"month": "2026-13"})
    ).status_code == 422
