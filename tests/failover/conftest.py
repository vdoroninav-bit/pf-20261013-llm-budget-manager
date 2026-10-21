"""Отказные тесты SC-002 и SC-003 (T032, T093): против поднятого стека.

Запуск: `make up && make failover` (docker compose) или `make up-local && BM_STACK=local make failover`
(стек из локальных процессов, 14_tech_rulings.md, пакет 3, 3Д1(в); драйвер — tests/failover/stack.py). Цель `failover` ставит BM_FAILOVER=1. Без неё (в `make test`,
`pytest --collect-only`, обычном `pytest`) тесты пропускаются с причиной — Docker и стек им обязательны.
Если BM_FAILOVER=1, но стека нет, тест ПАДАЕТ (а не пропускается): гейт релиза не должен молча зеленеть.

Переменные окружения:
  BM_STACK            compose (по умолчанию) | local
  BASE_URL            адрес API (по умолчанию http://127.0.0.1:8080; local — из `deploy/local/stack.sh env`)
  DATABASE_URL        BM_STACK=local: кластер стека для проверки report_key (из `stack.sh env`; не 5432)
  BM_LOCAL_STACK      BM_STACK=local: путь к stack.sh (по умолчанию deploy/local/stack.sh)
  BM_ENV_FILE         файл переменных стека (compose: deploy/.env; local: deploy/local/.state/stack.env)
  BM_COMPOSE          команда compose (по умолчанию «docker compose -f deploy/docker-compose.yml --env-file deploy/.env»)
  BM_ADMIN_LOGIN      учётная запись админки для подготовки данных (по умолчанию ADMIN_INITIAL_LOGIN из deploy/.env)
  BM_ADMIN_PASSWORD   пароль после смены; при первом входе временный ADMIN_INITIAL_PASSWORD сменяется на него
  BM_PG_USER, BM_PG_DB  пользователь и БД в контейнере postgres для проверки (по умолчанию bm / bm)
"""

import os
import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENABLED = os.environ.get("BM_FAILOVER") == "1"
SKIP_REASON = (
    "отказные тесты SC-002/SC-003 требуют поднятого стека: `make up && make failover` "
    "или `make up-local && BM_STACK=local make failover`"
)

# Эталонный клиент (T033) лежит в app/client/budget_client; до установки пакета — через sys.path.
CLIENT_DIR = ROOT / "app" / "client" / "budget_client"
if CLIENT_DIR.is_dir() and str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if ENABLED:
        return
    skip = pytest.mark.skip(reason=SKIP_REASON)
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(skip)


# ---------------------------------------------------------------- сеть: только loopback


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


@pytest.fixture(scope="session")
def stack() -> None:
    """Предусловия: стек (compose или local) запущен, API отвечает на readyz. Иначе — FAIL, не skip."""
    from tests.failover.stack import check_stack

    check_stack()
