"""T041/T042 (backend): правила админ-CRUD и загрузки AD сверх приёмочных тестов QA (test_us3_config.py).

- `SESSION_COOKIE_SECURE` (14_tech_rulings.md Б2): флаг `Secure` у `bm_session` — по настройке;
- `409 last_admin` (13_design_rulings.md В6): нельзя оставить систему без активного администратора;
- права и подразделение владельца бюджета на новых роутерах, 401 без сессии — по контракту 1.0.2 (Б1);
- валидация: формат файла AD, область ключа вызывающей системы, неизвестная модель в разрешённых, лимиты.
"""

from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.qa import ADMIN_PASSWORD, CYRILLIC, admin_client_login, catalog
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db

HEADER = "login;display_name;group;is_primary\n"


@pytest.fixture
async def admin(client: httpx.AsyncClient, seed: Seed) -> dict[str, str]:
    return await admin_client_login(client, seed, "root")


async def _import(
    client: httpx.AsyncClient, csrf: dict[str, str], data: bytes, dry_run: bool = False
) -> httpx.Response:
    return await client.post(
        "/api/v1/org/import",
        files={"file": ("ad.csv", data, "text/csv")},
        data={"dry_run": "true" if dry_run else "false"},
        headers=csrf,
    )


def _other(client: httpx.AsyncClient) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=client._transport, base_url=str(client.base_url))


# ---------------------------------------------------------------- SESSION_COOKIE_SECURE (Б2)


@pytest.mark.parametrize("secure", [False, True])
async def test_session_cookie_secure_flag_follows_setting(
    db: asyncpg.Connection, seed: Seed, settings: Any, clock: FrozenClock, secure: bool
) -> None:
    from bm.main import create_app

    await seed.admin_user("anna", ADMIN_PASSWORD)
    app = create_app(settings.model_copy(update={"session_cookie_secure": secure}), clock=clock)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8080") as c:
            resp = await c.post("/api/v1/auth/login", json={"login": "anna", "password": ADMIN_PASSWORD})
    assert resp.status_code == 200
    attrs = [a.strip().lower() for a in resp.headers["set-cookie"].split(";")]
    assert ("secure" in attrs) is secure
    assert "httponly" in attrs and "samesite=strict" in attrs


# ---------------------------------------------------------------- 401 без сессии на новых роутерах (Б1)


@pytest.mark.parametrize(
    ("method", "path", "op_id"),
    [
        ("GET", "/api/v1/departments", "listDepartments"),
        ("GET", f"/api/v1/departments/{uuid4()}", "getDepartment"),
        ("GET", "/api/v1/employees", "listEmployees"),
        ("GET", "/api/v1/models", "listModels"),
        ("GET", "/api/v1/fx-rates", "listFxRates"),
        ("GET", "/api/v1/callers", "listCallers"),
        ("GET", "/api/v1/admin-users", "listAdminUsers"),
        ("POST", "/api/v1/org/import", "importOrg"),
    ],
)
async def test_new_routes_require_session(
    client: httpx.AsyncClient, method: str, path: str, op_id: str
) -> None:
    resp = await client.request(method, path)
    assert resp.status_code == 401 and resp.json()["code"] == "unauthorized"
    assert_matches_contract(resp, op_id)


async def test_mutation_without_csrf_is_403(client: httpx.AsyncClient, admin: dict[str, str]) -> None:
    resp = await client.post("/api/v1/departments", json={"name": "Без CSRF"})
    assert resp.status_code == 403
    assert_matches_contract(resp, "createDepartment")


# ---------------------------------------------------------------- подразделения и сотрудники: права


async def test_budget_owner_sees_only_own_department_and_employees(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep_a = await seed.department("А", policy_mode="observe")
    dep_b = await seed.department("Б", policy_mode="observe")
    await seed.employee("ivanov", department=dep_a)
    emp_b = await seed.employee("petrov", department=dep_b)
    await admin_client_login(client, seed, "owner", role="budget_owner", department=dep_a)

    resp = await client.get("/api/v1/departments")
    assert resp.status_code == 200
    assert_matches_contract(resp, "listDepartments")
    assert [d["id"] for d in resp.json()["items"]] == [str(dep_a)]
    assert resp.json()["items"][0]["employees_count"] == 1

    foreign = await client.get(f"/api/v1/departments/{dep_b}")
    assert foreign.status_code == 403
    assert_matches_contract(foreign, "getDepartment")

    emps = await client.get("/api/v1/employees")
    assert emps.status_code == 200
    assert_matches_contract(emps, "listEmployees")
    assert emps.json()["total"] == 1 and emps.json()["items"][0]["department_id"] == str(dep_a)

    other = await client.get("/api/v1/employees", params={"department_id": str(dep_b)})
    assert other.status_code == 403
    assert_matches_contract(other, "listEmployees")
    got = await client.get(f"/api/v1/employees/{emp_b.pseudonym}")
    assert got.status_code == 403
    assert_matches_contract(got, "getEmployee")
    n = await db.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'forbidden_access' AND actor = 'owner'"
    )
    assert n == 3


async def test_finance_viewer_cannot_list_employees(client: httpx.AsyncClient, seed: Seed) -> None:
    await admin_client_login(client, seed, "fin", role="finance_viewer")
    resp = await client.get("/api/v1/employees")
    assert resp.status_code == 403
    assert_matches_contract(resp, "listEmployees")


async def test_department_not_found_and_q_filter(client: httpx.AsyncClient, admin: dict[str, str]) -> None:
    resp = await client.get(f"/api/v1/departments/{uuid4()}")
    assert resp.status_code == 404
    assert_matches_contract(resp, "getDepartment")
    for name in ("Маркетинг", "Разработка"):
        assert (
            await client.post("/api/v1/departments", json={"name": name}, headers=admin)
        ).status_code == 201
    resp = await client.get("/api/v1/departments", params={"q": "марк"})
    assert [d["name"] for d in resp.json()["items"]] == ["Маркетинг"]


async def test_department_ad_group_conflict_is_409(client: httpx.AsyncClient, admin: dict[str, str]) -> None:
    body = {"name": "Маркетинг", "ad_group": "MKT"}
    assert (await client.post("/api/v1/departments", json=body, headers=admin)).status_code == 201
    resp = await client.post("/api/v1/departments", json=body | {"name": "Маркетинг-2"}, headers=admin)
    assert resp.status_code == 409 and resp.json()["code"] == "conflict"
    assert_matches_contract(resp, "createDepartment")


async def test_unknown_model_in_allowed_models_is_422(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed, db: asyncpg.Connection
) -> None:
    dep = await seed.department("А", policy_mode="observe")
    v0 = await db.fetchval("SELECT version FROM config_version")
    resp = await client.patch(f"/api/v1/departments/{dep}", json={"allowed_models": ["nope"]}, headers=admin)
    assert resp.status_code == 422 and CYRILLIC.search(resp.json()["detail"])
    assert_matches_contract(resp, "updateDepartment")
    assert await db.fetchval("SELECT version FROM config_version") == v0


async def test_negative_or_malformed_limit_is_422(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed
) -> None:
    dep = await seed.department("А", policy_mode="observe")
    for bad in ("-1.00", "10", "1.5"):
        resp = await client.put(
            f"/api/v1/departments/{dep}/limits", json={"day": bad, "week": None, "month": None}, headers=admin
        )
        assert resp.status_code == 422, bad
        assert_matches_contract(resp, "putDepartmentLimits")


async def test_employee_limits_and_reveal_404(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed
) -> None:
    dep = await seed.department("А", policy_mode="observe")
    emp = await seed.employee("ivanov", department=dep)
    limits = {"day": "100.00", "week": None, "month": "1000.00"}
    resp = await client.put(f"/api/v1/employees/{emp.pseudonym}/limits", json=limits, headers=admin)
    assert resp.status_code == 200 and resp.json() == limits
    assert_matches_contract(resp, "putEmployeeLimits")
    got = await client.get(f"/api/v1/employees/{emp.pseudonym}")
    assert_matches_contract(got, "getEmployee")
    assert got.json() == {
        "pseudonym": emp.pseudonym,
        "department_id": str(dep),
        "active": True,
        "limits": limits,
    }
    missing = await client.get("/api/v1/pseudonyms/emp-0000000000")
    assert missing.status_code == 404
    assert_matches_contract(missing, "revealPseudonym")


# ---------------------------------------------------------------- модели и курсы


async def test_update_model_and_list(client: httpx.AsyncClient, admin: dict[str, str], seed: Seed) -> None:
    await catalog(seed, only="AB")
    resp = await client.patch("/api/v1/models/A", json={"active": False, "tie_order": 1}, headers=admin)
    assert resp.status_code == 200 and resp.json()["active"] is False and resp.json()["tie_order"] == 1
    assert_matches_contract(resp, "updateModel")
    listing = await client.get("/api/v1/models")
    assert_matches_contract(listing, "listModels")
    assert [m["id"] for m in listing.json()["items"]] == ["A", "B"]
    assert listing.json()["items"][0]["current_price"]["currency"] == "RUB"
    missing = await client.patch("/api/v1/models/nope", json={"active": False}, headers=admin)
    assert missing.status_code == 404
    assert_matches_contract(missing, "updateModel")
    bad = await client.patch("/api/v1/models/A", json={"price": {}}, headers=admin)
    assert bad.status_code == 422  # цены — только через /prices


async def test_fx_rates_listed_by_month_desc(client: httpx.AsyncClient, admin: dict[str, str]) -> None:
    for month, rate in (("2026-09", "90.1"), ("2026-10", "92.5000")):
        resp = await client.put(f"/api/v1/fx-rates/{month}", json={"usd_rub": rate}, headers=admin)
        assert resp.status_code == 200
        assert_matches_contract(resp, "putFxRate")
    resp = await client.get("/api/v1/fx-rates")
    assert_matches_contract(resp, "listFxRates")
    assert [(x["month"], x["usd_rub"], x["entered_by"]) for x in resp.json()["items"]] == [
        ("2026-10", "92.5000", "root"),
        ("2026-09", "90.1000", "root"),
    ]


# ---------------------------------------------------------------- вызывающие системы


@pytest.mark.parametrize(
    "scope",
    [
        {"type": "service"},
        {"type": "department_employees", "department_id": None},
        {"type": "service", "department_id": "00000000-0000-0000-0000-000000000001"},
    ],
)
async def test_caller_scope_requires_existing_department(
    client: httpx.AsyncClient, admin: dict[str, str], scope: dict[str, Any]
) -> None:
    resp = await client.post("/api/v1/callers", json={"name": "svc", "subject_scope": scope}, headers=admin)
    assert resp.status_code == 422
    assert_matches_contract(resp, "createCaller")


async def test_caller_update_and_audit_has_no_key(
    client: httpx.AsyncClient, admin: dict[str, str], seed: Seed, db: asyncpg.Connection
) -> None:
    dep = await seed.department("А", policy_mode="observe")
    resp = await client.post(
        "/api/v1/callers",
        json={"name": "svc", "subject_scope": {"type": "service", "department_id": str(dep)}},
        headers=admin,
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["subject_scope"] == {"type": "service", "department_id": str(dep)}
    upd = await client.patch(f"/api/v1/callers/{created['id']}", json={"name": "svc-2"}, headers=admin)
    assert upd.status_code == 200 and upd.json()["name"] == "svc-2"
    assert_matches_contract(upd, "updateCaller")
    dump = await db.fetchval(
        "SELECT string_agg(coalesce(before::text, '') || after::text, ' ') FROM audit_log"
    )
    assert created["api_key"] not in dump
    missing = await client.post(f"/api/v1/callers/{uuid4()}/revoke", headers=admin)
    assert missing.status_code == 404
    assert_matches_contract(missing, "revokeCaller")


# ---------------------------------------------------------------- пользователи админки, last_admin (В6)


async def _user_id(db: asyncpg.Connection, login: str) -> UUID:
    v = await db.fetchval("SELECT id FROM admin_user WHERE login = $1", login)
    assert v is not None
    return UUID(str(v))


@pytest.mark.parametrize("patch", [{"active": False}, {"role": "finance_viewer"}])
async def test_last_active_admin_cannot_be_disabled_or_demoted(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection, patch: dict[str, Any]
) -> None:
    # bootstrap-admin из настроек тоже admin; выключаем его, чтобы root остался единственным
    await db.execute("UPDATE admin_user SET active = false WHERE login = 'bootstrap-admin'")
    root = await _user_id(db, "root")
    n0 = await db.fetchval("SELECT count(*) FROM audit_log")
    resp = await client.patch(f"/api/v1/admin-users/{root}", json=patch, headers=admin)
    assert resp.status_code == 409 and resp.json()["code"] == "last_admin"
    assert CYRILLIC.search(resp.json()["detail"])
    assert_matches_contract(resp, "updateAdminUser")
    row = await db.fetchrow("SELECT role, active FROM admin_user WHERE id = $1", root)
    assert (row["role"], row["active"]) == ("admin", True)
    assert await db.fetchval("SELECT count(*) FROM audit_log") == n0


async def test_admin_can_disable_self_when_another_admin_active(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection
) -> None:
    root = await _user_id(db, "root")
    resp = await client.patch(f"/api/v1/admin-users/{root}", json={"active": False}, headers=admin)
    assert resp.status_code == 200 and resp.json()["active"] is False
    assert (await client.get("/api/v1/auth/me")).status_code == 401  # сессии выключенного закрыты


async def test_create_admin_user_and_reset_password(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection, seed: Seed
) -> None:
    dep = await seed.department("А", policy_mode="observe")
    body = {
        "login": "owner",
        "password": "owner-password-1",
        "role": "budget_owner",
        "department_id": str(dep),
    }
    resp = await client.post("/api/v1/admin-users", json=body, headers=admin)
    assert resp.status_code == 201
    assert_matches_contract(resp, "createAdminUser")
    user = resp.json()
    assert user["must_change_password"] is True and user["department_id"] == str(dep)
    dup = await client.post("/api/v1/admin-users", json=body, headers=admin)
    assert dup.status_code == 409
    assert_matches_contract(dup, "createAdminUser")
    no_dep = await client.post(
        "/api/v1/admin-users",
        json={"login": "o2", "password": "owner-password-1", "role": "budget_owner"},
        headers=admin,
    )
    assert no_dep.status_code == 422
    assert_matches_contract(no_dep, "createAdminUser")

    async with _other(client) as owner:
        r = await owner.post("/api/v1/auth/login", json={"login": "owner", "password": "owner-password-1"})
        assert r.status_code == 200
        resp = await client.patch(
            f"/api/v1/admin-users/{user['id']}", json={"reset_password": "brand-new-pass-1"}, headers=admin
        )
        assert resp.status_code == 200 and resp.json()["must_change_password"] is True
        assert_matches_contract(resp, "updateAdminUser")
        assert (await owner.get("/api/v1/auth/me")).status_code == 401  # сессии сброшены
    listing = await client.get("/api/v1/admin-users")
    assert_matches_contract(listing, "listAdminUsers")
    assert {u["login"] for u in listing.json()["items"]} == {"bootstrap-admin", "root", "owner"}
    dump = await db.fetchval(
        "SELECT string_agg(coalesce(before::text, '') || after::text, ' ') FROM audit_log"
    )
    assert "owner-password-1" not in dump and "brand-new-pass-1" not in dump and "argon2" not in dump


async def test_update_unknown_admin_user_is_404(client: httpx.AsyncClient, admin: dict[str, str]) -> None:
    resp = await client.patch(f"/api/v1/admin-users/{uuid4()}", json={"active": False}, headers=admin)
    assert resp.status_code == 404
    assert_matches_contract(resp, "updateAdminUser")


# ---------------------------------------------------------------- загрузка AD: формат файла


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"login,display_name,group,is_primary\nivanov,I,G,1\n",  # не тот разделитель
        "login;display_name;group;is_primary\nivanov;Иванов;Маркетинг;1\n".encode("cp1251"),  # не UTF-8
    ],
)
async def test_import_bad_file_is_422(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection, data: bytes
) -> None:
    resp = await _import(client, admin, data)
    assert resp.status_code == 422 and CYRILLIC.search(resp.json()["detail"])
    assert_matches_contract(resp, "importOrg")
    assert await db.fetchval("SELECT count(*) FROM department") == 0


async def test_import_bom_and_duplicate_primary(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection
) -> None:
    data = (
        "﻿"
        + HEADER
        + "ivanov;Иванов;Маркетинг;1\n"
        + "ivanov;Иванов;Разработка;1\n"  # вторая основная группа — ошибка строки
        + "ivanov;Иванов;Маркетинг;0\n"  # повтор пары логин+группа — ошибка строки
        + "petrov;Петров;Маркетинг;1;лишнее\n"  # лишняя колонка
    ).encode()
    resp = await _import(client, admin, data)
    assert resp.status_code == 200, resp.text
    s = resp.json()
    assert (s["rows_total"], s["rows_applied"]) == (4, 1)
    assert sorted(e["line"] for e in s["errors"]) == [3, 4, 5]
    assert s["departments_added"] == 1  # «Разработка» только в ошибочной строке
    assert await db.fetchval("SELECT count(*) FROM department") == 1


async def test_import_updates_display_name_and_audits_summary(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection
) -> None:
    assert (
        await _import(client, admin, (HEADER + "ivanov;Иванов;Маркетинг;1\n").encode())
    ).status_code == 200
    s = (await _import(client, admin, (HEADER + "ivanov;Иванов Иван;Маркетинг;1\n").encode())).json()
    assert (s["employees_updated"], s["employees_moved"], s["employees_added"]) == (1, 0, 0)
    assert await db.fetchval("SELECT display_name FROM employee") == "Иванов Иван"
    row = await db.fetchrow(
        "SELECT actor, after::text AS after FROM audit_log WHERE action = 'import' ORDER BY id DESC LIMIT 1"
    )
    assert row["actor"] == "root" and "ivanov" not in row["after"]  # в аудите сводка, без логинов


async def test_new_department_from_import_is_observe(
    client: httpx.AsyncClient, admin: dict[str, str], db: asyncpg.Connection
) -> None:
    await _import(client, admin, (HEADER + "ivanov;Иванов;Маркетинг;1\n").encode())
    row = await db.fetchrow("SELECT name, ad_group, policy_mode FROM department")
    assert (row["name"], row["ad_group"], row["policy_mode"]) == ("Маркетинг", "Маркетинг", "observe")
