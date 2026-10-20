"""Каркас приложения (T015): /healthz, /readyz, /metrics и ошибки application/problem+json."""

from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from bm.http.errors import ProblemError
from tests.contract.helpers import assert_matches_contract

pytestmark = pytest.mark.db


async def test_healthz(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert_matches_contract(resp, "healthz")


async def test_readyz_ok_when_db_and_migrations_ready(client: httpx.AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok" and body["db"] == "ok"
    assert body["migrations"].startswith("0001")
    assert_matches_contract(resp, "readyz")


async def test_readyz_503_when_migrations_missing(client: httpx.AsyncClient, db: asyncpg.Connection) -> None:
    await db.execute("DELETE FROM schema_migrations")
    resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["status"] == "fail"
    assert_matches_contract(resp, "readyz")


async def test_metrics_prometheus_text(client: httpx.AsyncClient) -> None:
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain; version=0.0.4")
    for name in (
        "decision_latency_seconds",
        "decisions_total",
        "reports_total",
        "import_lag_hours",
        "job_failures_total",
    ):
        assert name in resp.text, name
    assert "reservations_open" not in resp.text  # убрана (14_tech_rulings.md, пакет 2, В1-6)
    assert_matches_contract(resp, "metrics")


async def test_unknown_route_is_problem_json(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/no-such-thing")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["code"] == "not_found" and body["status"] == 404
    assert any("а" <= ch <= "я" for ch in body["detail"].lower()), body["detail"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


def _probe_router() -> APIRouter:
    r = APIRouter()

    @r.post("/__probe/validate")
    async def validate(body: _Body) -> dict[str, str]:
        return {"name": body.name}

    @r.get("/__probe/problem")
    async def problem() -> None:
        raise ProblemError(409, "conflict", "Такая запись уже есть.")

    @r.get("/__probe/crash")
    async def crash() -> None:
        raise RuntimeError("секретная деталь, которой не место в ответе")

    return r


@pytest.fixture
def probe_app(app: Any) -> Any:
    app.include_router(_probe_router())
    return app


@pytest.fixture
async def probe(probe_app: Any) -> Any:
    transport = httpx.ASGITransport(app=probe_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8080") as c:
        yield c


async def test_validation_error_is_problem_422_in_russian(probe: httpx.AsyncClient) -> None:
    resp = await probe.post("/__probe/validate", json={"name": "x", "prompt": "текст запроса"})
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["code"] == "validation_error"
    assert "prompt" in body["detail"]
    assert "текст запроса" not in resp.text  # содержимое не эхоится (принцип I)


async def test_problem_error_rendered(probe: httpx.AsyncClient) -> None:
    resp = await probe.get("/__probe/problem")
    assert resp.status_code == 409
    assert resp.json() == {
        "type": "about:blank",
        "title": "Conflict",
        "status": 409,
        "code": "conflict",
        "detail": "Такая запись уже есть.",
    }


async def test_unhandled_error_is_500_without_details(probe: httpx.AsyncClient) -> None:
    resp = await probe.get("/__probe/crash")
    assert resp.status_code == 500
    assert resp.json()["code"] == "internal"
    assert "секретная" not in resp.text
