"""Вход, сессия, CSRF, блокировка, первый администратор, аудит отказов (T016, FR-030, FR-031)."""

from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import APIRouter

from bm.access.authorize import Principal
from bm.clock import FrozenClock
from bm.http.deps import AdminSession
from tests.conftest import BOOTSTRAP_ADMIN
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db

PASSWORD = "correct-horse-battery"


async def _login(client: httpx.AsyncClient, login: str, password: str = PASSWORD) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"login": login, "password": password})


async def _logged_in(client: httpx.AsyncClient, login: str) -> str:
    resp = await _login(client, login)
    assert resp.status_code == 200, resp.text
    return str(resp.json()["csrf_token"])


# ---------------------------------------------------------------- первый администратор


async def test_initial_admin_created_with_must_change_password(app: Any, db: asyncpg.Connection) -> None:
    rows = await db.fetch("SELECT login, role, must_change_password, argon2_hash FROM admin_user")
    assert len(rows) == 1
    row = rows[0]
    assert (row["login"], row["role"], row["must_change_password"]) == (BOOTSTRAP_ADMIN[0], "admin", True)
    assert row["argon2_hash"].startswith("$argon2id$")
    assert BOOTSTRAP_ADMIN[1] not in row["argon2_hash"]


async def test_initial_admin_not_recreated_on_restart(app: Any, db: asyncpg.Connection) -> None:
    async with app.router.lifespan_context(app):
        pass
    assert await db.fetchval("SELECT count(*) FROM admin_user") == 1


async def test_initial_admin_can_log_in_and_must_change_password(client: httpx.AsyncClient) -> None:
    resp = await _login(client, BOOTSTRAP_ADMIN[0], BOOTSTRAP_ADMIN[1])
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["must_change_password"] is True
    assert_matches_contract(resp, "login")


# ---------------------------------------------------------------- вход и сессия


async def test_login_sets_strict_httponly_cookie_and_returns_session(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    await seed.admin_user("anna", PASSWORD, role="finance_viewer")
    resp = await _login(client, "anna")
    assert resp.status_code == 200, resp.text
    assert_matches_contract(resp, "login")
    body = resp.json()
    assert body["user"]["login"] == "anna" and body["user"]["role"] == "finance_viewer"
    assert body["csrf_token"]
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith("bm_session=")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
    token = cookie.split(";", 1)[0].split("=", 1)[1]
    stored = await db.fetchval("SELECT count(*) FROM session WHERE token_sha256 = $1", token.encode())
    assert stored == 0  # в БД — только хэш токена
    assert await db.fetchval("SELECT count(*) FROM audit_log WHERE action = 'login' AND actor = 'anna'") == 1


async def test_me_requires_session(client: httpx.AsyncClient, seed: Seed) -> None:
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401 and resp.json()["code"] == "unauthorized"
    assert_matches_contract(resp, "me")
    await seed.admin_user("anna", PASSWORD)
    csrf = await _logged_in(client, "anna")
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["csrf_token"] == csrf
    assert_matches_contract(resp, "me")


async def test_session_expires(client: httpx.AsyncClient, seed: Seed, clock: FrozenClock) -> None:
    await seed.admin_user("anna", PASSWORD)
    await _logged_in(client, "anna")
    clock.advance(hours=13)
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert_matches_contract(resp, "me")


@pytest.mark.parametrize("login, password", [("anna", "wrong-password"), ("nobody", PASSWORD)])
async def test_invalid_credentials(client: httpx.AsyncClient, seed: Seed, login: str, password: str) -> None:
    await seed.admin_user("anna", PASSWORD)
    resp = await _login(client, login, password)
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_credentials"
    assert "set-cookie" not in resp.headers
    assert_matches_contract(resp, "login")


async def test_inactive_user_cannot_log_in(client: httpx.AsyncClient, seed: Seed) -> None:
    await seed.admin_user("anna", PASSWORD, active=False)
    resp = await _login(client, "anna")
    assert resp.status_code == 401
    assert_matches_contract(resp, "login")


async def test_login_extra_fields_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/login", json={"login": "a", "password": "b", "remember": True})
    assert resp.status_code == 422


# ---------------------------------------------------------------- блокировка


async def test_lock_after_10_failures_for_15_minutes(
    client: httpx.AsyncClient, seed: Seed, clock: FrozenClock, db: asyncpg.Connection
) -> None:
    await seed.admin_user("anna", PASSWORD)
    for _ in range(10):
        assert (await _login(client, "anna", "wrong")).status_code == 401
    resp = await _login(client, "anna")  # даже верный пароль
    assert resp.status_code == 429
    assert resp.json()["code"] == "login_locked"
    assert_matches_contract(resp, "login")
    clock.advance(minutes=14)
    assert (await _login(client, "anna")).status_code == 429
    clock.advance(minutes=1, seconds=1)
    assert (await _login(client, "anna")).status_code == 200
    assert await db.fetchval("SELECT failed_attempts FROM admin_user WHERE login = 'anna'") == 0


async def test_success_resets_failure_counter(client: httpx.AsyncClient, seed: Seed) -> None:
    await seed.admin_user("anna", PASSWORD)
    for _ in range(9):
        await _login(client, "anna", "wrong")
    assert (await _login(client, "anna")).status_code == 200
    for _ in range(9):
        assert (await _login(client, "anna", "wrong")).status_code == 401
    assert (await _login(client, "anna")).status_code == 200


# ---------------------------------------------------------------- выход и CSRF


async def test_logout_requires_csrf(client: httpx.AsyncClient, seed: Seed) -> None:
    await seed.admin_user("anna", PASSWORD)
    csrf = await _logged_in(client, "anna")
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 403
    assert_matches_contract(resp, "logout")
    resp = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "forged"})
    assert resp.status_code == 403
    assert_matches_contract(resp, "logout")
    resp = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 204
    assert_matches_contract(resp, "logout")
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert_matches_contract(resp, "me")


# ---------------------------------------------------------------- смена пароля


async def test_change_password(client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection) -> None:
    await seed.admin_user("anna", PASSWORD, must_change_password=True)
    csrf = await _logged_in(client, "anna")
    hdr = {"X-CSRF-Token": csrf}
    url = "/api/v1/auth/password"
    resp = await client.post(
        url, json={"old_password": "wrong", "new_password": "new-password-123"}, headers=hdr
    )
    assert resp.status_code == 422 and resp.json()["code"] == "invalid_credentials"
    assert_matches_contract(resp, "changePassword")
    resp = await client.post(url, json={"old_password": PASSWORD, "new_password": "short"}, headers=hdr)
    assert resp.status_code == 422
    assert_matches_contract(resp, "changePassword")
    resp = await client.post(
        url, json={"old_password": PASSWORD, "new_password": "new-password-123"}, headers=hdr
    )
    assert resp.status_code == 204
    assert_matches_contract(resp, "changePassword")
    assert await db.fetchval("SELECT must_change_password FROM admin_user WHERE login = 'anna'") is False
    assert (await client.get("/api/v1/auth/me")).status_code == 200  # текущая сессия жива
    async with httpx.AsyncClient(transport=client._transport, base_url=str(client.base_url)) as other:
        resp = await _login(other, "anna")
        assert resp.status_code == 401
        assert_matches_contract(resp, "login")
        assert (await _login(other, "anna", "new-password-123")).status_code == 200
    n = await db.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'change_password' AND actor = 'anna'"
    )
    assert n == 1


async def test_change_password_closes_other_sessions(client: httpx.AsyncClient, seed: Seed) -> None:
    await seed.admin_user("anna", PASSWORD)
    async with httpx.AsyncClient(transport=client._transport, base_url=str(client.base_url)) as other:
        await _logged_in(other, "anna")
        csrf = await _logged_in(client, "anna")
        resp = await client.post(
            "/api/v1/auth/password",
            json={"old_password": PASSWORD, "new_password": "new-password-123"},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 204
        resp = await other.get("/api/v1/auth/me")
        assert resp.status_code == 401
        assert_matches_contract(resp, "me")


# ---------------------------------------------------------------- роли и аудит отказов


def _probe_router() -> APIRouter:
    """Маршрут с operationId=listAudit (только admin) — проверка общей зависимости прав."""
    r = APIRouter()

    @r.get("/__probe/audit", operation_id="listAudit")
    async def probe(principal: AdminSession) -> dict[str, str]:
        p: Principal = principal.principal
        return {"login": p.login}

    return r


@pytest.fixture
async def probe(app: Any) -> Any:
    app.include_router(_probe_router())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8080") as c:
        yield c


async def test_forbidden_role_gets_403_and_audit(
    probe: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    await seed.admin_user("fin", PASSWORD, role="finance_viewer")
    await _logged_in(probe, "fin")
    resp = await probe.get("/__probe/audit")
    assert resp.status_code == 403 and resp.json()["code"] == "forbidden"
    row = await db.fetchrow(
        "SELECT actor, object_type, object_id FROM audit_log WHERE action = 'forbidden_access'"
    )
    assert row is not None and (row["actor"], row["object_type"], row["object_id"]) == (
        "fin",
        "operation",
        "listAudit",
    )


async def test_allowed_role_passes(probe: httpx.AsyncClient, seed: Seed) -> None:
    await seed.admin_user("boss", PASSWORD, role="admin")
    await _logged_in(probe, "boss")
    resp = await probe.get("/__probe/audit")
    assert resp.status_code == 200 and resp.json() == {"login": "boss"}


async def test_must_change_password_blocks_other_operations(probe: httpx.AsyncClient, seed: Seed) -> None:
    await seed.admin_user("boss", PASSWORD, role="admin", must_change_password=True)
    await _logged_in(probe, "boss")
    resp = await probe.get("/__probe/audit")
    assert resp.status_code == 403 and resp.json()["code"] == "password_change_required"
    assert (await probe.get("/api/v1/auth/me")).status_code == 200


async def test_must_change_password_on_real_admin_route(client: httpx.AsyncClient, seed: Seed) -> None:
    """Контракт 1.0.2 (14_tech_rulings.md Б1): 403 password_change_required объявлен у операций админки."""
    await seed.admin_user("boss", PASSWORD, role="admin", must_change_password=True)
    await _logged_in(client, "boss")
    resp = await client.get("/api/v1/departments")
    assert resp.status_code == 403 and resp.json()["code"] == "password_change_required"
    assert_matches_contract(resp, "listDepartments")
