"""Smoke против поднятого стека (T087, 12_plan.md §8): `make up && make smoke` или `make up-local && make smoke`.

Цель `smoke` ставит BM_SMOKE=1. Без неё (обычный `pytest`, `make test`) тесты пропускаются с причиной.
Если BM_SMOKE=1, а стека нет, тест ПАДАЕТ: гейт релиза не должен молча зеленеть.

Переменные окружения:
  BM_STACK           compose (по умолчанию) | local — стек из локальных процессов (deploy/local/stack.sh)
  BASE_URL           адрес API (compose: http://127.0.0.1:8080; local: из deploy/local/.state/stack.env)
  BM_ENV_FILE        файл переменных стека (compose: deploy/.env; local: deploy/local/.state/stack.env)
  BM_ADMIN_LOGIN     логин администратора (по умолчанию ADMIN_INITIAL_LOGIN из BM_ENV_FILE)
  BM_ADMIN_PASSWORD  пароль администратора ПОСЛЕ смены. При первом входе (временный пароль ADMIN_INITIAL_PASSWORD)
                     smoke сменяет временный пароль на этот — так же, как администратор при первом входе
  BM_COMPOSE         команда compose для проверок compose_only
                     (по умолчанию «docker compose -f deploy/docker-compose.yml --env-file deploy/.env»)

Проверки, которые есть только у образа и compose (SPA из /app/static/admin, uid 10001, HEALTHCHECK, сети),
помечены `compose_only` и в режиме local пропускаются с причиной (14_tech_rulings.md, пакет 3, 3Д1(в)).
"""

import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENABLED = os.environ.get("BM_SMOKE") == "1"
STACK = os.environ.get("BM_STACK", "compose")
SKIP_REASON = "smoke требует поднятого стека: `make up && make smoke` или `make up-local && make smoke`"
LOCAL_SKIP = (
    "compose_only: проверка есть только у образа и docker compose; на стеке из локальных процессов "
    "(BM_STACK=local) не применима — проверяется в CI (T092) или на стенде заказчика"
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "compose_only: проверка образа/compose, в BM_STACK=local пропускается")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    mine = [i for i in items if here in Path(str(i.fspath)).parents]
    if not ENABLED:
        for item in mine:
            item.add_marker(pytest.mark.skip(reason=SKIP_REASON))
        return
    if STACK == "local":
        for item in mine:
            if item.get_closest_marker("compose_only"):
                item.add_marker(pytest.mark.skip(reason=LOCAL_SKIP))


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def egress_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """Переопределяет корневой guard: стек опубликован на loopback; всё остальное по-прежнему запрещено."""
    attempts: list[Any] = []
    real_connect = socket.socket.connect

    def guarded(sock: socket.socket, address: Any) -> Any:
        if sock.family != getattr(socket, "AF_UNIX", None) and str(address[0]) not in LOOPBACK:
            attempts.append(address)
            raise ConnectionRefusedError(f"исходящее соединение запрещено в тестах: {address!r}")
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield attempts
    if attempts:
        pytest.fail(f"тест пытался открыть соединения не на loopback: {attempts!r}")
