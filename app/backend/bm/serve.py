"""Точка запуска api: `python -m bm.serve` (T098, 14_tech_rulings.md, пакет 5, 5Д1; дефект Д-1).

При `uvicorn --workers N` (N > 1) слушающий сокет создаёт сам uvicorn с `proto=0`, а asyncio ставит
TCP_NODELAY на принятые соединения только при `proto == IPPROTO_TCP`. Без флага каждый повторный запрос
на keep-alive соединении ждёт ~40 мс (алгоритм Нейгла + отложенный ACK). Поэтому протокол HTTP — подкласс
H11Protocol, который ставит TCP_NODELAY на каждом принятом TCP-сокете. Внутренности uvicorn и asyncio
не подменяются.

Параметры — как в прежней команде `uvicorn bm.main:create_app --factory --host 0.0.0.0 --port 8080
--workers ${UVICORN_WORKERS} --no-server-header`: хост и порт — аргументами (по умолчанию 0.0.0.0:8080, как в
контейнере), число воркеров — из UVICORN_WORKERS (по умолчанию 2). Новых переменных окружения нет.
"""

import argparse
import asyncio
import os
import socket
from collections.abc import Sequence

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

APP = "bm.main:create_app"
DEFAULT_HOST = "0.0.0.0"  # noqa: S104 - в контейнере api слушает все интерфейсы; публикация — BIND_ADDR в compose
DEFAULT_PORT = 8080
DEFAULT_WORKERS = 2  # как bm.settings.Settings.uvicorn_workers
_TCP_FAMILIES = (socket.AF_INET, socket.AF_INET6)


class NoDelayH11Protocol(H11Protocol):
    """H11Protocol с TCP_NODELAY на принятом TCP-сокете. Объявлен на уровне модуля: воркеры uvicorn
    запускаются через spawn и получают класс по имени `bm.serve.NoDelayH11Protocol`."""

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        sock = transport.get_extra_info("socket")
        if sock is not None and sock.family in _TCP_FAMILIES:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass  # соединение уже закрыто клиентом — обработает базовый протокол
        super().connection_made(transport)


def _workers() -> int:
    raw = os.environ.get("UVICORN_WORKERS", "").strip()
    if not raw:
        return DEFAULT_WORKERS
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise SystemExit(f"UVICORN_WORKERS: нужно целое число не меньше 1, получено {raw!r}")
    return value


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m bm.serve", description="Запуск api (uvicorn)")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"адрес (по умолчанию {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"порт (по умолчанию {DEFAULT_PORT})")
    args = parser.parse_args(argv)
    workers = _workers()
    uvicorn.run(
        APP,
        factory=True,
        host=args.host,
        port=args.port,
        workers=workers,
        http=NoDelayH11Protocol,
        server_header=False,
    )


if __name__ == "__main__":
    # Запуск через `-m`: этот модуль — __main__. Берём main из импортированного bm.serve, чтобы в uvicorn ушёл
    # класс `bm.serve.NoDelayH11Protocol`, а не `__main__.NoDelayH11Protocol` (его ищут дочерние процессы).
    from bm.serve import main as _main

    _main()
