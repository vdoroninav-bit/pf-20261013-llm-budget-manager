"""HTTP к API утилиты на `http.client` из stdlib.

- Соединение открывается только к хосту и порту из `base_url`. Переменные прокси окружения
  (`HTTP_PROXY` и др.) не применяются: `http.client` их не читает, в отличие от `urllib.request`.
- Таймаут — общий срок на запрос (соединение, отправка, ответ), а не на каждую операцию сокета.
- Keep-alive: одно соединение на поток. Если переиспользованное соединение оказалось закрытым
  сервером (перезапуск API, простой дольше keep-alive), запрос один раз повторяется на новом,
  если срок ещё не истёк.
"""

import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

USER_AGENT = "budget_client/0.1"


class TransportError(Exception):
    """Утилита не ответила: таймаут, отказ в соединении, разрыв, неполный ответ."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None


_STALE = (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError, http.client.BadStatusLine)


class Transport:
    def __init__(self, base_url: str, api_key: str) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("base_url: нужен адрес вида http(s)://host[:port][/prefix]")
        self._https = parts.scheme == "https"
        self._host = parts.hostname
        self._port = parts.port or (443 if self._https else 80)
        self._prefix = parts.path.rstrip("/")
        self._api_key = api_key
        self._local = threading.local()
        self._all_lock = threading.Lock()
        self._all: set[http.client.HTTPConnection] = set()

    def request(
        self,
        method: str,
        path: str,
        timeout_s: float,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        deadline = time.monotonic() + timeout_s
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        hdrs = {"X-Api-Key": self._api_key, "User-Agent": USER_AGENT, "Accept": "application/json"}
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        for attempt in (0, 1):
            reused = False
            try:
                conn, reused = self._connection(deadline)
                return self._exchange(conn, method, self._prefix + path, data, hdrs, deadline)
            except _STALE as exc:
                self._drop()
                if not reused or attempt == 1 or time.monotonic() >= deadline:
                    raise TransportError(type(exc).__name__) from exc
            except (OSError, http.client.HTTPException, ValueError) as exc:
                self._drop()
                raise TransportError(type(exc).__name__) from exc
        raise TransportError("unreachable")  # pragma: no cover

    def close(self) -> None:
        """Закрыть соединения всех потоков."""
        with self._all_lock:
            conns, self._all = self._all, set()
        for conn in conns:
            conn.close()

    # ------------------------------------------------------------ внутреннее

    def _connection(self, deadline: float) -> tuple[http.client.HTTPConnection, bool]:
        conn: http.client.HTTPConnection | None = getattr(self._local, "conn", None)
        if conn is not None and conn.sock is not None:
            return conn, True
        remaining = _remaining(deadline)
        cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
        conn = cls(self._host, self._port, timeout=remaining)
        try:
            conn.connect()
            conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except BaseException:
            conn.close()
            raise
        self._local.conn = conn
        with self._all_lock:
            self._all.add(conn)
        return conn, False

    @staticmethod
    def _exchange(
        conn: http.client.HTTPConnection,
        method: str,
        url: str,
        data: bytes | None,
        headers: dict[str, str],
        deadline: float,
    ) -> HttpResponse:
        conn.sock.settimeout(_remaining(deadline))
        conn.request(method, url, body=data, headers=headers)
        conn.sock.settimeout(_remaining(deadline))
        resp = conn.getresponse()
        if conn.sock is not None:
            conn.sock.settimeout(_remaining(deadline))
        payload = resp.read()
        if resp.will_close:
            conn.close()
        return HttpResponse(resp.status, {k.lower(): v for k, v in resp.getheaders()}, payload)

    def _drop(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
            with self._all_lock:
                self._all.discard(conn)


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("срок запроса истёк")
    return left
