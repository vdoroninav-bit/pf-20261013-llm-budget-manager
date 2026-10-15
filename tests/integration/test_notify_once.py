"""Ядро уведомлений (T019): одно уведомление на ключ, лента в админке с правами по подразделению."""

import asyncio
import os
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest

from bm.notify.channels import InApp
from bm.notify.service import create_once
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db

PASSWORD = "correct-horse-battery"
OCT = date(2026, 10, 1)


# ---------------------------------------------------------------- create_once


async def test_create_once_is_idempotent_by_key(db: asyncpg.Connection, seed: Seed) -> None:
    dep = await seed.department()
    args = ("threshold_80", dep, "month", OCT, "Расход подразделения достиг 80% месячного лимита.")
    assert await create_once(db, *args) is True
    assert await create_once(db, *args) is False
    assert await db.fetchval("SELECT count(*) FROM notification") == 1


async def test_create_once_distinguishes_kind_period_and_start(db: asyncpg.Connection, seed: Seed) -> None:
    dep = await seed.department()
    assert await create_once(db, "threshold_80", dep, "month", OCT, "80%")
    assert await create_once(db, "threshold_100", dep, "month", OCT, "100%")
    assert await create_once(db, "threshold_80", dep, "week", date(2026, 10, 12), "80% недели")
    assert await create_once(db, "threshold_80", dep, "month", date(2026, 11, 1), "80% ноября")
    assert await db.fetchval("SELECT count(*) FROM notification") == 4


async def test_create_once_without_department(db: asyncpg.Connection) -> None:
    """Уведомления без подразделения (сбой импорта) — тоже одно на ключ: NULL в ключе равен NULL."""
    assert await create_once(
        db, "import_failure", None, None, date(2026, 10, 14), "Импорт Anthropic не удался 2 раза."
    )
    assert not await create_once(db, "import_failure", None, None, date(2026, 10, 14), "повтор")
    assert await create_once(db, "import_failure", None, None, date(2026, 10, 15), "на следующий день")


async def test_create_once_concurrent_single_winner(db: asyncpg.Connection, seed: Seed) -> None:
    dep = await seed.department()
    conns = [await asyncpg.connect(os.environ["DATABASE_URL"]) for _ in range(8)]
    try:
        results = await asyncio.gather(
            *(create_once(c, "threshold_100", dep, "day", date(2026, 10, 14), "100%") for c in conns)
        )
    finally:
        for c in conns:
            await c.close()
    assert sorted(results) == [False] * 7 + [True]


async def test_in_app_channel_is_the_row_itself(db: asyncpg.Connection, seed: Seed) -> None:
    dep = await seed.department()
    await create_once(db, "threshold_80", dep, "month", OCT, "80%")
    nid = await db.fetchval("SELECT id FROM notification")
    await InApp().deliver(db, nid)
    row = await db.fetchrow("SELECT webhook_status FROM notification WHERE id = $1", nid)
    assert row is not None and row["webhook_status"] == "disabled"


# ---------------------------------------------------------------- HTTP: listNotifications, markNotificationRead


async def _login(client: httpx.AsyncClient, login: str) -> dict[str, str]:
    resp = await client.post("/api/v1/auth/login", json={"login": login, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"X-CSRF-Token": resp.json()["csrf_token"]}


@pytest.fixture
async def world(db: asyncpg.Connection, seed: Seed) -> dict[str, Any]:
    dep_a = await seed.department("Маркетинг")
    dep_b = await seed.department("Продажи")
    await create_once(db, "threshold_80", dep_a, "month", OCT, "Маркетинг: 80% месячного лимита.")
    await create_once(db, "threshold_100", dep_b, "month", OCT, "Продажи: 100% месячного лимита.")
    await create_once(
        db, "import_failure", None, None, date(2026, 10, 14), "Импорт Anthropic не удался 2 раза подряд."
    )
    ids = {
        r["message"].split(":")[0]: r["id"] for r in await db.fetch("SELECT id, message FROM notification")
    }
    await seed.admin_user("boss", PASSWORD, role="admin")
    await seed.admin_user("owner_a", PASSWORD, role="budget_owner", department=dep_a)
    await seed.admin_user("fin", PASSWORD, role="finance_viewer")
    return {"dep_a": dep_a, "dep_b": dep_b, "ids": ids}


async def test_admin_sees_all_notifications(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    await _login(client, "boss")
    resp = await client.get("/api/v1/notifications")
    assert resp.status_code == 200, resp.text
    assert_matches_contract(resp, "listNotifications")
    body = resp.json()
    assert body["total"] == 3 and len(body["items"]) == 3
    assert all(item["read"] is False for item in body["items"])


async def test_budget_owner_sees_only_own_department(
    client: httpx.AsyncClient, world: dict[str, Any]
) -> None:
    await _login(client, "owner_a")
    resp = await client.get("/api/v1/notifications")
    assert resp.status_code == 200
    assert_matches_contract(resp, "listNotifications")
    items = resp.json()["items"]
    assert [i["department_id"] for i in items] == [str(world["dep_a"])]
    assert resp.json()["total"] == 1


async def test_finance_viewer_forbidden_and_audited(
    client: httpx.AsyncClient, world: dict[str, Any], db: asyncpg.Connection
) -> None:
    await _login(client, "fin")
    resp = await client.get("/api/v1/notifications")
    assert resp.status_code == 403
    assert_matches_contract(resp, "listNotifications")
    n = await db.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'forbidden_access' AND actor = 'fin' "
        "AND object_id = 'listNotifications'"
    )
    assert n == 1


async def test_mark_read_is_per_user_and_unread_filter(
    client: httpx.AsyncClient, world: dict[str, Any], seed: Seed
) -> None:
    hdr = await _login(client, "boss")
    nid = world["ids"]["Маркетинг"]
    resp = await client.post(f"/api/v1/notifications/{nid}/read", headers=hdr)
    assert resp.status_code == 204
    assert_matches_contract(resp, "markNotificationRead")
    resp = await client.post(f"/api/v1/notifications/{nid}/read", headers=hdr)  # повтор — тоже 204
    assert resp.status_code == 204
    unread = (await client.get("/api/v1/notifications", params={"unread": "true"})).json()
    assert unread["total"] == 2 and str(nid) not in {i["id"] for i in unread["items"]}
    all_items = (await client.get("/api/v1/notifications")).json()["items"]
    assert {i["id"]: i["read"] for i in all_items}[str(nid)] is True
    # Другой пользователь своё «прочитано» не получил.
    async with httpx.AsyncClient(transport=client._transport, base_url=str(client.base_url)) as other:
        await _login(other, "owner_a")
        items = (await other.get("/api/v1/notifications")).json()["items"]
        assert items[0]["read"] is False


async def test_pagination(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    await _login(client, "boss")
    resp = await client.get("/api/v1/notifications", params={"page": 2, "page_size": 2})
    assert resp.status_code == 200
    assert resp.json()["total"] == 3 and len(resp.json()["items"]) == 1
    assert (await client.get("/api/v1/notifications", params={"page_size": 201})).status_code == 422


async def test_budget_owner_cannot_mark_foreign(
    client: httpx.AsyncClient, world: dict[str, Any], db: asyncpg.Connection
) -> None:
    hdr = await _login(client, "owner_a")
    for key in ("Продажи", "Импорт Anthropic не удался 2 раза подряд."):
        nid: UUID = world["ids"][key]
        resp = await client.post(f"/api/v1/notifications/{nid}/read", headers=hdr)
        assert resp.status_code == 403, key
        assert_matches_contract(resp, "markNotificationRead")
    n = await db.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'forbidden_access' AND object_id = 'markNotificationRead'"
    )
    assert n == 2
    assert await db.fetchval("SELECT count(*) FROM notification WHERE cardinality(read_by) > 0") == 0


async def test_mark_read_unknown_is_404(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    hdr = await _login(client, "boss")
    resp = await client.post(f"/api/v1/notifications/{uuid4()}/read", headers=hdr)
    assert resp.status_code == 404
    assert_matches_contract(resp, "markNotificationRead")


async def test_mark_read_requires_csrf(client: httpx.AsyncClient, world: dict[str, Any]) -> None:
    await _login(client, "boss")
    resp = await client.post(f"/api/v1/notifications/{world['ids']['Маркетинг']}/read")
    assert resp.status_code == 403
    assert_matches_contract(resp, "markNotificationRead")
