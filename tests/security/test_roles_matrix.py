"""T070, SC-010: матрица «роль × операция» строится из `PERMISSIONS` (bm/access/permissions.py) и контракта.

Для каждой операции админки (security: session) и каждой роли:
- роль не входит в `PERMISSIONS[op]` → 403 `forbidden` по контракту и запись `forbidden_access` в аудите;
- роль входит → не 401 и не 403 (тело запроса пустое, поэтому 404/422 допустимы — проверяется только доступ);
- без сессии → 401.
Отдельно — владелец бюджета против данных чужого подразделения: 0 успешных обращений, 100% отказов в аудите.

Операции, роутеры которых ещё не написаны (`PENDING` контрактного теста), проверяются в отдельных тестах с
`xfail(strict)`: когда последняя из них появится (T071), тест станет XPASS и метку нужно снять.
Метки сняты в T071: `PENDING` пуст, все операции админки проверяются тестами `*_implemented*`.
"""

import uuid
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest

from bm.access.permissions import PERMISSIONS
from tests.contract.helpers import assert_matches_contract, contract, operation, operations
from tests.contract.test_openapi_contract import PENDING, _registered
from tests.fixtures.qa import session_as
from tests.fixtures.seed import Emp, Seed

pytestmark = pytest.mark.db

ROLES = ("admin", "budget_owner", "finance_viewer")
QUERY_VALUES = {"from": "2026-10-01", "to": "2026-10-31", "period": "month", "month": "2026-10"}


def session_ops() -> list[str]:
    return sorted(op for op in operations() if any("session" in s for s in operation(op).get("security", [])))


def _param(p: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in p:
        name = p["$ref"].rsplit("/", 1)[-1]
        resolved: dict[str, Any] = contract()["components"]["parameters"][name]
        return resolved
    return p


def build_request(op: str, ids: dict[str, str]) -> tuple[str, str, dict[str, Any]]:
    """(метод, путь, kwargs для httpx) с подставленными идентификаторами и обязательными query-параметрами."""
    method, template = operations()[op]
    path = template
    for name, value in ids.items():
        path = path.replace("{" + name + "}", value)
    params = {}
    item = contract()["paths"][template]
    for p in [*item.get("parameters", []), *operation(op).get("parameters", [])]:
        p = _param(p)
        if p["in"] == "query" and p.get("required"):
            params[p["name"]] = QUERY_VALUES[p["name"]]
    kw: dict[str, Any] = {"params": params}
    body = operation(op).get("requestBody", {}).get("content", {})
    if "multipart/form-data" in body:
        kw["files"] = {"file": ("ad.csv", b"login;display_name;group;is_primary\n", "text/csv")}
    elif "application/json" in body:
        kw["json"] = {}
    return method.upper(), path, kw


async def matrix_world(seed: Seed) -> tuple[UUID, UUID, Emp, Emp]:
    await seed.model("A")
    a = await seed.department("Маркетинг", allowed=("A",))
    b = await seed.department("Разработка", allowed=("A",))
    return a, b, await seed.employee("ivanov", department=a), await seed.employee("petrov", department=b)


def ids_for(dep: UUID, emp: Emp) -> dict[str, str]:
    return {
        "department_id": str(dep),
        "pseudonym": emp.pseudonym,
        "model_id": "A",
        "month": "2026-10",
        "subscription_id": str(uuid.uuid4()),
        "source_id": str(uuid.uuid4()),
        "caller_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "notification_id": str(uuid.uuid4()),
    }


async def forbidden_rows(db: asyncpg.Connection, actor: str, op: str) -> int:
    n: int = await db.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'forbidden_access' AND actor = $1 AND object_id = $2",
        actor,
        op,
    )
    return n


async def check_role(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, role: str, ops: list[str]
) -> list[str]:
    a, _, ivanov, _ = await matrix_world(seed)
    login = f"user_{role}"
    registered = _registered(app)
    problems: list[str] = [f"{op}: маршрута нет в приложении" for op in ops if op not in registered]
    async with session_as(
        client, seed, login, role=role, department=a if role == "budget_owner" else None
    ) as (
        c,
        csrf,
    ):
        for op in ops:
            if op == "logout":  # закрыла бы сессию; права logout проверяет tests/acceptance/test_auth.py
                continue
            method, path, kw = build_request(op, ids_for(a, ivanov))
            before = await forbidden_rows(db, login, op)
            resp = await c.request(method, path, headers=csrf, **kw)
            allowed = role in {r.value for r in PERMISSIONS[op]}
            if allowed and resp.status_code in (401, 403):
                problems.append(f"{op}: роль {role} разрешена, но {resp.status_code} {resp.text[:120]}")
            if not allowed:
                if resp.status_code != 403 or resp.json().get("code") != "forbidden":
                    problems.append(
                        f"{op}: роль {role} запрещена, а ответ {resp.status_code} {resp.text[:120]}"
                    )
                    continue
                try:
                    assert_matches_contract(resp, op)
                except AssertionError as e:
                    problems.append(str(e)[:200])
                if await forbidden_rows(db, login, op) != before + 1:
                    problems.append(f"{op}: отказ роли {role} не записан в аудит (forbidden_access)")
    return problems


def implemented() -> list[str]:
    return [op for op in session_ops() if op not in PENDING]


def pending() -> list[str]:
    return [op for op in session_ops() if op in PENDING]


def test_matrix_covers_every_session_operation() -> None:
    assert set(session_ops()) == set(PERMISSIONS), "PERMISSIONS и операции контракта с session расходятся"


@pytest.mark.parametrize("role", ROLES)
async def test_role_matrix_implemented_operations(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, role: str
) -> None:
    problems = await check_role(app, client, seed, db, role, implemented())
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("role", ROLES)
async def test_role_matrix_pending_operations(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, role: str
) -> None:
    problems = await check_role(app, client, seed, db, role, pending())
    assert problems == [], "\n".join(problems)


async def _anonymous(client: httpx.AsyncClient, seed: Seed, ops: list[str]) -> list[str]:
    a, _, ivanov, _ = await matrix_world(seed)
    problems = []
    for op in ops:
        method, path, kw = build_request(op, ids_for(a, ivanov))
        resp = await client.request(method, path, **kw)
        if resp.status_code != 401:
            problems.append(f"{op}: без сессии {resp.status_code}")
            continue
        try:
            assert_matches_contract(resp, op)
        except AssertionError as e:
            problems.append(str(e)[:200])
    return problems


async def test_anonymous_gets_401_on_implemented_operations(client: httpx.AsyncClient, seed: Seed) -> None:
    problems = await _anonymous(client, seed, implemented())
    assert problems == [], "\n".join(problems)


async def test_anonymous_gets_401_on_pending_operations(client: httpx.AsyncClient, seed: Seed) -> None:
    problems = await _anonymous(client, seed, pending())
    assert problems == [], "\n".join(problems)


# ---------------------------------------------------------------- чужое подразделение (6.4)


async def foreign_attempts(
    db: asyncpg.Connection, b: UUID, petrov: Emp
) -> list[tuple[str, str, str, dict[str, Any]]]:
    """(операция, метод, путь, kwargs): объекты подразделения «Разработка» для владельца «Маркетинга»."""
    sub_id = await db.fetchval(
        "INSERT INTO subscription(provider, plan, seat_price, currency, seats, start_date, assignee_type, assignee_id) "
        "VALUES ('anthropic', 'Team', 100, 'RUB', 1, '2026-10-01', 'department', $1) RETURNING id",
        b,
    )
    note_id = await db.fetchval(
        "INSERT INTO notification(kind, department_id, period, period_start, message) "
        "VALUES ('threshold_80', $1, 'month', '2026-10-01', 'Расход 80%') RETURNING id",
        b,
    )
    month = {"from": "2026-10-01", "to": "2026-10-31", "department_id": str(b)}
    return [
        ("getDepartment", "GET", f"/api/v1/departments/{b}", {}),
        ("getEmployee", "GET", f"/api/v1/employees/{petrov.pseudonym}", {}),
        ("markNotificationRead", "POST", f"/api/v1/notifications/{note_id}/read", {}),
        ("getDepartmentBalance", "GET", f"/api/v1/departments/{b}/balance", {}),
        (
            "listDepartmentBalanceItems",
            "GET",
            f"/api/v1/departments/{b}/balance/items",
            {"params": {"period": "month"}},
        ),
        ("listSubscriptions", "GET", "/api/v1/subscriptions", {"params": {"department_id": str(b)}}),
        ("getSubscription", "GET", f"/api/v1/subscriptions/{sub_id}", {}),
        ("getSpendReport", "GET", "/api/v1/reports/spend", {"params": month}),
        ("getSpendCsv", "GET", "/api/v1/reports/spend.csv", {"params": month}),
        ("listEvents", "GET", "/api/v1/events", {"params": month}),
    ]


async def _foreign(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, *, want_pending: bool
) -> list[str]:
    a, b, _, petrov = await matrix_world(seed)
    attempts = [x for x in await foreign_attempts(db, b, petrov) if (x[0] in PENDING) == want_pending]
    problems = []
    async with session_as(client, seed, "owner_a", role="budget_owner", department=a) as (owner, csrf):
        for op, method, path, kw in attempts:
            before = await forbidden_rows(db, "owner_a", op)
            resp = await owner.request(method, path, headers=csrf, **kw)
            if resp.status_code != 403:
                problems.append(f"{op}: чужое подразделение → {resp.status_code} {resp.text[:120]}")
                continue
            if "Разработка" in resp.text or petrov.pseudonym in resp.text:
                problems.append(f"{op}: в отказе есть данные чужого подразделения")
            if await forbidden_rows(db, "owner_a", op) != before + 1:
                problems.append(f"{op}: попытка не записана в аудит")
    return problems


async def test_owner_cannot_reach_other_department_implemented(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    problems = await _foreign(client, seed, db, want_pending=False)
    assert problems == [], "\n".join(problems)


async def test_owner_cannot_reach_other_department_pending(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    problems = await _foreign(client, seed, db, want_pending=True)
    assert problems == [], "\n".join(problems)


async def test_owner_lists_contain_only_own_department(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    a, b, ivanov, petrov = await matrix_world(seed)
    await db.execute(
        "INSERT INTO notification(kind, department_id, period, period_start, message) "
        "VALUES ('threshold_80', $1, 'month', '2026-10-01', 'Расход 80%')",
        b,
    )
    async with session_as(client, seed, "owner_a", role="budget_owner", department=a) as (owner, _):
        deps = await owner.get("/api/v1/departments")
        assert deps.status_code == 200
        assert {d["id"] for d in deps.json()["items"]} == {str(a)}
        emps = await owner.get("/api/v1/employees")
        assert emps.status_code == 200
        assert {e["pseudonym"] for e in emps.json()["items"]} == {ivanov.pseudonym}
        notes = await owner.get("/api/v1/notifications")
        assert notes.status_code == 200 and notes.json()["items"] == []
        assert petrov.pseudonym not in emps.text and "Разработка" not in deps.text
