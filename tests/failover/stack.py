"""Помощники отказных тестов (T032): compose, psql в контейнере, ожидание готовности, подготовка данных.

Переменные окружения описаны в tests/failover/conftest.py.
"""

import os
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8080").rstrip("/")


# ---------------------------------------------------------------- стек и compose


def _dotenv() -> dict[str, str]:
    path = ROOT / "deploy" / ".env"
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def compose_cmd() -> list[str]:
    default = "docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
    return shlex.split(os.environ.get("BM_COMPOSE", default))


def compose(*args: str, timeout: float = 180, input_: str | None = None) -> str:
    # команда — из BM_COMPOSE оператора стенда или константы; аргументы задаёт сам тест
    proc = subprocess.run(  # noqa: S603
        [*compose_cmd(), *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout, input=input_
    )
    assert proc.returncode == 0, f"compose {' '.join(args)}: exit {proc.returncode}\n{proc.stderr[-2000:]}"
    return proc.stdout


def psql(sql: str) -> list[list[str]]:
    """SQL в контейнере postgres (сеть core — internal, с хоста БД недоступна)."""
    user = os.environ.get("BM_PG_USER", "bm")
    db = os.environ.get("BM_PG_DB", "bm")
    out = compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        user,
        "-d",
        db,
        "-At",
        "-F",
        "\t",
        "-v",
        "ON_ERROR_STOP=1",
        "-f",
        "-",
        input_=sql,
    )
    return [line.split("\t") for line in out.splitlines() if line]


def wait_ready(timeout_s: float = 120) -> None:
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/readyz", timeout=2)
            if r.status_code == 200:
                return
            last = f"{r.status_code} {r.text[:200]}"
        except httpx.HTTPError as e:
            last = repr(e)
        time.sleep(1)
    pytest.fail(f"стек не готов за {timeout_s} с ({BASE_URL}/readyz): {last}")


def check_stack() -> None:
    """Проверка предусловий: Docker доступен, compose-файл есть, API отвечает на readyz."""
    if shutil.which("docker") is None:
        pytest.fail("нет docker CLI: отказные тесты требуют Docker")
    # docker ищется в PATH, как в Makefile
    info = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=30)  # noqa: S607
    if info.returncode != 0:
        pytest.fail(f"Docker-демон недоступен: {info.stderr.strip()[:300]}")
    if not (ROOT / "deploy" / "docker-compose.yml").is_file():
        pytest.fail("нет deploy/docker-compose.yml (T087)")
    wait_ready(30)


# ---------------------------------------------------------------- данные через API админки


@dataclass
class Provisioned:
    run: str
    model: str
    departments: dict[str, str]  # fail_mode → department_id
    keys: dict[str, str]  # fail_mode → api_key сервисного ключа подразделения


class Admin:
    def __init__(self) -> None:
        env = _dotenv()
        login = os.environ.get("BM_ADMIN_LOGIN") or env.get("ADMIN_INITIAL_LOGIN", "admin")
        password = os.environ.get("BM_ADMIN_PASSWORD") or env.get("ADMIN_INITIAL_PASSWORD", "")
        self.http = httpx.Client(base_url=BASE_URL, timeout=10)
        r = self.http.post("/api/v1/auth/login", json={"login": login, "password": password})
        assert r.status_code == 200, (
            f"вход {login!r}: {r.status_code} {r.text[:200]} (задайте BM_ADMIN_LOGIN/PASSWORD)"
        )
        body = r.json()
        assert not body["user"]["must_change_password"], (
            f"у {login!r} не сменён начальный пароль: войдите в админку, смените его и задайте BM_ADMIN_PASSWORD"
        )
        self.csrf = {"X-CSRF-Token": body["csrf_token"]}

    def call(self, method: str, path: str, body: Any = None, expect: int = 200) -> Any:
        r = self.http.request(method, path, json=body, headers=self.csrf)
        assert r.status_code == expect, f"{method} {path}: {r.status_code} {r.text[:300]}"
        return r.json() if r.content else None


def provision(fail_modes: tuple[str, ...] = ("open", "closed")) -> Provisioned:
    """Модель и по подразделению на каждый fail_mode (enforce, большой лимит) + сервисный ключ на каждое."""
    run = uuid.uuid4().hex[:8]
    admin = Admin()
    model = f"failover-{run}"
    admin.call(
        "POST",
        "/api/v1/models",
        {
            "id": model,
            "provider": "internal",
            "kind": "chat",
            "supports_tools": True,
            "supports_json": True,
            "max_context": 8000,
            "default_input_tokens": 10,
            "default_output_tokens": 10,
            "price": {"input_per_1k": "1", "output_per_1k": "1", "currency": "RUB"},
        },
        expect=201,
    )
    departments: dict[str, str] = {}
    keys: dict[str, str] = {}
    for mode in fail_modes:
        dep = admin.call("POST", "/api/v1/departments", {"name": f"failover-{mode}-{run}"}, expect=201)["id"]
        admin.call(
            "PATCH",
            f"/api/v1/departments/{dep}",
            {"policy_mode": "enforce", "fail_mode": mode, "allowed_models": [model]},
        )
        admin.call(
            "PUT", f"/api/v1/departments/{dep}/limits", {"day": None, "week": None, "month": "1000000.00"}
        )
        caller = admin.call(
            "POST",
            "/api/v1/callers",
            {"name": f"failover-{mode}-{run}", "subject_scope": {"type": "service", "department_id": dep}},
            expect=201,
        )
        departments[mode] = dep
        keys[mode] = caller["api_key"]
    return Provisioned(run, model, departments, keys)


def report_counts(report_ids: list[str]) -> dict[str, int]:
    """Сколько раз каждый report_id записан в report_key (идемпотентность FR-009/FR-011)."""
    if not report_ids:
        return {}
    values = ",".join(f"('{uuid.UUID(rid)}'::uuid)" for rid in report_ids)  # только валидные UUID
    sql = f"SELECT v.id, count(k.report_id) FROM (VALUES {values}) AS v(id) "  # noqa: S608
    rows = psql(sql + "LEFT JOIN report_key k ON k.report_id = v.id GROUP BY v.id;")
    return {r[0]: int(r[1]) for r in rows}
