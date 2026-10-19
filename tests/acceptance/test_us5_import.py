"""T060: приёмочные тесты сценария 5 — загрузка расхода Anthropic и OpenAI (10_tech_spec.md, 5.1–5.7, FR-021–FR-025).

Конвейер проверяется на записанном источнике `RecordedSource` (без сети), коннекторы — на подменном транспорте
httpx с фикстурами tests/fixtures/{anthropic,openai}/*.synthetic.json через `bm.egress.get_client(transport=…)`.
Договорённости об интерфейсах — tests/fixtures/imports.py. Ожидаемые суммы:
- сутки импорта — сутки UTC провайдера = дата расхода (ADR-004, APPROVAL вопрос 4А);
- ₽ = сумма в USD × курс месяца даты расхода, одно округление ROUND_HALF_UP до копейки;
- курс октября 95,50, ноября 100,00.
Часы: 14.10.2026 12:00 МСК (прошедшие сутки — 13.10).

Написаны до реализации: конвейер и справочник — T061, коннекторы — T062/T063, отчёты — T071.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from bm.clock import FrozenClock
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.imports import (
    ANTHROPIC_KEY,
    OPENAI_KEY,
    FakeProvider,
    cost_line,
    import_ledger,
    import_lines,
    last_run,
    load_pages,
    provider_source,
    recorded,
    run_import,
    totals,
)
from tests.fixtures.qa import (
    admin_client_login,
    balance,
    catalog,
    decide,
    decision_body,
    limit_of,
    msk,
    notifications,
    one,
    report,
    session_as,
    usage_body,
    worker_tick,
)
from tests.fixtures.seed import Seed

pytestmark = [
    pytest.mark.db,
    pytest.mark.xfail(
        strict=True,
        raises=(AssertionError, ImportError),
        reason="T061: конвейер импорта и справочник источников",
    ),
]

OCT12, OCT13 = date(2026, 10, 12), date(2026, 10, 13)
T062 = pytest.mark.xfail(
    strict=True, raises=(AssertionError, ImportError), reason="T062: коннектор Anthropic"
)
T063 = pytest.mark.xfail(strict=True, raises=(AssertionError, ImportError), reason="T063: коннектор OpenAI")
T071 = pytest.mark.xfail(strict=True, raises=(AssertionError, ImportError), reason="T071: отчёты и баланс")


@pytest.fixture
def settings(settings: Any, request: pytest.FixtureRequest) -> Any:
    """Импорт включён, ключи администратора заданы. Параметр (indirect) переопределяет поля."""
    update: dict[str, Any] = {
        "import_enabled": True,
        "anthropic_admin_key": SecretStr(ANTHROPIC_KEY),
        "openai_admin_key": SecretStr(OPENAI_KEY),
    }
    update |= getattr(request, "param", {})
    return settings.model_copy(update=update)


async def world(seed: Seed, db: asyncpg.Connection) -> tuple[UUID, str]:
    """«Аналитика» с месячным лимитом 100 000 ₽, курс октября 95,50; возвращает (подразделение, служебный ключ)."""
    dep = await seed.department("Аналитика", limits={"day": 1_000_000, "month": 10_000_000})
    await seed.fx(date(2026, 10, 1), "95.5")
    _, key = await seed.caller_key("svc", scope_type="service", department=dep)
    return dep, key


# ---------------------------------------------------------------- 5.1 импорт списывается


async def test_5_1_import_charged_to_department_by_expense_date(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, key = await world(seed, db)
    emp = await seed.employee("ivanov", department=dep)
    await provider_source(db, "wrkspc_analytics", dep, "import")
    src = recorded("anthropic", [cost_line("wrkspc_analytics", OCT13, "12.34")])
    await run_import(app, src, OCT13)

    line = one(await import_lines(db))
    assert (line["status"], line["amount_kop"], line["department_id"]) == (
        "charged",
        117_847,
        dep,
    )  # 12,34 × 95,5
    assert (line["currency"], str(line["fx"]), line["day"]) == ("USD", "95.5000", OCT13)
    assert await import_ledger(db, dep, OCT13) == 117_847  # датой фактического расхода
    assert await db.fetchval("SELECT count(*) FROM ledger_daily WHERE owner_type = 'employee'") == 0
    assert emp.id  # импорт идёт только в бюджет подразделения
    run = await last_run(db)
    assert (run["status"], run["lines_charged"], run["error_class"]) == ("ok", 1, None)

    b = await balance(client, key, login=None)
    assert limit_of(b, "department", "month")["spent"]["import_rub"] == "1178.47"
    assert limit_of(b, "department", "day")["spent"]["import_rub"] == "0.00"  # 13.10 — не сегодня
    assert limit_of(b, "department", "month")["remaining_rub"] == "98821.53"


async def test_5_1_ruble_source_is_not_converted(app: Any, seed: Seed, db: asyncpg.Connection) -> None:
    dep, _ = await world(seed, db)
    await provider_source(db, "proj_rub", dep, "import", provider="openai", kind="project")
    await run_import(
        app,
        recorded("openai", [cost_line("proj_rub", OCT13, "10.505", provider="openai", currency="RUB")]),
        OCT13,
    )
    line = one(await import_lines(db))
    assert line["amount_kop"] == 1051  # 10,505 ₽ → 10,51 ₽ (ROUND_HALF_UP)


# ---------------------------------------------------------------- 5.2 Decision API — только сверка


async def test_5_2_decision_api_source_is_not_charged(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, key = await world(seed, db)
    await provider_source(db, "wrkspc_gateway", dep, "decision_api")
    await run_import(app, recorded("anthropic", [cost_line("wrkspc_gateway", OCT13, "0.50")]), OCT13)
    line = one(await import_lines(db))
    assert line["status"] == "reconcile" and line["amount_kop"] == 4775  # сумма для сверки посчитана
    assert await import_ledger(db, dep) == 0  # повторного списания нет
    assert (await last_run(db))["lines_reconcile"] == 1
    b = await balance(client, key, login=None)
    assert limit_of(b, "department", "month")["spent"]["import_rub"] == "0.00"


@T071
async def test_5_2_reconciliation_report_shows_difference(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    """Сверка SC-005: события ключа, связанного с источником, против импорта за месяц (сутки UTC)."""
    await catalog(seed, only="A")
    dep, _ = await world(seed, db)
    await seed.employee("ivanov", department=dep)
    sid = await provider_source(db, "wrkspc_gateway", dep, "decision_api")
    caller_id, key = await seed.caller_key("gateway")
    await db.execute("UPDATE caller_system SET provider_source_id = $1 WHERE id = $2", sid, caller_id)
    await seed.allow(dep, "A")
    clock.set(msk(2026, 10, 13, 12))  # 09:00 UTC 13.10
    d = await decide(client, key, decision_body(model="A"))
    await report(client, key, usage_body(d["decision_id"], 1000, 400))  # 30,00 ₽
    clock.set(msk(2026, 10, 14, 12))
    await run_import(
        app, recorded("anthropic", [cost_line("wrkspc_gateway", OCT13, "0.50")]), OCT13
    )  # 47,75 ₽

    await admin_client_login(client, seed)
    resp = await client.get("/api/v1/reports/reconciliation", params={"month": "2026-10"})
    assert resp.status_code == 200, resp.text
    assert_matches_contract(resp, "getReconciliation")
    row = one([r for r in resp.json()["items"] if r["source_ref"] == "wrkspc_gateway"])
    assert (row["provider"], row["imported_rub"], row["decision_api_rub"]) == ("anthropic", "47.75", "30.00")
    assert row["diff_rub"] in ("17.75", "-17.75") and row["department_id"] == str(dep)


# ---------------------------------------------------------------- 5.3 несопоставленный источник


async def test_5_3_unmapped_source_goes_to_unallocated_with_notification(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, _ = await world(seed, db)
    await provider_source(db, "wrkspc_analytics", dep, "import")
    lines = [cost_line("wrkspc_analytics", OCT13, "1.00"), cost_line("wrkspc_unknown", OCT13, "2.00")]
    await run_import(app, recorded("anthropic", lines), OCT13)

    by_ref = {r["source_ref"]: r for r in await import_lines(db)}
    assert by_ref["wrkspc_unknown"]["status"] == "unallocated"
    assert by_ref["wrkspc_unknown"]["department_id"] is None
    assert await import_ledger(db, dep) == 9550  # только сопоставленная строка
    assert await db.fetchval("SELECT count(*) FROM ledger_daily WHERE source = 'import'") == 1
    assert (await last_run(db))["lines_unallocated"] == 1

    await admin_client_login(client, seed)
    n = one(await notifications(client, "import_unallocated"))
    assert n["department_id"] is None and n["period"] is None and n["period_start"] == "2026-10-14"
    assert "wrkspc_unknown" in n["message"]
    await run_import(app, recorded("anthropic", lines), OCT13)
    assert (
        len(await notifications(client, "import_unallocated")) == 1
    )  # не чаще раза в сутки (14_tech_rulings Б5)

    status = await client.get("/api/v1/imports/status")
    assert status.status_code == 200
    assert_matches_contract(status, "getImportStatus")
    anthropic = next(p for p in status.json()["providers"] if p["provider"] == "anthropic")
    assert anthropic["unallocated_sources"] == ["wrkspc_unknown"]
    async with session_as(client, seed, "owner", role="budget_owner", department=dep) as (owner, _):
        resp = await owner.get("/api/v1/imports/status")
        assert resp.status_code == 200
        assert all(
            p["unallocated_sources"] == [] and p["last_error_class"] is None for p in resp.json()["providers"]
        )


# ---------------------------------------------------------------- 5.4 повтор и уточнение


async def test_5_4_rerun_without_changes_does_not_charge_twice(
    app: Any, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, _ = await world(seed, db)
    await provider_source(db, "wrkspc_analytics", dep, "import")
    src = recorded("anthropic", [cost_line("wrkspc_analytics", OCT13, "12.34")])
    await run_import(app, src, OCT13)
    await run_import(app, src, OCT13)
    assert await import_ledger(db, dep) == 117_847
    line = one(await import_lines(db))
    assert line["version"] == 1
    assert (await last_run(db))["lines_changed"] == 0


async def test_5_4_refined_amount_replaces_previous_value(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, key = await world(seed, db)
    await provider_source(db, "wrkspc_analytics", dep, "import")
    await run_import(app, recorded("anthropic", [cost_line("wrkspc_analytics", OCT12, "12.34")]), OCT12)
    # провайдер уточнил сумму за 12.10; загрузка окна 12.10–13.10 (IMPORT_WINDOW_DAYS)
    refined = [cost_line("wrkspc_analytics", OCT12, "15.00"), cost_line("wrkspc_analytics", OCT13, "1.00")]
    await run_import(app, recorded("anthropic", refined), OCT12, OCT13)
    assert await import_ledger(db, dep, OCT12) == 143_250  # заменено (15 × 95,5), а не 117 847 + 143 250
    assert await import_ledger(db, dep, OCT13) == 9550
    by_day = {r["day"]: r for r in await import_lines(db)}
    assert (by_day[OCT12]["version"], by_day[OCT13]["version"]) == (2, 1)
    assert (await last_run(db))["lines_changed"] == 1
    b = await balance(client, key, login=None)
    assert limit_of(b, "department", "month")["spent"]["import_rub"] == "1528.00"


async def test_5_4_source_mode_change_recalculates_on_next_load(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    """ADR-004: сменили способ учёта — при следующей загрузке статус строки пересчитан, в реестр ушла дельта."""
    dep, _ = await world(seed, db)
    csrf = await admin_client_login(client, seed)
    created = await client.post(
        "/api/v1/provider-sources",
        json={
            "provider": "anthropic",
            "kind": "workspace",
            "ref": "wrkspc_analytics",
            "department_id": str(dep),
            "mode": "import",
        },
        headers=csrf,
    )
    assert created.status_code == 201, created.text
    src = recorded("anthropic", [cost_line("wrkspc_analytics", OCT13, "12.34")])
    await run_import(app, src, OCT13)
    assert await import_ledger(db, dep) == 117_847

    resp = await client.patch(
        f"/api/v1/provider-sources/{created.json()['id']}", json={"mode": "decision_api"}, headers=csrf
    )
    assert resp.status_code == 200 and resp.json()["mode"] == "decision_api"
    assert_matches_contract(resp, "updateProviderSource")
    await run_import(app, src, OCT13)
    line = one(await import_lines(db))
    assert line["status"] == "reconcile" and await import_ledger(db, dep) == 0

    resp = await client.delete(f"/api/v1/provider-sources/{created.json()['id']}", headers=csrf)
    assert resp.status_code == 204
    assert_matches_contract(resp, "deleteProviderSource")
    await run_import(app, src, OCT13)
    line = one(await import_lines(db))
    assert line["status"] == "unallocated" and await import_ledger(db, dep) == 0


# ---------------------------------------------------------------- 5.5 сбои загрузки


async def test_5_5_two_failures_in_a_row_notify_admin_once(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    await catalog(seed, only="A")
    dep, _ = await world(seed, db)
    await seed.allow(dep, "A")
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key("gateway")
    await admin_client_login(client, seed)
    broken = recorded("anthropic", error_class="provider_unavailable")

    await run_import(app, broken, OCT13)
    run = await last_run(db)
    assert (run["status"], run["error_class"]) == ("failed", "provider_unavailable")
    assert await notifications(client, "import_failure") == []  # после одной неудачи — ещё нет
    assert (await decide(client, key, decision_body(model="A")))["outcome"] == "allow"  # решения работают

    await run_import(app, broken, OCT13)
    n = one(await notifications(client, "import_failure"))
    assert n["department_id"] is None and n["period_start"] == "2026-10-14"
    assert "Anthropic" in n["message"] or "anthropic" in n["message"]
    await run_import(app, broken, OCT13)
    assert len(await notifications(client, "import_failure")) == 1

    status = await client.get("/api/v1/imports/status")
    assert status.status_code == 200
    assert_matches_contract(status, "getImportStatus")
    st = next(p for p in status.json()["providers"] if p["provider"] == "anthropic")
    assert (st["consecutive_failures"], st["last_error_class"], st["last_success_day"]) == (
        3,
        "provider_unavailable",
        None,
    )

    await run_import(app, recorded("anthropic", []), OCT13)
    status = await client.get("/api/v1/imports/status")
    st = next(p for p in status.json()["providers"] if p["provider"] == "anthropic")
    assert st["consecutive_failures"] == 0 and st["last_success_day"] == "2026-10-13"
    assert isinstance(st["lag_hours"], int) and st["lag_hours"] >= 0 and st["last_run_at"] is not None


async def test_5_5_auth_rejected_is_classified(app: Any, seed: Seed, db: asyncpg.Connection) -> None:
    await world(seed, db)
    await run_import(app, recorded("anthropic", error_class="auth_rejected"), OCT13)
    run = await last_run(db)
    assert (run["status"], run["error_class"]) == ("failed", "auth_rejected")
    assert await db.fetchval("SELECT count(*) FROM import_line") == 0


async def test_runs_are_listed_and_manual_run_is_queued(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, _ = await world(seed, db)
    await provider_source(db, "wrkspc_analytics", dep, "import")
    await run_import(app, recorded("anthropic", [cost_line("wrkspc_analytics", OCT13, "1.00")]), OCT13)
    csrf = await admin_client_login(client, seed)
    runs = await client.get("/api/v1/imports/runs", params={"provider": "anthropic"})
    assert runs.status_code == 200
    assert_matches_contract(runs, "listImportRuns")
    r = one(runs.json()["items"])
    assert (r["status"], r["day_from"], r["day_to"], r["lines_charged"]) == (
        "ok",
        "2026-10-13",
        "2026-10-13",
        1,
    )

    queued = await client.post("/api/v1/imports/run", json={"provider": "anthropic"}, headers=csrf)
    assert queued.status_code == 202, queued.text
    assert_matches_contract(queued, "runImport")
    assert queued.json()["status"] == "queued"


# ---------------------------------------------------------------- 5.6 импорт выключен


@pytest.mark.parametrize("settings", [{"import_enabled": False}], indirect=True)
async def test_5_6_import_disabled_makes_no_connections(
    app: Any,
    client: httpx.AsyncClient,
    seed: Seed,
    db: asyncpg.Connection,
    clock: FrozenClock,
    egress_guard: list[Any],
) -> None:
    dep, _ = await world(seed, db)
    await provider_source(db, "wrkspc_analytics", dep, "import")
    clock.set(msk(2026, 10, 14, 4, 0))  # IMPORT_HOUR_MSK
    await worker_tick(app)
    clock.set(msk(2026, 10, 14, 5, 0))
    await worker_tick(app)
    assert egress_guard == []  # ни одной попытки соединения
    assert await db.fetchval("SELECT count(*) FROM import_run WHERE status IN ('running', 'ok')") == 0

    csrf = await admin_client_login(client, seed)
    resp = await client.post("/api/v1/imports/run", json={"provider": "anthropic"}, headers=csrf)
    assert resp.status_code == 409 and resp.json()["code"] == "import_disabled"
    assert_matches_contract(resp, "runImport")
    status = await client.get("/api/v1/imports/status")
    assert status.status_code == 200 and status.json()["enabled"] is False
    assert egress_guard == []


@pytest.mark.parametrize("settings", [{"openai_admin_key": None}], indirect=True)
async def test_5_6_run_without_provider_key_is_conflict(client: httpx.AsyncClient, seed: Seed) -> None:
    csrf = await admin_client_login(client, seed)
    resp = await client.post("/api/v1/imports/run", json={"provider": "openai"}, headers=csrf)
    assert resp.status_code == 409 and resp.json()["code"] == "provider_key_missing"
    status = await client.get("/api/v1/imports/status")
    assert_matches_contract(status, "getImportStatus")
    configured = {p["provider"]: p["configured"] for p in status.json()["providers"]}
    assert configured == {"anthropic": True, "openai": False}
    assert ANTHROPIC_KEY not in status.text


# ---------------------------------------------------------------- 5.7 поздний импорт


async def test_5_7_late_import_belongs_to_previous_month(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    dep, key = await world(seed, db)
    await seed.fx(date(2026, 11, 1), "100")
    await provider_source(db, "wrkspc_analytics", dep, "import")
    clock.set(msk(2026, 11, 2, 10))
    await run_import(
        app,
        recorded("anthropic", [cost_line("wrkspc_analytics", date(2026, 10, 31), "2.00")]),
        date(2026, 10, 31),
    )
    assert await import_ledger(db, dep, date(2026, 10, 31)) == 19_100  # курс октября, дата — 31.10
    b = await balance(client, key, login=None)
    assert limit_of(b, "department", "month")["spent"]["import_rub"] == "0.00"  # текущий месяц не тронут
    assert limit_of(b, "department", "month")["remaining_rub"] == "100000.00"


@T071
async def test_5_7_late_import_shown_in_previous_month_report(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    dep, _ = await world(seed, db)
    await db.execute("UPDATE limit_def SET amount_kop = 1000 WHERE owner_id = $1 AND period = 'month'", dep)
    await provider_source(db, "wrkspc_analytics", dep, "import")
    clock.set(msk(2026, 11, 2, 10))
    await run_import(
        app,
        recorded("anthropic", [cost_line("wrkspc_analytics", date(2026, 10, 31), "2.00")]),
        date(2026, 10, 31),
    )
    await admin_client_login(client, seed)
    rep = await client.get(
        "/api/v1/reports/spend",
        params={
            "from": "2026-10-01",
            "to": "2026-10-31",
            "group_by": "department,source",
            "department_id": str(dep),
        },
    )
    assert rep.status_code == 200, rep.text
    assert_matches_contract(rep, "getSpendReport")
    row = one([r for r in rep.json()["items"] if r["source"] == "import"])
    assert (row["amount_rub"], row["late"]) == ("191.00", True)
    bal = await client.get(
        f"/api/v1/departments/{dep}/balance", params={"at": msk(2026, 10, 31, 23, 59).isoformat()}
    )
    assert bal.status_code == 200, bal.text
    october = limit_of(bal.json(), "department", "month")
    assert (
        october["remaining_rub"] == "-181.00" and october["over_limit"] is True
    )  # перерасход прошлого периода


# ---------------------------------------------------------------- справочник источников (API)


async def test_provider_source_rules(client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection) -> None:
    dep = await seed.department("Аналитика")
    csrf = await admin_client_login(client, seed)
    body = {
        "provider": "anthropic",
        "kind": "workspace",
        "ref": "wrkspc_analytics",
        "department_id": str(dep),
        "mode": "import",
        "display_name": "Аналитика — Claude",
    }
    created = await client.post("/api/v1/provider-sources", json=body, headers=csrf)
    assert created.status_code == 201, created.text
    assert_matches_contract(created, "createProviderSource")

    dup = await client.post("/api/v1/provider-sources", json=body | {"mode": "decision_api"}, headers=csrf)
    assert dup.status_code == 409 and dup.json()["code"] == "source_exists"  # ровно один способ учёта
    assert_matches_contract(dup, "createProviderSource")

    key_src = await client.post(
        "/api/v1/provider-sources", json=body | {"kind": "api_key", "ref": "apikey_01"}, headers=csrf
    )
    assert key_src.status_code == 422 and key_src.json()["code"] == "unsupported_source_kind"
    assert_matches_contract(key_src, "createProviderSource")
    openai_key = await client.post(
        "/api/v1/provider-sources",
        json=body | {"provider": "openai", "kind": "api_key", "ref": "key_01"},
        headers=csrf,
    )
    assert openai_key.status_code == 201  # у OpenAI источник «ключ» допустим

    listing = await client.get("/api/v1/provider-sources")
    assert listing.status_code == 200 and listing.json()["total"] == 2
    assert_matches_contract(listing, "listProviderSources")


# ---------------------------------------------------------------- коннекторы на подменном транспорте


@T062
async def test_anthropic_connector_reads_cost_report_pages(settings: Any, egress_guard: list[Any]) -> None:
    from bm.provider_import.sources.anthropic import AnthropicSource

    fake = FakeProvider(
        load_pages("anthropic", "cost_report_page1.synthetic.json", "cost_report_page2.synthetic.json")
    )
    lines = await AnthropicSource(settings, transport=fake.transport).fetch(OCT12, OCT13)

    assert len(fake.requests) == 2  # пагинация: вторая страница по next_page
    first = fake.requests[0]
    assert (first.url.host, first.url.path) == ("api.anthropic.com", "/v1/organizations/cost_report")
    assert first.headers["x-api-key"] == ANTHROPIC_KEY and "anthropic-version" in first.headers
    query = first.url.query.decode()
    assert "bucket_width=1d" in query and "workspace_id" in query and "description" in query
    assert fake.requests[1].url.params.get("page") == "page_qa_2"

    assert {(x.provider, x.source_kind, x.currency) for x in lines} == {("anthropic", "workspace", "USD")}
    by_ref_day = totals(lines, lambda x: (x.source_ref, x.day))
    # amount в центах [Гипотеза, tests/fixtures/anthropic/README.md]: 500 → 5,00 $; 1234,5 + 765,5 → 20,00 $
    assert by_ref_day == {
        ("wrkspc_analytics", OCT12): 5,
        ("wrkspc_analytics", OCT13): 20,
        ("wrkspc_marketing", OCT13): 3,
    }
    assert (
        len({x.model for x in lines if x.source_ref == "wrkspc_analytics"}) == 1
    )  # одна модель — вход+выход
    assert {x.model for x in lines if x.source_ref == "wrkspc_marketing"} != {
        x.model for x in lines if x.source_ref == "wrkspc_analytics"
    }
    assert egress_guard == []


@T062
@pytest.mark.parametrize(("status", "error_class"), [(401, "auth_rejected"), (503, "provider_unavailable")])
async def test_anthropic_connector_errors_are_classified(
    app: Any, settings: Any, seed: Seed, db: asyncpg.Connection, status: int, error_class: str
) -> None:
    from bm.provider_import.sources.anthropic import AnthropicSource

    fake = FakeProvider([], status=status)
    await run_import(app, AnthropicSource(settings, transport=fake.transport), OCT13)
    run = await last_run(db)
    assert (run["status"], run["error_class"]) == ("failed", error_class)


@T062
async def test_anthropic_connector_bad_response(app: Any, settings: Any, db: asyncpg.Connection) -> None:
    from bm.provider_import.sources.anthropic import AnthropicSource

    fake = FakeProvider([], raw=b'{"unexpected": true}')
    await run_import(app, AnthropicSource(settings, transport=fake.transport), OCT13)
    assert (await last_run(db))["error_class"] == "bad_response"


@T062
@pytest.mark.parametrize("settings", [{"import_enabled": False}], indirect=True)
async def test_anthropic_connector_disabled_flag_no_connection(
    settings: Any, egress_guard: list[Any]
) -> None:
    from bm.egress.client import EgressDisabled
    from bm.provider_import.sources.anthropic import AnthropicSource

    fake = FakeProvider([])
    with pytest.raises(EgressDisabled):
        await AnthropicSource(settings, transport=fake.transport).fetch(OCT13, OCT13)
    assert fake.requests == [] and egress_guard == []


@T063
async def test_openai_connector_reads_costs_pages(settings: Any, egress_guard: list[Any]) -> None:
    from bm.provider_import.sources.openai import OpenAISource

    fake = FakeProvider(load_pages("openai", "costs_page1.synthetic.json", "costs_page2.synthetic.json"))
    lines = await OpenAISource(settings, transport=fake.transport).fetch(OCT12, OCT13)

    assert len(fake.requests) == 2
    first = fake.requests[0]
    assert (first.url.host, first.url.path) == ("api.openai.com", "/v1/organization/costs")
    assert first.headers["authorization"] == f"Bearer {OPENAI_KEY}"
    params = first.url.params
    assert params["bucket_width"] == "1d"
    assert params["start_time"] == str(int(datetime(2026, 10, 12, tzinfo=UTC).timestamp()))
    query = first.url.query.decode()
    assert "project_id" in query and "line_item" in query  # group_by (формат массива — как у провайдера)
    assert fake.requests[1].url.params.get("page") == "page_qa_2"

    assert {(x.provider, x.source_kind, x.currency) for x in lines} == {("openai", "project", "USD")}
    # amount.value в JSON — число; в CostLine — Decimal без двоичных «хвостов» (принцип VI): 0.12, а не 0.1199…
    assert totals(lines, lambda x: (x.source_ref, x.day)) == {
        ("proj_analytics", OCT12): Decimal("1"),
        ("proj_analytics", OCT13): Decimal("3.75"),
        ("proj_marketing", OCT13): Decimal("0.12"),
    }
    assert (
        len({x.model for x in lines if x.source_ref == "proj_analytics"}) == 1
    )  # input/output — одна модель
    assert egress_guard == []
