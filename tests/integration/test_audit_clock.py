"""T073, первый шаг (14_tech_rulings.md, пакет 3, 3Б7): время записи аудита — от `Clock` приложения.

Во всех местах записи (`config.apply`, `forbidden_access`, вход, `reveal_pseudonym`, импорт AD) поле `at`
равно времени часов приложения, а не `now()` БД. Под `FrozenClock` запись видна в `listAudit` за
замороженный интервал. Замороженное время намеренно далеко от реального (март 2026), чтобы `now()` БД
не попадало в интервал случайно.
"""

from datetime import datetime
from typing import Any

import asyncpg
import httpx
import pytest

from bm.access import audit
from bm.clock import FrozenClock
from bm.config.service import Applied, Change, apply
from bm.domain.periods import MSK
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.qa import admin_client_login, session_as
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db

FROZEN = datetime(2026, 3, 3, 10, 15, 30, tzinfo=MSK)
DAY = {"from": "2026-03-03", "to": "2026-03-03"}


@pytest.fixture
def frozen(clock: FrozenClock) -> FrozenClock:
    clock.set(FROZEN)
    return clock


async def _audit(client: httpx.AsyncClient, **params: str) -> list[dict[str, Any]]:
    resp = await client.get("/api/v1/audit", params=DAY | params | {"page_size": "200"})
    assert resp.status_code == 200, resp.text
    assert_matches_contract(resp, "listAudit")
    items: list[dict[str, Any]] = resp.json()["items"]
    return items


def _at(entry: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(entry["at"])


async def test_config_apply_uses_app_clock(
    frozen: FrozenClock, client: httpx.AsyncClient, seed: Seed
) -> None:
    csrf = await admin_client_login(client, seed)
    created = await client.post("/api/v1/departments", json={"name": "Маркетинг"}, headers=csrf)
    assert created.status_code == 201, created.text
    dep = created.json()["id"]
    await client.put(f"/api/v1/departments/{dep}/limits", json={"month": "1000.00"}, headers=csrf)
    user = await client.post(
        "/api/v1/admin-users",
        json={"login": "fin", "password": "fin-password-123", "role": "finance_viewer"},
        headers=csrf,
    )
    assert user.status_code == 201, user.text

    items = await _audit(client)
    by_type = {(e["object_type"], e["action"]) for e in items}
    assert ("department", "create") in by_type
    assert ("admin_user", "create") in by_type
    for e in items:
        assert _at(e) == FROZEN, e


async def test_provider_source_audit_uses_app_clock(
    frozen: FrozenClock, client: httpx.AsyncClient, seed: Seed
) -> None:
    dep = await seed.department("Аналитика")
    csrf = await admin_client_login(client, seed)
    resp = await client.post(
        "/api/v1/provider-sources",
        json={
            "provider": "anthropic",
            "kind": "workspace",
            "ref": "wrkspc_a",
            "department_id": str(dep),
            "mode": "import",
        },
        headers=csrf,
    )
    assert resp.status_code == 201, resp.text
    items = await _audit(client, object_type="provider_source")
    assert [e["action"] for e in items] == ["create"]
    assert _at(items[0]) == FROZEN


async def test_login_and_reveal_use_app_clock(
    frozen: FrozenClock, client: httpx.AsyncClient, seed: Seed
) -> None:
    dep = await seed.department("Маркетинг")
    emp = await seed.employee("ivanov", department=dep)
    await admin_client_login(client, seed)
    assert (await client.get(f"/api/v1/pseudonyms/{emp.pseudonym}")).status_code == 200

    items = await _audit(client)
    actions = {e["action"]: e for e in items}
    assert _at(actions["login"]) == FROZEN
    assert _at(actions["reveal_pseudonym"]) == FROZEN


async def test_forbidden_access_uses_app_clock(
    frozen: FrozenClock, client: httpx.AsyncClient, seed: Seed
) -> None:
    await admin_client_login(client, seed)
    async with session_as(client, seed, "fin", role="finance_viewer") as (fin, _):
        assert (await fin.get("/api/v1/audit", params=DAY)).status_code == 403
    items = await _audit(client)
    denied = [e for e in items if e["action"] == "forbidden_access"]
    assert len(denied) == 1 and denied[0]["actor"] == "fin"
    assert _at(denied[0]) == FROZEN


async def test_org_import_uses_app_clock(frozen: FrozenClock, client: httpx.AsyncClient, seed: Seed) -> None:
    await seed.department("Маркетинг", ad_group="CN=Marketing")
    csrf = await admin_client_login(client, seed)
    data = "login;display_name;group;is_primary\nivanov;Иванов;CN=Marketing;true\n".encode()
    resp = await client.post(
        "/api/v1/org/import",
        files={"file": ("ad.csv", data, "text/csv")},
        data={"dry_run": "false"},
        headers=csrf,
    )
    assert resp.status_code == 200, resp.text
    items = await _audit(client, object_type="org")
    assert len(items) == 1 and _at(items[0]) == FROZEN


async def test_audit_write_requires_explicit_time(db: asyncpg.Connection) -> None:
    """Время записи передаётся явно: вызов без `at` — ошибка программиста, а не тихий `now()` БД."""
    with pytest.raises(TypeError):
        await audit.write(db, actor="x", action="create", object_type="t")  # type: ignore[call-arg]

    async def run(_: asyncpg.Connection) -> Applied:
        return Applied(object_id="1")

    with pytest.raises(TypeError):
        await apply(db, Change("create", "t", run), "x")  # type: ignore[call-arg]
    assert await db.fetchval("SELECT count(*) FROM audit_log") == 0
