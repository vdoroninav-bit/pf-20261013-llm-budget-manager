"""T098 (14_tech_rulings.md, пакет 5, 5Д1): регресс-тест дефекта Д-1 — задержка keep-alive при нескольких воркерах.

Запускает ту же команду, что `CMD` образа и `deploy/local/stack.sh` (`python -m bm.serve`), с `UVICORN_WORKERS=2`
на свободном порту 127.0.0.1 и тестовой БД из DATABASE_URL. Клиент — одно соединение `http.client.HTTPConnection`
без TCP_NODELAY: 1 прогревочный и 30 подряд `GET /healthz`. Медиана запросов 2–31 должна быть < 10 мс.
С дефектом (сокет без TCP_NODELAY на стороне сервера: Nagle + delayed ACK) — ~40 мс на запрос.
"""

import http.client
import os
import signal
import socket
import statistics
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests import conftest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="дефект Д-1 (TCP_NODELAY при uvicorn --workers) воспроизводится и проверяется только на Linux",
)

# Та же команда, что CMD образа и stack.sh (T098); хост и порт — аргументами, воркеры — из UVICORN_WORKERS.
SERVE_CMD = [sys.executable, "-m", "bm.serve", "--host", "127.0.0.1", "--port", "{port}"]
WORKERS = "2"
WARMUP = 1
REQUESTS = 30
MEDIAN_LIMIT_MS = 10.0
START_TIMEOUT_S = 60.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _healthz_ok(port: int) -> bool:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        conn.request("GET", "/healthz")
        return conn.getresponse().status == 200
    except OSError:
        return False
    finally:
        conn.close()


@pytest.fixture
def api_port(tmp_path: Path) -> Iterator[int]:
    if not conftest.DATABASE_URL:
        pytest.skip("DATABASE_URL не задан: api при старте мигрирует БД (make test)")
    port = _free_port()
    endpoint = ("127.0.0.1", port)
    conftest.ALLOWED_ENDPOINTS.add(endpoint)  # loopback к своему api, не исходящее соединение
    env = {
        **os.environ,
        "DATABASE_URL": conftest.DATABASE_URL,
        "SESSION_SECRET": conftest.TEST_SESSION_SECRET,
        "UVICORN_WORKERS": WORKERS,
        "IMPORT_ENABLED": "false",
        "LOG_LEVEL": "WARNING",
    }
    env.pop("PROMETHEUS_MULTIPROC_DIR", None)
    cmd = [part.format(port=port) for part in SERVE_CMD]
    log = tmp_path / "api.log"
    with log.open("wb") as out:
        proc = subprocess.Popen(  # noqa: S603 - своя команда запуска api, без ввода извне
            cmd, env=env, cwd=tmp_path, stdout=out, stderr=subprocess.STDOUT, start_new_session=True
        )
    try:
        deadline = time.monotonic() + START_TIMEOUT_S
        while not _healthz_ok(port):
            if proc.poll() is not None or time.monotonic() > deadline:
                pytest.fail(f"api не поднялся ({' '.join(cmd)}):\n{log.read_text(errors='replace')[-3000:]}")
            time.sleep(0.2)
        yield port
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=20)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        conftest.ALLOWED_ENDPOINTS.discard(endpoint)


def test_keepalive_requests_are_fast_with_two_workers(api_port: int) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", api_port, timeout=5)  # без TCP_NODELAY у клиента
    durations_ms: list[float] = []
    try:
        for i in range(WARMUP + REQUESTS):
            started = time.perf_counter()
            conn.request("GET", "/healthz")
            resp = conn.getresponse()
            resp.read()
            elapsed = (time.perf_counter() - started) * 1000
            assert resp.status == 200
            assert resp.getheader("Connection", "").lower() != "close"  # соединение одно, keep-alive
            if i >= WARMUP:
                durations_ms.append(elapsed)
    finally:
        conn.close()
    median = statistics.median(durations_ms)
    print(
        f"keep-alive GET /healthz, воркеров {WORKERS}: медиана {median:.2f} мс, "
        f"мин {min(durations_ms):.2f}, макс {max(durations_ms):.2f} (n={len(durations_ms)})"
    )
    assert median < MEDIAN_LIMIT_MS, (
        f"медиана {median:.2f} мс ≥ {MEDIAN_LIMIT_MS} мс: похоже на Д-1 (нет TCP_NODELAY на сокетах api)"
    )
