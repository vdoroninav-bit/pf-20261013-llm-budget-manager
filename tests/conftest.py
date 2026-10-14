"""Общие фикстуры тестов (12_plan.md §5.2, 12_tasks.md T014, T017).

- `db`    — соединение asyncpg с чистой схемой: схема `public` пересоздаётся и мигрируется на каждый тест.
            Строка подключения — DATABASE_URL (make test подставляет её сам; имя БД обязано содержать «test»).
- `clock` — FrozenClock на 2026-10-14 12:00 МСК; двигается методами `set()` / `advance()`.
- `seed`  — построитель тестовых данных прямыми INSERT (tests/fixtures/seed.py).
- `egress_guard` (autouse) — подмена `socket.socket.connect`: разрешён только хост и порт PostgreSQL
            из DATABASE_URL и unix-сокеты; любая другая попытка соединения роняет тест (принцип IV, T017).
"""

import os
import socket
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import asyncpg
import pytest

from bm.clock import FrozenClock
from bm.domain.periods import MSK
from tests.fixtures.seed import Seed

DATABASE_URL = os.environ.get("DATABASE_URL", "")
START = datetime(2026, 10, 14, 12, 0, tzinfo=MSK)


def _db_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/").split("?")[0]


@pytest.fixture
async def db() -> AsyncIterator[asyncpg.Connection]:
    if not DATABASE_URL:
        pytest.skip("DATABASE_URL не задан: тест требует PostgreSQL (make test)")
    if "test" not in _db_name(DATABASE_URL) and os.environ.get("ALLOW_ANY_TEST_DB") != "1":
        pytest.fail(f"имя БД {_db_name(DATABASE_URL)!r} не содержит 'test': фикстура пересоздаёт схему")
    from bm.db.migrate import migrate

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        await migrate(conn)
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(START)


@pytest.fixture
def seed(db: asyncpg.Connection) -> Seed:
    return Seed(db)


# ------------------------------------------------------------------ T015: приложение и HTTP-клиент

TEST_SESSION_SECRET = "test-session-secret-0123456789abcdef"
BOOTSTRAP_ADMIN = ("bootstrap-admin", "bootstrap-password-123")


@pytest.fixture
def settings() -> Any:
    """Настройки приложения для тестов: без файла .env, импорт выключен, первый админ — `bootstrap-admin`."""
    from bm.settings import Settings

    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=DATABASE_URL or "postgresql://nobody@127.0.0.1:1/none_test",
        session_secret=TEST_SESSION_SECRET,
        admin_initial_login=BOOTSTRAP_ADMIN[0],
        admin_initial_password=BOOTSTRAP_ADMIN[1],
        import_enabled=False,
    )


@pytest.fixture
async def app(db: asyncpg.Connection, settings: Any, clock: FrozenClock) -> AsyncIterator[Any]:
    """Приложение FastAPI с выполненным lifespan (пул к той же тестовой БД, что и `db`)."""
    from bm.main import create_app

    application = create_app(settings, clock=clock)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: Any) -> AsyncIterator[Any]:
    """httpx-клиент поверх ASGI: без сети, базовый URL как в `servers` контракта."""
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8080") as c:
        yield c


# ------------------------------------------------------------------ T017: ноль исходящих соединений


class EgressAttempt(ConnectionRefusedError):
    """Попытка исходящего соединения в тесте (кроме PostgreSQL)."""


def _allowed_endpoints() -> set[tuple[str, int]]:
    if not DATABASE_URL:
        return set()
    parts = urlsplit(DATABASE_URL)
    host, port = parts.hostname or "localhost", parts.port or 5432
    allowed = {(host, port)}
    try:
        for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
            allowed.add((str(info[4][0]), port))
    except OSError:
        pass
    return allowed


ALLOWED_ENDPOINTS = _allowed_endpoints()


@pytest.fixture(autouse=True)
def egress_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """Любое соединение, кроме PostgreSQL и unix-сокетов, записывается и отклоняется; тест падает."""
    attempts: list[Any] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def check(sock: socket.socket, address: Any) -> None:
        if sock.family == getattr(socket, "AF_UNIX", None):
            return
        host, port = address[0], address[1]
        if (str(host), int(port)) not in ALLOWED_ENDPOINTS:
            attempts.append(address)
            raise EgressAttempt(f"исходящее соединение запрещено в тестах: {address!r}")

    def guarded_connect(sock: socket.socket, address: Any) -> Any:
        check(sock, address)
        return real_connect(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: Any) -> Any:
        check(sock, address)
        return real_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    yield attempts
    if attempts:
        pytest.fail(f"тест пытался открыть исходящие соединения: {attempts!r}")


@pytest.fixture
def expect_egress_attempts(egress_guard: list[Any]) -> Iterator[list[Any]]:
    """Для тестов самого guard: попытки ожидаемы, тест сам проверяет список."""
    yield egress_guard
    egress_guard.clear()
