"""Помощники отказных тестов (T032, T093): драйвер стека, SQL-проверка, ожидание готовности, подготовка данных.

Драйвер стека (14_tech_rulings.md, пакет 3, 3Д1(в)) выбирается переменной BM_STACK:
  compose (по умолчанию) — `docker compose stop|start|kill`, SQL через `psql` в контейнере postgres;
  local — стек из локальных процессов (`deploy/local/stack.sh`, T087):
    stop  = stack.sh stop  — сигнал остановки главному процессу (как STOPSIGNAL образа: SIGTERM у api/worker,
            SIGINT у postgres), до 10 с ожидания, затем SIGKILL группы — как `docker stop` по умолчанию;
    kill  = stack.sh kill  — SIGKILL всей группы процессов сервиса — как `docker kill`;
    start = stack.sh start;
    SQL   — запрос к локальному кластеру по DATABASE_URL (asyncpg).
Остальные переменные окружения описаны в tests/failover/conftest.py.
"""

import asyncio
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
STACK = os.environ.get("BM_STACK", "compose")
if STACK not in ("compose", "local"):
    raise RuntimeError(f"BM_STACK={STACK!r}: допустимо compose или local")
LOCAL_SERVICES = ("postgres", "api", "worker")


# ---------------------------------------------------------------- переменные стека


def _env_file() -> Path:
    if os.environ.get("BM_ENV_FILE"):
        return Path(os.environ["BM_ENV_FILE"])
    if STACK == "local":
        return ROOT / "deploy" / "local" / ".state" / "stack.env"
    return ROOT / "deploy" / ".env"


def _dotenv() -> dict[str, str]:
    path = _env_file()
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------- compose


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


# ---------------------------------------------------------------- стек из локальных процессов


def local_stack_cmd() -> list[str]:
    script = os.environ.get("BM_LOCAL_STACK") or str(ROOT / "deploy" / "local" / "stack.sh")
    return ["bash", script]


def local(*args: str, timeout: float = 180) -> str:
    # скрипт — deploy/local/stack.sh (T087) или BM_LOCAL_STACK оператора; аргументы задаёт сам тест
    proc = subprocess.run(  # noqa: S603
        [*local_stack_cmd(), *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout
    )
    assert proc.returncode == 0, (
        f"stack.sh {' '.join(args)}: exit {proc.returncode}\n{proc.stdout[-1000:]}\n{proc.stderr[-2000:]}"
    )
    return proc.stdout


def local_status() -> dict[str, str]:
    """«postgres: running (pid …)» → {"postgres": "running", …}."""
    out: dict[str, str] = {}
    for line in local("status").splitlines():
        name, _, rest = line.partition(":")
        out[name.strip()] = rest.strip().split(" ", 1)[0]
    return out


# ---------------------------------------------------------------- операции над сервисами (оба драйвера)


def stop(*services: str) -> None:
    """Штатная остановка (`docker compose stop` / SIGTERM → 10 с → SIGKILL)."""
    if STACK == "compose":
        compose("stop", *services)
        return
    for svc in services:
        local("stop", svc)


def start(*services: str) -> None:
    if STACK == "compose":
        compose("start", *services)
        return
    for svc in services:
        local("start", svc)


def kill(service: str) -> None:
    """Принудительная остановка (`docker compose kill` / SIGKILL группы процессов)."""
    if STACK == "compose":
        compose("kill", service)
        return
    local("kill", service)


def _psql_compose(sql: str) -> list[list[str]]:
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


def _psql_local(sql: str) -> list[list[str]]:
    """SQL к локальному кластеру стека по DATABASE_URL (stack.sh env); общий кластер 5432 не используется."""
    import asyncpg

    dsn = os.environ.get("DATABASE_URL") or _dotenv().get("DATABASE_URL", "")
    assert dsn, 'BM_STACK=local: нет DATABASE_URL кластера стека (eval "$(deploy/local/stack.sh env)")'
    assert ":5432/" not in dsn, f"DATABASE_URL указывает на общий кластер 5432, а не на кластер стека: {dsn}"

    async def run() -> list[list[str]]:
        conn = await asyncpg.connect(dsn, timeout=10)
        try:
            rows = await conn.fetch(sql)
        finally:
            await conn.close()
        return [["" if v is None else str(v) for v in r.values()] for r in rows]

    return asyncio.run(run())


def psql(sql: str) -> list[list[str]]:
    return _psql_compose(sql) if STACK == "compose" else _psql_local(sql)


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
    """Предусловия: драйвер стека доступен, сервисы запущены, API отвечает на readyz. Иначе — FAIL, не skip."""
    if STACK == "local":
        script = Path(local_stack_cmd()[1])
        if not script.is_file():
            pytest.fail(f"нет {script} (T087): BM_STACK=local требует стека из локальных процессов")
        status = local_status()
        down = [s for s in LOCAL_SERVICES if status.get(s) != "running"]
        if down:
            pytest.fail(f"BM_STACK=local: не запущены {down} — сначала `make up-local`")
        wait_ready(30)
        return
    if shutil.which("docker") is None:
        pytest.fail("нет docker CLI: отказные тесты требуют Docker (или BM_STACK=local)")
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
    """Вход администратора. Временный пароль (первый вход) сменяется на BM_ADMIN_PASSWORD — как в smoke."""

    def __init__(self) -> None:
        env = _dotenv()
        login = os.environ.get("BM_ADMIN_LOGIN") or env.get("ADMIN_INITIAL_LOGIN", "admin")
        final = os.environ.get("BM_ADMIN_PASSWORD") or env.get("BM_ADMIN_PASSWORD", "")
        initial = env.get("ADMIN_INITIAL_PASSWORD", "")
        self.http = httpx.Client(base_url=BASE_URL, timeout=10)
        body = self._login(login, final) if final else None
        if body is None and initial:
            body = self._login(login, initial)
        assert body is not None, (
            f"вход {login!r} не удался ни с BM_ADMIN_PASSWORD, ни с ADMIN_INITIAL_PASSWORD из {_env_file()}"
        )
        if body["user"]["must_change_password"]:
            assert final and len(final) >= 12, (
                f"у {login!r} не сменён временный пароль: задайте BM_ADMIN_PASSWORD (не короче 12 символов)"
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
        assert r.status_code == 200, f"вход {login!r}: {r.status_code} {r.text[:200]}"
        data: dict[str, Any] = r.json()
        return data

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
