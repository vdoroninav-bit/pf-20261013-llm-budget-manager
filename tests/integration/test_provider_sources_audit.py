"""T062, первый шаг (14_tech_rulings.md, пакет 3, 3Б6; FR-031): изменения справочника источников провайдеров
пишутся в аудит «было/стало» с временем часов приложения и поднимают `config_version`.
"""

from datetime import datetime
from typing import Any

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.qa import admin_client_login
from tests.fixtures.seed import Seed

pytestmark = [pytest.mark.db]


async def _version(db: asyncpg.Connection) -> int:
    v: int = await db.fetchval("SELECT version FROM config_version")
    return v


async def test_provider_source_create_update_delete_are_audited(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    dep = await seed.department("Аналитика")
    csrf = await admin_client_login(client, seed)
    v0 = await _version(db)

    created = await client.post(
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
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    moved = await client.patch(f"/api/v1/provider-sources/{sid}", json={"mode": "decision_api"}, headers=csrf)
    assert moved.status_code == 200
    gone = await client.delete(f"/api/v1/provider-sources/{sid}", headers=csrf)
    assert gone.status_code == 204
    assert await _version(db) == v0 + 3

    rows = await db.fetch(
        "SELECT at, actor, action, before, after FROM audit_log "
        "WHERE object_type = 'provider_source' AND object_id = $1 ORDER BY id",
        sid,
    )
    assert [r["action"] for r in rows] == ["create", "update", "delete"]
    now: datetime = clock.now()
    assert all(r["actor"] == "root" and r["at"] == now for r in rows)  # время — часы приложения (3Б7)
    create, update, delete = rows
    assert create["before"] is None and '"import"' in create["after"]
    assert '"import"' in update["before"] and '"decision_api"' in update["after"]
    assert '"decision_api"' in delete["before"] and delete["after"] is None

    audit = await client.get(
        "/api/v1/audit", params={"from": "2026-10-14", "to": "2026-10-14", "object_type": "provider_source"}
    )
    assert audit.status_code == 200, audit.text
    assert_matches_contract(audit, "listAudit")
    assert audit.json()["total"] == 3


async def test_provider_source_rejected_change_is_not_audited(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep = await seed.department("Аналитика")
    csrf = await admin_client_login(client, seed)
    v0 = await _version(db)
    body: dict[str, Any] = {
        "provider": "anthropic",
        "kind": "api_key",
        "ref": "k1",
        "department_id": str(dep),
        "mode": "import",
    }
    resp = await client.post("/api/v1/provider-sources", json=body, headers=csrf)
    assert resp.status_code == 422
    assert await _version(db) == v0
    assert await db.fetchval("SELECT count(*) FROM audit_log WHERE object_type = 'provider_source'") == 0
