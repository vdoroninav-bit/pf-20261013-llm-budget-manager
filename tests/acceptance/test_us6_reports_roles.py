"""T070: приёмочные тесты сценария 6 — отчёты, остатки, уведомления и роли (10_tech_spec.md, 6.1–6.6, FR-027–FR-031).

Через API админки под каждой ролью (admin, budget_owner, finance_viewer); ответы проверяются по контракту.
Матрица «роль × операция» (SC-010) — tests/security/test_roles_matrix.py; псевдонимы (6.6) —
tests/acceptance/test_us3_config.py::test_reveal_pseudonym_admin_only_and_audited (T040).

Мир: «Маркетинг» (ivanov) и «Разработка» (petrov), 14.10.2026; расход октября:
- Маркетинг: A — 30,00 ₽ (1 000/400), C — 5,00 ₽ (1 000/400);
- Разработка: A — 70,00 ₽ (2 000/1 000) и импорт Anthropic 13.10 — 2,00 $ × 95,50 = 191,00 ₽.

Написаны до реализации (T071); реализация — T071.
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest

from tests.contract.helpers import assert_matches_contract
from tests.fixtures.imports import cost_line, provider_source, recorded, run_import
from tests.fixtures.qa import (
    admin_client_login,
    catalog,
    decide,
    decision_body,
    notifications,
    one,
    report,
    session_as,
    spend,
    usage_body,
    worker_tick,
)
from tests.fixtures.seed import Emp, Seed

pytestmark = pytest.mark.db

# T071 реализована; оставшиеся точечные xfail — не в коде отчётов (14_dev_backend.md, Backend 2, вопросы техлиду).
SEED_DEFECT = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Дефект засева (QA): сотрудники user1, user2 засеяны после первого решения без bump_config — "
    "снимок конфигурации их не видит (subject_unknown). Проверка CSV-инъекции покрыта "
    "tests/integration/test_reports.py::test_csv_neutralizes_formula_prefixes",
)
# Дефект T061 (3Б6) исправлен в T062: источники пишут аудит со временем часов приложения
# (tests/integration/test_provider_sources_audit.py). 3Б7 закрыт в T073 (Backend 2): все записи аудита —
# по часам приложения (tests/integration/test_audit_clock.py), пометка снята.

CSV_HEADER = "period_start;department;model;source;amount_rub;original_currency;original_amount"
PSEUDONYM = re.compile(r"emp-[0-9a-f]{10}")
OCTOBER = {"from": "2026-10-01", "to": "2026-10-31"}


@dataclass
class World:
    a: UUID
    b: UUID
    ivanov: Emp
    petrov: Emp
    key: str


async def world(app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection) -> World:
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
    await seed.fx(date(2026, 10, 1), "95.5")
    await provider_source(db, "wrkspc_dev", b, "import")
    await run_import(
        app, recorded("anthropic", [cost_line("wrkspc_dev", date(2026, 10, 13), "2.00")]), date(2026, 10, 13)
    )
    return World(a, b, ivanov, petrov, key)


async def forbidden_audit(db: asyncpg.Connection, actor: str) -> int:
    n: int = await db.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'forbidden_access' AND actor = $1", actor
    )
    return n


def money(rows: list[dict[str, Any]], *keys: str) -> dict[tuple[Any, ...], Decimal]:
    out: dict[tuple[Any, ...], Decimal] = {}
    for r in rows:
        k = tuple(r[x] for x in keys)
        out[k] = out.get(k, Decimal(0)) + Decimal(r["amount_rub"])
    return out


# ---------------------------------------------------------------- 6.1 панель владельца бюджета


async def test_6_1_budget_owner_sees_only_own_department(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    w = await world(app, client, seed, db)
    async with session_as(client, seed, "owner_a", role="budget_owner", department=w.a) as (owner, _):
        bal = await owner.get(f"/api/v1/departments/{w.a}/balance")
        assert bal.status_code == 200, bal.text
        assert_matches_contract(bal, "getDepartmentBalance")
        rows = {x["period"]: x for x in bal.json()["limits"]}
        assert set(rows) == {"day", "month"}  # каждый заданный период: лимит, расход, остаток, сброс
        assert rows["month"]["spent"] == {
            "token_rub": "35.00",
            "import_rub": "0.00",
            "subscriptions_rub": "0.00",
        }
        assert rows["month"]["remaining_rub"] == "99965.00"
        assert rows["month"]["resets_at"].startswith("2026-11-01T00:00:00")
        assert rows["day"]["resets_at"].startswith("2026-10-15T00:00:00")

        rep = await owner.get(
            "/api/v1/reports/spend", params=OCTOBER | {"group_by": "department,model,source"}
        )
        assert rep.status_code == 200, rep.text
        assert_matches_contract(rep, "getSpendReport")
        items = rep.json()["items"]
        assert {r["department_id"] for r in items} == {str(w.a)}  # только своё подразделение
        assert money(items, "model_id", "source") == {
            ("A", "token"): Decimal("30.00"),
            ("C", "token"): Decimal("5.00"),
        }
        assert rep.json()["total_rub"] == "35.00"

        items_resp = await owner.get(f"/api/v1/departments/{w.a}/balance/items", params={"period": "month"})
        assert items_resp.status_code == 200, items_resp.text
        assert_matches_contract(items_resp, "listDepartmentBalanceItems")
        assert {x["subject_pseudonym"] for x in items_resp.json()["items"]} == {w.ivanov.pseudonym}
        for text in (bal.text, rep.text, items_resp.text):
            assert "ivanov" not in text and "Иванов" not in text  # сотрудники — только псевдонимами

        events = await owner.get("/api/v1/events", params=OCTOBER)
        assert events.status_code == 200, events.text
        assert_matches_contract(events, "listEvents")
        assert {e["department_id"] for e in events.json()["items"]} == {str(w.a)}
        assert {e["subject_pseudonym"] for e in events.json()["items"]} == {w.ivanov.pseudonym}
        assert w.petrov.pseudonym not in events.text and "ivanov" not in events.text

        status = await owner.get("/api/v1/imports/status")  # отставание импорта на панели (FR-027)
        assert status.status_code == 200


async def test_6_1_owner_request_for_other_department_is_denied_and_audited(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    w = await world(app, client, seed, db)
    async with session_as(client, seed, "owner_a", role="budget_owner", department=w.a) as (owner, _):
        attempts = [
            ("getDepartmentBalance", f"/api/v1/departments/{w.b}/balance", {}),
            ("listDepartmentBalanceItems", f"/api/v1/departments/{w.b}/balance/items", {"period": "month"}),
            ("getSpendReport", "/api/v1/reports/spend", OCTOBER | {"department_id": str(w.b)}),
            ("getSpendCsv", "/api/v1/reports/spend.csv", OCTOBER | {"department_id": str(w.b)}),
            ("listEvents", "/api/v1/events", OCTOBER | {"department_id": str(w.b)}),
        ]
        for op, path, params in attempts:
            before = await forbidden_audit(db, "owner_a")
            resp = await owner.get(path, params=params)
            assert resp.status_code == 403, f"{op}: {resp.status_code} {resp.text[:200]}"
            assert_matches_contract(resp, op)
            assert "Разработка" not in resp.text and "70.00" not in resp.text
            assert await forbidden_audit(db, "owner_a") == before + 1, f"{op}: попытка не записана в аудит"


# ---------------------------------------------------------------- 6.2 CSV финансиста


async def test_6_2_finance_viewer_csv_without_employee_identifiers(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    w = await world(app, client, seed, db)
    async with session_as(client, seed, "fin", role="finance_viewer") as (fin, csrf):
        resp = await fin.get("/api/v1/reports/spend.csv", params=OCTOBER)
        assert resp.status_code == 200, resp.text
        assert_matches_contract(resp, "getSpendCsv")
        assert resp.headers["content-type"].startswith("text/csv")
        raw = resp.content
        assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 с BOM"
        text = raw.decode("utf-8-sig")
        assert text.splitlines()[0] == CSV_HEADER
        rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
        assert money(rows, "department", "model", "source") == {
            ("Маркетинг", "A", "token"): Decimal("30.00"),
            ("Маркетинг", "C", "token"): Decimal("5.00"),
            ("Разработка", "A", "token"): Decimal("70.00"),
            ("Разработка", "claude-sonnet-4-5", "import"): Decimal("191.00"),
        }
        imported = [r for r in rows if r["source"] == "import"]
        assert {(r["original_currency"], Decimal(r["original_amount"])) for r in imported} == {
            ("USD", Decimal("2"))
        }
        assert {r["original_currency"] for r in rows if r["source"] == "token"} == {"RUB"}
        assert all("2026-10-01" <= r["period_start"] <= "2026-10-31" for r in rows)
        for ident in ("ivanov", "petrov", "Иванов", "Петров", w.ivanov.pseudonym, w.petrov.pseudonym):
            assert ident not in text
        assert not PSEUDONYM.search(text)

        items = await fin.get(f"/api/v1/departments/{w.a}/balance/items", params={"period": "month"})
        assert items.status_code == 200
        assert {x["subject_pseudonym"] for x in items.json()["items"]} == {
            None
        }  # финансист сотрудников не видит

        change = await fin.put(
            f"/api/v1/departments/{w.a}/limits",
            json={"day": None, "week": None, "month": "1.00"},
            headers=csrf,
        )
        assert change.status_code == 403  # изменять настройки финансист не может


@SEED_DEFECT
async def test_6_2_csv_injection_is_neutralized(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    await seed.model("-2+3", in_per_1k="10", out_per_1k="50")
    evil = await seed.department('=HYPERLINK("http://x")', allowed=("-2+3",))
    at = await seed.department("@SUM(A1)", allowed=("-2+3",))
    plus = await seed.department("+cmd", allowed=("-2+3",))
    _, key = await seed.caller_key()
    for i, dep in enumerate((evil, at, plus)):
        await seed.employee(f"user{i}", department=dep)
        d = await decide(client, key, decision_body(login=f"user{i}", model="-2+3"))
        await report(client, key, usage_body(d["decision_id"], 1000, 400))
    await admin_client_login(client, seed)
    resp = await client.get("/api/v1/reports/spend.csv", params=OCTOBER)
    assert resp.status_code == 200, resp.text
    rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig")), delimiter=";"))
    assert {r["department"] for r in rows} == {'\'=HYPERLINK("http://x")', "'@SUM(A1)", "'+cmd"}
    assert {r["model"] for r in rows} == {"'-2+3"}


async def test_4_6_unallocated_shown_as_separate_row(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    """Подписка без назначения и несопоставленный импорт — строка «не распределено», ни в одном бюджете."""
    w = await world(app, client, seed, db)
    await run_import(
        app, recorded("anthropic", [cost_line("wrkspc_lost", date(2026, 10, 13), "1.00")]), date(2026, 10, 13)
    )
    csrf = await admin_client_login(client, seed)
    sub = await client.post(
        "/api/v1/subscriptions",
        json={
            "provider": "openai",
            "plan": "Codex",
            "seat_price": "3100",
            "currency": "RUB",
            "seats": 1,
            "start_date": "2026-10-01",
            "assignee": {"type": "unallocated", "ref": None},
        },
        headers=csrf,
    )
    assert sub.status_code == 201, sub.text
    rep = await client.get(
        "/api/v1/reports/spend", params=OCTOBER | {"department_id": "unallocated", "group_by": "source"}
    )
    assert rep.status_code == 200, rep.text
    assert_matches_contract(rep, "getSpendReport")
    by_source = money(rep.json()["items"], "source")
    assert by_source == {
        ("import",): Decimal("95.50"),
        ("subscription",): Decimal("1400.00"),
    }  # 3 100 × 14 / 31
    assert all(r["department_id"] is None for r in rep.json()["items"])
    for dep in (w.a, w.b):
        bal = await client.get(f"/api/v1/departments/{dep}/balance")
        assert {x["spent"]["subscriptions_rub"] for x in bal.json()["limits"]} == {"0.00"}


# ---------------------------------------------------------------- 6.3 пороги 80% и 100%


async def test_6_3_thresholds_80_and_100_notify_once_per_period(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    a = await seed.department("Маркетинг", limits={"month": 10_000})  # 100 ₽
    b = await seed.department("Разработка", limits={"month": 10_000})
    await admin_client_login(client, seed)
    await spend(db, "department", a, 7_900)
    await worker_tick(app)
    assert await notifications(client, "threshold_80") == []  # 79% — ещё нет

    await spend(db, "department", a, 100)  # ровно 80%
    await worker_tick(app)
    await worker_tick(app)
    n80 = one(await notifications(client, "threshold_80"))
    assert (n80["department_id"], n80["period"], n80["period_start"]) == (str(a), "month", "2026-10-01")
    assert "80" in n80["message"] and n80["webhook_status"] == "disabled"  # внешний канал не настроен

    await spend(db, "department", a, 2_500)  # 105%
    await worker_tick(app)
    await worker_tick(app)
    assert len(await notifications(client, "threshold_80")) == 1
    n100 = one(await notifications(client, "threshold_100"))
    assert n100["department_id"] == str(a) and "100" in n100["message"]

    async with session_as(client, seed, "owner_a", role="budget_owner", department=a) as (owner, _):
        kinds = sorted(n["kind"] for n in await notifications(owner))
        assert kinds == ["threshold_100", "threshold_80"]  # владелец бюджета получает оба
    async with session_as(client, seed, "owner_b", role="budget_owner", department=b) as (owner_b, _):
        assert await notifications(owner_b) == []


# ---------------------------------------------------------------- 6.5 аудит изменений


async def test_6_5_audit_records_who_when_before_after(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep = await seed.department("Маркетинг")
    csrf = await admin_client_login(client, seed)
    fin_id = await seed.admin_user("fin", "fin-password-123", role="finance_viewer")

    sub = await client.post(
        "/api/v1/subscriptions",
        json={
            "provider": "anthropic",
            "plan": "Team",
            "seat_price": "2500",
            "currency": "RUB",
            "seats": 3,
            "start_date": "2026-10-01",
            "assignee": {"type": "department", "ref": str(dep)},
        },
        headers=csrf,
    )
    assert sub.status_code == 201, sub.text
    ended = await client.patch(
        f"/api/v1/subscriptions/{sub.json()['id']}", json={"end_date": "2026-10-31"}, headers=csrf
    )
    assert ended.status_code == 200
    src = await client.post(
        "/api/v1/provider-sources",
        json={
            "provider": "anthropic",
            "kind": "workspace",
            "ref": "wrkspc_m",
            "department_id": str(dep),
            "mode": "import",
        },
        headers=csrf,
    )
    assert src.status_code == 201
    moved = await client.patch(
        f"/api/v1/provider-sources/{src.json()['id']}", json={"mode": "decision_api"}, headers=csrf
    )
    assert moved.status_code == 200
    role = await client.patch(
        f"/api/v1/admin-users/{fin_id}",
        json={"role": "budget_owner", "department_id": str(dep)},
        headers=csrf,
    )
    assert role.status_code == 200, role.text

    resp = await client.get(
        "/api/v1/audit", params={"from": "2026-10-14", "to": "2026-10-14", "page_size": 200}
    )
    assert resp.status_code == 200, resp.text
    assert_matches_contract(resp, "listAudit")
    entries = resp.json()["items"]
    by_obj: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_obj.setdefault(str(e["object_id"]), []).append(e)
    for obj_id, expect_update in ((sub.json()["id"], True), (src.json()["id"], True), (str(fin_id), True)):
        got = by_obj.get(obj_id, [])
        assert got, f"нет записей аудита для {obj_id}"
        assert all(e["actor"] == "root" and e["at"].startswith("2026-10-14") for e in got)
        if expect_update:
            upd = [e for e in got if e["before"] is not None]
            assert upd and all(e["before"] != e["after"] for e in upd), f"{obj_id}: нет «было/стало»"
    role_entry = next(e for e in by_obj[str(fin_id)] if e["before"] is not None)
    assert "finance_viewer" in str(role_entry["before"]) and "budget_owner" in str(role_entry["after"])
    total_changes = 5
    assert sum(1 for e in entries if e["actor"] == "root" and e["action"] != "login") >= total_changes


# ---------------------------------------------------------------- журнал событий


async def test_events_journal_shows_codes_and_flags_to_admin(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    """3.6 и 2.3: отказ `model_unknown` и резерв без отчёта видны администратору в журнале."""
    w = await world(app, client, seed, db)
    unknown = await decide(client, w.key, decision_body(model="no-such-model"))
    assert unknown["code"] == "model_unknown"
    await admin_client_login(client, seed)
    resp = await client.get("/api/v1/events", params=OCTOBER | {"code": "model_unknown"})
    assert resp.status_code == 200, resp.text
    assert_matches_contract(resp, "listEvents")
    e = one(resp.json()["items"])
    assert (e["id"], e["outcome"], e["requested_model"]) == (unknown["decision_id"], "deny", "no-such-model")
    assert e["subject_pseudonym"] == w.ivanov.pseudonym and "ivanov" not in resp.text
    all_events = await client.get("/api/v1/events", params=OCTOBER | {"page_size": 200})
    assert all_events.json()["total"] == 4
