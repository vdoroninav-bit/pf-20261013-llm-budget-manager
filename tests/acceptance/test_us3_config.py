"""T040: приёмочные тесты сценария 3 — оргструктура, бюджеты, каталог (10_tech_spec.md, 3.1–3.6).

Через API админки (cookie-сессия + X-CSRF-Token), ответы проверяются по контракту:
- 3.1/3.2 загрузка AD-файла (tests/fixtures/ad_groups_ok.csv, ad_groups_errors.csv), dry_run, основная группа;
- 3.3 лимиты 5 000 / 20 000 / 60 000 ₽ — в решении действуют все три;
- 3.4 модель в каталоге → доступна подразделению; 3.5 цена с 15-го — старые события не меняются;
- USD-цена без курса → 422 fx_rate_missing; аудит «было/стало» для каждого изменения;
- test_removed_employee_keeps_period_spend (12_plan.md §9 п. 5); ключ показывается один раз, отзыв — сразу.
Сценарий 3.6 (model_unknown) — tests/acceptance/test_edge_cases.py::test_model_unknown (T020).

Нумерация строк в сводке импорта: строка файла, заголовок — строка 1.
Написаны до реализации (T041, T042). После T041/T042 пометка xfail снята с модуля и оставлена только
у `test_removed_employee_keeps_period_spend` — ждёт `getDepartmentBalance` (T071). В
`test_3_5_new_price_from_15th_old_events_unchanged` после `clock.set(15-е 10:00)` выполняется повторный вход:
сессия админки живёт 12 ч.
"""

import hashlib
import re
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.qa import (
    ADMIN_PASSWORD,
    CYRILLIC,
    admin_client_login,
    balance,
    call_record,
    catalog,
    decide,
    decision_body,
    key_headers,
    limit_of,
    msk,
    post_decision,
    report,
    spend,
    usage_body,
)
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
AD_OK = (FIXTURES / "ad_groups_ok.csv").read_bytes()
AD_ERRORS = (FIXTURES / "ad_groups_errors.csv").read_bytes()
PSEUDONYM = re.compile(r"^emp-[0-9a-f]{10}$")
HEADER = "login;display_name;group;is_primary\n"


@pytest.fixture
async def admin(client: httpx.AsyncClient, seed: Seed) -> dict[str, str]:
    """Вход администратором; возвращает заголовок CSRF."""
    return await admin_client_login(client, seed, "root")


async def import_org(
    client: httpx.AsyncClient, csrf: dict[str, str], data: bytes, *, dry_run: bool = False
) -> dict[str, Any]:
    resp = await client.post(
        "/api/v1/org/import",
        files={"file": ("ad.csv", data, "text/csv")},
        data={"dry_run": "true" if dry_run else "false"},
        headers=csrf,
    )
    assert resp.status_code == 200, f"importOrg: {resp.status_code} {resp.text[:300]}"
    assert_matches_contract(resp, "importOrg")
    body: dict[str, Any] = resp.json()
    return body


async def dept_id(db: asyncpg.Connection, name: str) -> UUID:
    v = await db.fetchval("SELECT id FROM department WHERE name = $1", name)
    assert v is not None, f"нет подразделения {name}"
    return UUID(str(v))


async def current_dept(db: asyncpg.Connection, login: str) -> str | None:
    name: str | None = await db.fetchval(
        "SELECT d.name FROM employee e JOIN employee_assignment a ON a.employee_id = e.id AND a.valid_to IS NULL "
        "JOIN department d ON d.id = a.department_id WHERE e.ad_login = $1",
        login,
    )
    return name


async def audit_count(db: asyncpg.Connection) -> int:
    n: int = await db.fetchval("SELECT count(*) FROM audit_log")
    return n


async def config_version(db: asyncpg.Connection) -> int:
    v: int = await db.fetchval("SELECT version FROM config_version")
    return v


# ---------------------------------------------------------------- 3.1 / 3.2 загрузка AD-файла


async def test_3_1_import_creates_departments_and_employees(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection
) -> None:
    s = await import_org(client, admin, AD_OK)
    assert s["dry_run"] is False
    assert (s["rows_total"], s["rows_applied"]) == (7, 7)
    assert (s["departments_added"], s["employees_added"]) == (3, 5)
    assert (s["employees_updated"], s["employees_moved"], s["employees_removed"]) == (0, 0, 0)
    assert s["errors"] == []
    names = {r["name"] for r in await db.fetch("SELECT name FROM department")}
    assert names == {"Маркетинг", "Разработка", "Аналитика"}

    resp = await client.get("/api/v1/employees", params={"page_size": 200})
    assert resp.status_code == 200
    assert_matches_contract(resp, "listEmployees")
    items = resp.json()["items"]
    assert resp.json()["total"] == 5 and all(PSEUDONYM.match(x["pseudonym"]) for x in items)
    assert "ivanov" not in resp.text and "Иванов" not in resp.text  # сотрудники — только псевдонимами


async def test_3_2_primary_group_and_warning_without_mark(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection
) -> None:
    s = await import_org(client, admin, AD_OK)
    assert await current_dept(db, "sidorov") == "Разработка"  # отмечена основной
    assert await current_dept(db, "kuznetsova") == "Разработка"  # отметки нет — первая по порядку
    assert len(s["warnings"]) == 1
    warning = s["warnings"][0]
    assert warning["line"] in (6, 7) and CYRILLIC.search(warning["message"])


async def test_3_1_rows_with_errors_skipped_others_applied(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection
) -> None:
    s = await import_org(client, admin, AD_ERRORS)
    assert (s["rows_total"], s["rows_applied"]) == (5, 2)
    assert sorted(e["line"] for e in s["errors"]) == [3, 4, 5]
    assert all(CYRILLIC.search(e["message"]) for e in s["errors"])
    assert s["employees_added"] == 2 and s["departments_added"] == 2
    logins = {r["ad_login"] for r in await db.fetch("SELECT ad_login FROM employee")}
    assert logins == {"ivanov", "orlov"}


async def test_dry_run_changes_nothing(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection
) -> None:
    v0 = await config_version(db)
    s = await import_org(client, admin, AD_OK, dry_run=True)
    assert s["dry_run"] is True
    assert (s["departments_added"], s["employees_added"], s["rows_applied"]) == (3, 5, 7)
    assert await db.fetchval("SELECT count(*) FROM department") == 0
    assert await db.fetchval("SELECT count(*) FROM employee") == 0
    assert await config_version(db) == v0


async def test_reimport_moves_removes_and_keeps_pseudonyms(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection
) -> None:
    await import_org(client, admin, AD_OK)
    before = {
        r["ad_login"]: r["pseudonym"] for r in await db.fetch("SELECT ad_login, pseudonym FROM employee")
    }
    second = (
        HEADER
        + "ivanov;Иванов Иван;Маркетинг;1\n"
        + "sidorov;Сидоров Сидор;Маркетинг;1\n"  # переведён в Маркетинг
        + "kuznetsova;Кузнецова Анна;Разработка;1\n"
        + "smirnov;Смирнов Олег;Аналитика;1\n"
    ).encode()  # petrov в файле нет — выводится из подразделения
    s = await import_org(client, admin, second)
    assert (s["employees_moved"], s["employees_removed"], s["employees_added"]) == (1, 1, 0)
    assert await current_dept(db, "sidorov") == "Маркетинг"
    assert await current_dept(db, "petrov") is None
    after = {
        r["ad_login"]: r["pseudonym"] for r in await db.fetch("SELECT ad_login, pseudonym FROM employee")
    }
    assert after == before  # псевдоним выдаётся один раз


async def test_import_file_too_large_is_413(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection
) -> None:
    big = HEADER.encode() + b"x" * (10 * 1024 * 1024 + 1)
    resp = await client.post("/api/v1/org/import", files={"file": ("ad.csv", big, "text/csv")}, headers=admin)
    assert resp.status_code == 413 and resp.json()["code"] == "file_too_large"
    assert_matches_contract(resp, "importOrg")
    assert await db.fetchval("SELECT count(*) FROM department") == 0


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="T071: getDepartmentBalance (/departments/{id}/balance) ещё не реализована",
)
async def test_removed_employee_keeps_period_spend(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection, seed: Seed
) -> None:
    """12_plan.md §9 п. 5: назначение закрывается, резерв закрывается по отчёту, личный лимит и расход за период
    сохраняются; подразделение сохраняет расход сотрудника до перевода."""
    await catalog(seed, only="A")
    await import_org(client, admin, AD_OK)
    mkt = await dept_id(db, "Маркетинг")
    resp = await client.patch(
        f"/api/v1/departments/{mkt}", json={"policy_mode": "enforce", "allowed_models": ["A"]}, headers=admin
    )
    assert resp.status_code == 200
    await client.put(
        f"/api/v1/departments/{mkt}/limits",
        json={"day": None, "week": None, "month": "100000.00"},
        headers=admin,
    )
    petrov = await db.fetchval("SELECT pseudonym FROM employee WHERE ad_login = 'petrov'")
    resp = await client.put(
        f"/api/v1/employees/{petrov}/limits",
        json={"day": None, "week": None, "month": "1000.00"},
        headers=admin,
    )
    assert resp.status_code == 200
    _, key = await seed.caller_key()

    settled = await decide(client, key, decision_body(login="petrov", model="A"))
    await report(client, key, usage_body(settled["decision_id"], 1000, 400))  # 30 ₽
    open_ = await decide(client, key, decision_body(login="petrov", model="A"))  # резерв 30 ₽ открыт

    without_petrov = AD_OK.decode().replace("petrov;Петров Пётр;Маркетинг;\n", "").encode()
    s = await import_org(client, admin, without_petrov)
    assert s["employees_removed"] == 1
    assert (await decide(client, key, decision_body(login="petrov")))["code"] == "subject_unknown"
    # открытый резерв закрывается отчётом и списывается
    assert (await call_record(db, open_["decision_id"]))["status"] == "open"
    r = await report(client, key, usage_body(open_["decision_id"], 1000, 400))
    assert r["status"] == "charged" and r["amount_rub"] == "30.00"
    # расход остаётся у подразделения
    resp = await client.get(f"/api/v1/departments/{mkt}/balance")
    assert resp.status_code == 200
    assert_matches_contract(resp, "getDepartmentBalance")
    assert limit_of(resp.json(), "department", "month")["spent"]["token_rub"] == "60.00"
    # сотрудник вернулся — личный лимит и расход за период на месте
    await import_org(client, admin, AD_OK)
    emp = await client.get(f"/api/v1/employees/{petrov}")
    assert emp.status_code == 200 and emp.json()["limits"]["month"] == "1000.00"
    personal = limit_of(await balance(client, key, "petrov"), "employee", "month")
    assert (personal["spent"]["token_rub"], personal["remaining_rub"]) == ("60.00", "940.00")


# ---------------------------------------------------------------- 3.3 лимиты


async def _enforced_department(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed, name: str = "Маркетинг"
) -> UUID:
    resp = await client.post("/api/v1/departments", json={"name": name}, headers=admin)
    assert resp.status_code == 201, resp.text
    assert_matches_contract(resp, "createDepartment")
    dep = UUID(resp.json()["id"])
    resp = await client.patch(
        f"/api/v1/departments/{dep}", json={"policy_mode": "enforce", "allowed_models": ["A"]}, headers=admin
    )
    assert resp.status_code == 200
    assert_matches_contract(resp, "updateDepartment")
    return dep


async def test_new_department_defaults_to_observe(client: httpx.AsyncClient, admin: dict[str, str]) -> None:
    resp = await client.post("/api/v1/departments", json={"name": "Сервисы"}, headers=admin)
    assert resp.status_code == 201
    assert_matches_contract(resp, "createDepartment")
    body = resp.json()
    assert body["policy_mode"] == "observe" and body["fail_mode"] == "open"
    assert body["allowed_models"] == [] and body["limits"] == {"day": None, "week": None, "month": None}


SPEND_FOR_PERIOD = {
    # период → (сутки, сумма): исчерпывает только этот период до остатка 20 ₽
    "day": (msk(2026, 10, 14).date(), 498_000),
    "week": (msk(2026, 10, 12).date(), 1_998_000),
    "month": (msk(2026, 10, 1).date(), 5_998_000),
}


@pytest.mark.parametrize("period", list(SPEND_FOR_PERIOD))
async def test_3_3_three_limits_all_enforced(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed, db: asyncpg.Connection, period: str
) -> None:
    await catalog(seed, only="A")
    dep = await _enforced_department(client, admin, seed)
    limits = {"day": "5000.00", "week": "20000.00", "month": "60000.00"}
    resp = await client.put(f"/api/v1/departments/{dep}/limits", json=limits, headers=admin)
    assert resp.status_code == 200 and resp.json() == limits
    assert_matches_contract(resp, "putDepartmentLimits")
    got = await client.get(f"/api/v1/departments/{dep}")
    assert got.json()["limits"] == limits
    await seed.employee("ivanov", department=dep)
    day, kop = SPEND_FOR_PERIOD[period]
    await spend(db, "department", dep, kop, day=day)
    _, key = await seed.caller_key()
    d = await decide(client, key, decision_body(model="A"))  # 30 ₽ > 20 ₽ остатка
    assert d["outcome"] == "deny" and d["code"] == "budget_exceeded"
    assert d["applied_limit"]["period"] == period and d["applied_limit"]["remaining_rub"] == "20.00"


async def test_3_3_null_removes_limit(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed, db: asyncpg.Connection
) -> None:
    await catalog(seed, only="A")
    dep = await _enforced_department(client, admin, seed)
    await client.put(
        f"/api/v1/departments/{dep}/limits", json={"day": "10.00", "week": None, "month": None}, headers=admin
    )
    resp = await client.put(
        f"/api/v1/departments/{dep}/limits", json={"day": None, "week": None, "month": None}, headers=admin
    )
    assert resp.status_code == 200
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key()
    d = await decide(client, key, decision_body(model="A"))
    assert d["outcome"] == "allow"  # лимитов нет — ограничения нет


# ---------------------------------------------------------------- 3.4 / 3.5 каталог и цены


MODEL_X = {
    "id": "gigachat-pro",
    "provider": "gigachat",
    "kind": "chat",
    "supports_tools": True,
    "supports_json": True,
    "max_context": 32000,
    "default_input_tokens": 1000,
    "default_output_tokens": 400,
    "price": {"input_per_1k": "10", "output_per_1k": "50", "currency": "RUB"},
}


async def test_3_4_model_added_and_assignable(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed
) -> None:
    resp = await client.post("/api/v1/models", json=MODEL_X, headers=admin)
    assert resp.status_code == 201, resp.text
    assert_matches_contract(resp, "createModel")
    assert resp.json()["current_price"]["input_per_1k"] in ("10", "10.0", "10.00", "10.000000")
    dup = await client.post("/api/v1/models", json=MODEL_X, headers=admin)
    assert dup.status_code == 409
    resp = await client.post("/api/v1/departments", json={"name": "Маркетинг"}, headers=admin)
    assert resp.status_code == 201, resp.text
    dep = resp.json()["id"]
    resp = await client.patch(
        f"/api/v1/departments/{dep}",
        json={"policy_mode": "enforce", "allowed_models": ["gigachat-pro"]},
        headers=admin,
    )
    assert resp.status_code == 200 and resp.json()["allowed_models"] == ["gigachat-pro"]
    await seed.employee("ivanov", department=UUID(dep))
    _, key = await seed.caller_key()
    d = await decide(client, key, decision_body(model="gigachat-pro"))
    assert d["outcome"] == "allow" and d["reserve_rub"] == "30.00"


async def test_3_5_new_price_from_15th_old_events_unchanged(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    await catalog(seed, only="A")
    dep = await _enforced_department(client, admin, seed)
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key()
    old = await decide(client, key, decision_body(model="A"))
    await report(client, key, usage_body(old["decision_id"], 1000, 400))

    resp = await client.post(
        "/api/v1/models/A/prices",
        json={
            "valid_from": msk(2026, 10, 15).isoformat(),
            "input_per_1k": "20",
            "output_per_1k": "100",
            "currency": "RUB",
        },
        headers=admin,
    )
    assert resp.status_code == 201, resp.text
    assert_matches_contract(resp, "createModelPrice")
    still_old = await decide(client, key, decision_body(model="A"))
    assert still_old["reserve_rub"] == "30.00"  # 14-го действует старая цена

    clock.set(msk(2026, 10, 15, 10))
    new = await decide(client, key, decision_body(model="A"))
    assert new["reserve_rub"] == "60.00"
    assert (await call_record(db, old["decision_id"]))["fact_kop"] == 3000  # списанное не пересчитано
    # Сессия админки живёт 12 ч (SESSION_TTL), а часы сдвинуты на 22 ч — входим заново.
    relogin = await client.post("/api/v1/auth/login", json={"login": "root", "password": ADMIN_PASSWORD})
    assert relogin.status_code == 200, relogin.text
    hist = await client.get("/api/v1/models/A/prices")
    assert_matches_contract(hist, "listModelPrices")
    froms = [x["valid_from"] for x in hist.json()["items"]]
    assert hist.json()["total"] == 2 and froms == sorted(froms, reverse=True)


async def test_3_5_price_in_past_rejected(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed, clock: FrozenClock
) -> None:
    await catalog(seed, only="A")
    resp = await client.post(
        "/api/v1/models/A/prices",
        json={
            "valid_from": (clock.now() - timedelta(days=1)).isoformat(),
            "input_per_1k": "1",
            "output_per_1k": "1",
            "currency": "RUB",
        },
        headers=admin,
    )
    assert resp.status_code == 422
    assert_matches_contract(resp, "createModelPrice")


async def test_usd_price_without_fx_rate_is_422(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection
) -> None:
    usd = MODEL_X | {
        "id": "claude-x",
        "provider": "anthropic",
        "price": MODEL_X["price"] | {"currency": "USD"},
    }
    resp = await client.post("/api/v1/models", json=usd, headers=admin)
    assert resp.status_code == 422 and resp.json()["code"] == "fx_rate_missing"
    assert CYRILLIC.search(resp.json()["detail"])
    assert_matches_contract(resp, "createModel")
    assert await db.fetchval("SELECT count(*) FROM model") == 0
    fx = await client.put("/api/v1/fx-rates/2026-10", json={"usd_rub": "92.5000"}, headers=admin)
    assert fx.status_code == 200
    assert_matches_contract(fx, "putFxRate")
    resp = await client.post("/api/v1/models", json=usd, headers=admin)
    assert resp.status_code == 201


# ---------------------------------------------------------------- аудит «было / стало»


async def test_every_change_is_audited_with_before_and_after(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed, db: asyncpg.Connection
) -> None:
    await catalog(seed, only="A")
    await seed.department("База")  # чтобы была строка до изменений
    changes: list[tuple[str, str, dict[str, Any] | None, bool]] = []  # (метод, путь, тело, это обновление)

    created = await client.post("/api/v1/departments", json={"name": "Маркетинг"}, headers=admin)
    assert created.status_code == 201, created.text
    dep = created.json()["id"]
    changes += [
        ("PATCH", f"/api/v1/departments/{dep}", {"policy_mode": "enforce"}, True),
        ("PATCH", f"/api/v1/departments/{dep}", {"allowed_models": ["A"]}, True),
        ("PUT", f"/api/v1/departments/{dep}/limits", {"day": "5000.00", "week": None, "month": None}, True),
        ("PUT", f"/api/v1/departments/{dep}/limits", {"day": "6000.00", "week": None, "month": None}, True),
        ("PATCH", "/api/v1/models/A", {"tie_order": 5}, True),
        (
            "POST",
            "/api/v1/models/A/prices",
            {"input_per_1k": "11", "output_per_1k": "55", "currency": "RUB"},
            False,
        ),
        ("PUT", "/api/v1/fx-rates/2026-10", {"usd_rub": "92.5000"}, False),
        ("PUT", "/api/v1/fx-rates/2026-10", {"usd_rub": "93.0000"}, True),
        (
            "POST",
            "/api/v1/admin-users",
            {"login": "fin", "password": "fin-password-123", "role": "finance_viewer"},
            False,
        ),
    ]
    for method, path, body, is_update in changes:
        n0, v0 = await audit_count(db), await config_version(db)
        resp = await client.request(method, path, json=body, headers=admin)
        assert resp.status_code in (200, 201), f"{method} {path}: {resp.status_code} {resp.text[:200]}"
        assert await audit_count(db) == n0 + 1, f"{method} {path}: нет записи аудита"
        row = await db.fetchrow("SELECT actor, before, after FROM audit_log ORDER BY id DESC LIMIT 1")
        assert row is not None and row["actor"] == "root"
        assert row["after"] is not None
        if is_update:
            assert row["before"] is not None and row["before"] != row["after"], f"{method} {path}: нет «было»"
        if not path.startswith("/api/v1/admin-users"):
            assert await config_version(db) == v0 + 1, f"{method} {path}: config_version не увеличена"


# ---------------------------------------------------------------- ключи вызывающих систем


async def test_caller_key_shown_once_and_revocation_is_immediate(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed, db: asyncpg.Connection
) -> None:
    await catalog(seed, only="A")
    dep = await seed.department("Маркетинг", allowed=("A",))
    await seed.employee("ivanov", department=dep)
    resp = await client.post(
        "/api/v1/callers", json={"name": "gateway", "subject_scope": {"type": "any_employee"}}, headers=admin
    )
    assert resp.status_code == 201, resp.text
    assert_matches_contract(resp, "createCaller")
    created = resp.json()
    key = created["api_key"]
    assert key.startswith(f"bm_{created['key_prefix']}_")
    stored = await db.fetchval("SELECT key_sha256 FROM caller_system WHERE id = $1", UUID(created["id"]))
    assert bytes(stored) == hashlib.sha256(key.encode()).digest()  # в БД только хэш

    listing = await client.get("/api/v1/callers")
    assert listing.status_code == 200
    assert_matches_contract(listing, "listCallers")
    assert key not in listing.text and all("api_key" not in x for x in listing.json()["items"])

    assert (await decide(client, key, decision_body(model="A")))["outcome"] == "allow"
    resp = await client.post(f"/api/v1/callers/{created['id']}/revoke", headers=admin)
    assert resp.status_code == 200 and resp.json()["status"] == "revoked"
    assert_matches_contract(resp, "revokeCaller")
    after = await post_decision(client, key, decision_body(model="A"))
    assert after.status_code == 401  # отзыв действует на следующий же запрос
    assert (
        await db.fetchval(
            "SELECT count(*) FROM audit_log WHERE action = 'revoke' AND object_id = $1", created["id"]
        )
        == 1
    )
    bal = await client.get("/api/v1/balance", params={"login": "ivanov"}, headers=key_headers(key))
    assert bal.status_code == 401


# ---------------------------------------------------------------- псевдонимы и права


async def test_reveal_pseudonym_admin_only_and_audited(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed, db: asyncpg.Connection
) -> None:
    dep = await seed.department("Маркетинг")
    emp = await seed.employee("ivanov", department=dep, display_name="Иванов Иван")
    resp = await client.get(f"/api/v1/pseudonyms/{emp.pseudonym}")
    assert resp.status_code == 200
    assert_matches_contract(resp, "revealPseudonym")
    assert resp.json()["login"] == "ivanov"
    assert (
        await db.fetchval(
            "SELECT count(*) FROM audit_log WHERE action = 'reveal_pseudonym' AND actor = 'root'"
        )
        == 1
    )
    async with httpx.AsyncClient(transport=client._transport, base_url=str(client.base_url)) as owner:
        await admin_client_login(owner, seed, "owner", role="budget_owner", department=dep)
        denied = await owner.get(f"/api/v1/pseudonyms/{emp.pseudonym}")
        assert denied.status_code == 403
        assert_matches_contract(denied, "revealPseudonym")


async def test_finance_viewer_cannot_change_settings(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    await catalog(seed, only="A")
    csrf = await admin_client_login(client, seed, "fin", role="finance_viewer")
    resp = await client.post("/api/v1/models", json=MODEL_X, headers=csrf)
    assert resp.status_code == 403
    assert_matches_contract(resp, "createModel")
    resp = await client.get("/api/v1/models")
    assert resp.status_code == 200  # чтение каталога доступно
    assert_matches_contract(resp, "listModels")
