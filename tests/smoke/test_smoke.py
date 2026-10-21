"""Smoke поставки (T087, 12_plan.md §8): стек отвечает, ключевой сценарий проходит от входа до уменьшения остатка.

Сценарий: вход администратора → модель, подразделение, лимит, ключ вызывающей системы → решение allow →
отчёт о расходе → в следующем решении остаток меньше на сумму отчёта. Каждый прогон создаёт свои объекты
с уникальным суффиксом, поэтому smoke можно повторять (в том числе после make restore).

Переменные и режимы — в tests/smoke/conftest.py.
"""

import json
import os
import shlex
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
STACK = os.environ.get("BM_STACK", "compose")


def _env_file() -> Path:
    explicit = os.environ.get("BM_ENV_FILE")
    if explicit:
        return Path(explicit)
    if STACK == "local":
        return ROOT / "deploy" / "local" / ".state" / "stack.env"
    return ROOT / "deploy" / ".env"


def _dotenv() -> dict[str, str]:
    out: dict[str, str] = {}
    path = _env_file()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


ENV = _dotenv()
BASE_URL = (os.environ.get("BASE_URL") or ENV.get("BASE_URL") or "http://127.0.0.1:8080").rstrip("/")


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    default = "docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
    cmd = [*shlex.split(os.environ.get("BM_COMPOSE", default)), *args]
    # команда — из BM_COMPOSE оператора стенда или константы; аргументы задаёт сам тест
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)  # noqa: S603
    if check:
        assert proc.returncode == 0, f"{' '.join(cmd)}: exit {proc.returncode}\n{proc.stderr[-2000:]}"
    return proc


@pytest.fixture(scope="module")
def http() -> Any:
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        yield client


# ---------------------------------------------------------------- системные операции


def test_healthz(http: httpx.Client) -> None:
    r = http.get("/healthz")
    assert r.status_code == 200, f"{BASE_URL}/healthz: {r.status_code} {r.text[:200]}"
    assert r.json()["status"] == "ok"


def test_readyz_db_and_migrations(http: httpx.Client) -> None:
    r = http.get("/readyz")
    assert r.status_code == 200, f"/readyz: {r.status_code} {r.text[:300]}"
    body = r.json()
    assert body["status"] == "ok" and body["db"] == "ok", body


def test_metrics_exposed(http: httpx.Client) -> None:
    r = http.get("/metrics")
    assert r.status_code == 200
    assert "decision_latency_seconds" in r.text


# ---------------------------------------------------------------- ключевой сценарий


class Admin:
    def __init__(self, http: httpx.Client) -> None:
        self.http = http
        login = os.environ.get("BM_ADMIN_LOGIN") or ENV.get("ADMIN_INITIAL_LOGIN", "admin")
        final = os.environ.get("BM_ADMIN_PASSWORD") or ENV.get("BM_ADMIN_PASSWORD", "")
        initial = ENV.get("ADMIN_INITIAL_PASSWORD", "")
        body = self._login(login, final) if final else None
        if body is None:
            body = self._login(login, initial) if initial else None
            assert body is not None, (
                f"вход {login!r} не удался ни с BM_ADMIN_PASSWORD, ни с ADMIN_INITIAL_PASSWORD из {_env_file()}"
            )
        if body["user"]["must_change_password"]:
            assert final and len(final) >= 12, (
                "временный пароль администратора не сменён: задайте BM_ADMIN_PASSWORD (не короче 12 символов) — "
                "smoke сменит временный пароль на него, как администратор при первом входе"
            )
            r = self.http.post(
                "/api/v1/auth/password",
                json={"old_password": initial, "new_password": final},
                headers={"X-CSRF-Token": body["csrf_token"]},
            )
            assert r.status_code == 204, f"смена временного пароля: {r.status_code} {r.text[:300]}"
        self.csrf = {"X-CSRF-Token": body["csrf_token"]}

    def _login(self, login: str, password: str) -> dict[str, Any] | None:
        r = self.http.post("/api/v1/auth/login", json={"login": login, "password": password})
        if r.status_code == 401:
            return None
        assert r.status_code == 200, f"вход {login!r}: {r.status_code} {r.text[:300]}"
        return r.json()

    def call(self, method: str, path: str, body: Any = None, expect: int = 200) -> Any:
        r = self.http.request(method, path, json=body, headers=self.csrf)
        assert r.status_code == expect, f"{method} {path}: {r.status_code} {r.text[:300]}"
        return r.json() if r.content else None


def test_key_scenario_decision_and_usage(http: httpx.Client) -> None:
    admin = Admin(http)
    run = uuid.uuid4().hex[:8]
    model = f"smoke-{run}"
    admin.call(
        "POST",
        "/api/v1/models",
        {
            "id": model,
            "provider": "internal",
            "kind": "chat",
            "supports_tools": False,
            "supports_json": False,
            "max_context": 8000,
            "default_input_tokens": 1000,
            "default_output_tokens": 0,
            "price": {"input_per_1k": "100", "output_per_1k": "100", "currency": "RUB"},
        },
        expect=201,
    )
    dep = admin.call("POST", "/api/v1/departments", {"name": f"smoke-{run}"}, expect=201)["id"]
    admin.call("PATCH", f"/api/v1/departments/{dep}", {"policy_mode": "enforce", "allowed_models": [model]})
    admin.call("PUT", f"/api/v1/departments/{dep}/limits", {"day": None, "week": None, "month": "100000.00"})
    caller = admin.call(
        "POST",
        "/api/v1/callers",
        {"name": f"smoke-{run}", "subject_scope": {"type": "service", "department_id": dep}},
        expect=201,
    )
    key = {"X-Api-Key": caller["api_key"]}
    request = {"subject": {"type": "service"}, "model": model, "kind": "chat", "input_tokens": 1000}

    r = http.post("/api/v1/decisions", json=request, headers=key)
    assert r.status_code == 200, f"решение: {r.status_code} {r.text[:300]}"
    first = r.json()
    assert first["outcome"] == "allow", first
    assert first["applied_limit"] is not None, first

    report = {
        "report_id": str(uuid.uuid4()),
        "decision_id": first["decision_id"],
        "tokens": {"input": 1000, "output": 0},
        "call_status": "ok",
    }
    r = http.post("/api/v1/usage", json=report, headers=key)
    assert r.status_code == 200, f"отчёт: {r.status_code} {r.text[:300]}"
    usage = r.json()
    assert usage["status"] == "charged", usage
    amount = Decimal(usage["amount_rub"])
    assert amount == Decimal("100.00"), usage

    r = http.post("/api/v1/decisions", json=request, headers=key)
    assert r.status_code == 200
    second = r.json()
    assert second["outcome"] == "allow", second
    before = Decimal(first["applied_limit"]["remaining_rub"])
    after = Decimal(second["applied_limit"]["remaining_rub"])
    assert after < before, f"остаток не уменьшился: было {before}, стало {after}"
    assert before - after == amount, f"остаток уменьшился на {before - after}, а расход {amount}"


# ---------------------------------------------------------------- только образ и compose


@pytest.mark.compose_only
def test_admin_spa_served_from_image(http: httpx.Client) -> None:
    """Стадия node образа собирает SPA, python-runtime отдаёт его из /app/static/admin по /admin/."""
    r = http.get("/admin/")
    assert r.status_code == 200, f"/admin/: {r.status_code} {r.text[:200]}"
    assert r.headers["content-type"].startswith("text/html")
    assert '<div id="root">' in r.text


@pytest.mark.compose_only
def test_services_healthy() -> None:
    out = compose("ps", "--format", "json").stdout
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    if len(rows) == 1 and isinstance(rows[0], list):  # старые версии compose — один JSON-массив
        rows = rows[0]
    health = {row["Service"]: row.get("Health", "") for row in rows}
    assert set(health) >= {"api", "worker", "postgres", "backup"}, health
    assert all(h == "healthy" for h in health.values()), health


@pytest.mark.compose_only
@pytest.mark.parametrize("service", ["api", "worker"])
def test_runs_as_uid_10001(service: str) -> None:
    uid = compose("exec", "-T", service, "id", "-u").stdout.strip()
    assert uid == "10001", f"{service}: uid {uid}"


@pytest.mark.compose_only
def test_metrics_dir_only_at_api() -> None:
    api = compose("exec", "-T", "api", "printenv", "PROMETHEUS_MULTIPROC_DIR").stdout.strip()
    assert api == "/run/bm-metrics"
    worker = compose("exec", "-T", "worker", "printenv", "PROMETHEUS_MULTIPROC_DIR", check=False)
    assert worker.returncode != 0 and not worker.stdout.strip(), "у worker не должно быть каталога метрик"


@pytest.mark.compose_only
def test_postgres_not_reachable_from_outside_core() -> None:
    """postgres только в сети core (internal): порт на хост не публикуется."""
    out = compose("port", "postgres", "5432", check=False)
    assert out.returncode != 0 or not out.stdout.strip(), f"порт postgres опубликован: {out.stdout}"
