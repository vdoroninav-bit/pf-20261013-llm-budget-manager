"""T098 (14_tech_rulings.md, пакет 5, 5Д1): точка запуска `python -m bm.serve`.

Дефект Д-1: при `uvicorn --workers N` слушающий сокет создаётся с `proto=0`, asyncio не ставит TCP_NODELAY на
принятые соединения, и каждый повторный запрос на keep-alive соединении ждёт ~40 мс (Nagle + delayed ACK).
Протокол `bm.serve.NoDelayH11Protocol` ставит флаг сам в `connection_made`.
"""

import asyncio
import socket
import sys
from typing import Any

import pytest
from uvicorn.config import Config
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.server import ServerState

from bm import serve

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="TCP_NODELAY и запуск api проверяются только на Linux"
)


class _Transport:
    """Минимальный транспорт: uvicorn берёт из него сокет и адреса."""

    def __init__(self, sock: socket.socket | None) -> None:
        self._sock = sock

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return self._sock if name == "socket" else default

    def is_closing(self) -> bool:
        return False


async def _dummy_app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover - не вызывается
    return None


def _protocol() -> serve.NoDelayH11Protocol:
    config = Config(app=_dummy_app, lifespan="off", log_config=None)
    config.load()
    return serve.NoDelayH11Protocol(config=config, server_state=ServerState(), app_state={})


def _nodelay(sock: socket.socket) -> int:
    return sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)


def test_protocol_is_h11_subclass_importable_by_workers() -> None:
    # Дочерние процессы uvicorn (spawn) получают класс по имени модуля: он должен жить в bm.serve, не в __main__.
    assert issubclass(serve.NoDelayH11Protocol, H11Protocol)
    assert serve.NoDelayH11Protocol.__module__ == "bm.serve"


@pytest.mark.parametrize("family", [socket.AF_INET, socket.AF_INET6], ids=["inet", "inet6"])
async def test_connection_made_sets_tcp_nodelay_on_tcp_socket(family: socket.AddressFamily) -> None:
    # proto=0 — как у сокетов, принятых слушающим сокетом uvicorn в многопроцессном режиме.
    try:
        sock = socket.socket(family, socket.SOCK_STREAM, 0)
    except OSError:
        pytest.skip(f"{family.name} недоступен в среде")
    with sock:
        assert _nodelay(sock) == 0
        protocol = _protocol()
        protocol.connection_made(_Transport(sock))  # type: ignore[arg-type]
        assert _nodelay(sock) != 0
        assert protocol.transport is not None  # базовый connection_made отработал
        protocol.connections.discard(protocol)


async def test_connection_made_ignores_unix_socket() -> None:
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with left, right:
        protocol = _protocol()
        protocol.connection_made(_Transport(left))  # type: ignore[arg-type]
        assert protocol.transport is not None


async def test_connection_made_without_socket() -> None:
    protocol = _protocol()
    protocol.connection_made(_Transport(None))  # type: ignore[arg-type]
    assert protocol.transport is not None
    await asyncio.sleep(0)


# ---------------------------------------------------------------- запуск uvicorn.run


@pytest.fixture
def run_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    return calls


def test_main_defaults_match_image_cmd(
    run_calls: list[tuple[tuple[Any, ...], dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Прежний CMD: uvicorn bm.main:create_app --factory --host 0.0.0.0 --port 8080 --workers 2 --no-server-header
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    serve.main([])
    [(args, kwargs)] = run_calls
    assert args == ("bm.main:create_app",)
    assert kwargs == {
        "factory": True,
        "host": "0.0.0.0",  # noqa: S104 - как в прежнем CMD образа
        "port": 8080,
        "workers": 2,
        "http": serve.NoDelayH11Protocol,
        "server_header": False,
    }


def test_main_workers_from_env_and_host_port_from_args(
    run_calls: list[tuple[tuple[Any, ...], dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # stack.sh: --host 127.0.0.1 --port $API_PORT, число воркеров — из UVICORN_WORKERS
    monkeypatch.setenv("UVICORN_WORKERS", "3")
    serve.main(["--host", "127.0.0.1", "--port", "18080"])
    [(_, kwargs)] = run_calls
    assert (kwargs["host"], kwargs["port"], kwargs["workers"]) == ("127.0.0.1", 18080, 3)
    assert kwargs["http"] is serve.NoDelayH11Protocol


@pytest.mark.parametrize("value", ["0", "-1", "two"])
def test_main_rejects_bad_workers(
    value: str, run_calls: list[tuple[tuple[Any, ...], dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UVICORN_WORKERS", value)
    with pytest.raises(SystemExit) as exc:
        serve.main([])
    assert "UVICORN_WORKERS" in str(exc.value)
    assert run_calls == []


def test_main_empty_workers_is_default(
    run_calls: list[tuple[tuple[Any, ...], dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Пустая строка в окружении — «не задано», как в bm.settings.
    monkeypatch.setenv("UVICORN_WORKERS", "")
    serve.main([])
    assert run_calls[0][1]["workers"] == 2
