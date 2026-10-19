"""T033: эталонный клиент `budget_client` (FR-006, FR-011, принцип III, SC-002).

Клиент проверяется против фейкового API на loopback (http.server из stdlib). Корневой `egress_guard`
переопределён: разрешён только адрес фейкового API — так закрепляется правило «сеть только к адресу API».

Проверяется:
- буфер: SQLite с PRAGMA synchronous=FULL, ключ report_id, переживает перезапуск процесса;
- report(): сначала запись в буфер, report_id задаёт вызывающий;
- flush(): пакеты по 500, экспоненциальная пауза при сбое, построчные ошибки, без дублей;
- decide(): таймаут 50 мс, fail-open по умолчанию, fail-closed → PolicyUnavailable, кэш fail_mode на диске;
- integration-config: кэш на диске, обновление раз в refresh_s (60 с), If-None-Match/304;
- ключ API не пишется на диск; переменные прокси окружения игнорируются.
"""

import json
import socket
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

CLIENT_DIR = Path(__file__).resolve().parents[2] / "app" / "client" / "budget_client"
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

from budget_client.buffer import ReportBuffer  # noqa: E402
from budget_client.client import BudgetClient, ClientRequestError, PolicyUnavailable  # noqa: E402

API_KEY = "bm_test_key_0123456789abcdef"
DEPT_OPEN = "11111111-1111-4111-8111-111111111111"
DEPT_CLOSED = "22222222-2222-4222-8222-222222222222"


# ---------------------------------------------------------------- фейковый API


@dataclass
class FakeApi:
    """Состояние фейкового API: что отвечать и что получено."""

    decision_delay_s: float = 0.0
    decision_status: int = 200
    fail_mode: str = "open"
    department_id: str = DEPT_OPEN
    default_fail_mode: str = "open"
    departments: list[dict[str, str]] = field(default_factory=lambda: [])
    batch_failures: int = 0  # сколько первых запросов /usage/batch ответить 503
    row_errors: dict[str, int] = field(default_factory=dict)  # report_id → статус ошибки строки
    stored: dict[str, int] = field(default_factory=dict)  # report_id → сколько раз записан
    batches: list[int] = field(default_factory=list)  # размеры принятых пакетов
    config_requests: list[str | None] = field(default_factory=list)  # If-None-Match
    headers: list[dict[str, str]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def config(self) -> dict[str, Any]:
        return {
            "version": "v-" + self.default_fail_mode + json.dumps(self.departments, sort_keys=True),
            "timeout_ms": 50,
            "refresh_s": 60,
            "default_fail_mode": self.default_fail_mode,
            "departments": self.departments,
        }


def _handler(api: FakeApi) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _send(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> Any:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n)) if n else None

        def do_GET(self) -> None:
            api.headers.append(dict(self.headers))
            if self.path != "/api/v1/integration-config":
                self._send(404, {"code": "not_found"})
                return
            inm = self.headers.get("If-None-Match")
            api.config_requests.append(inm)
            cfg = api.config()
            etag = f'"{cfg["version"]}"'
            if inm == etag:
                self._send(304, None, {"ETag": etag})
            else:
                self._send(200, cfg, {"ETag": etag})

        def do_POST(self) -> None:
            api.headers.append(dict(self.headers))
            body = self._body()
            if self.headers.get("X-Api-Key") != API_KEY:
                self._send(401, {"code": "unauthorized"})
                return
            if self.path == "/api/v1/decisions":
                time.sleep(api.decision_delay_s)
                if api.decision_status != 200:
                    self._send(api.decision_status, {"code": "x", "status": api.decision_status})
                    return
                self._send(
                    200,
                    {
                        "decision_id": str(uuid.uuid4()),
                        "outcome": "allow",
                        "code": None,
                        "requested_model": body["model"],
                        "model": body["model"],
                        "http_status_hint": 200,
                        "policy_mode": "enforce",
                        "fail_mode": api.fail_mode,
                        "department_id": api.department_id,
                        "reserve_rub": "0.02",
                        "budget_blocked_by_subscriptions": False,
                        "message": "Разрешено.",
                        "created_at": "2026-10-19T12:00:00+03:00",
                    },
                )
            elif self.path == "/api/v1/usage/batch":
                with api.lock:
                    if api.batch_failures > 0:
                        api.batch_failures -= 1
                        self._send(503, {"code": "unavailable"})
                        return
                    items = body["items"]
                    assert 1 <= len(items) <= 1000
                    api.batches.append(len(items))
                    results = []
                    for i, it in enumerate(items):
                        rid = it["report_id"]
                        if rid in api.row_errors:
                            st = api.row_errors[rid]
                            results.append({"index": i, "ok": False, "error": {"status": st, "code": "e"}})
                            continue
                        dup = rid in api.stored
                        api.stored[rid] = api.stored.get(rid, 0) + (0 if dup else 1)
                        results.append(
                            {
                                "index": i,
                                "ok": True,
                                "result": {"report_id": rid, "status": "duplicate" if dup else "charged"},
                            }
                        )
                self._send(200, {"results": results})
            else:
                self._send(404, {"code": "not_found"})

    return Handler


class _Httpd(ThreadingHTTPServer):
    """Как остановка контейнера: stop() рвёт и открытые keep-alive соединения, а не только слушающий сокет."""

    daemon_threads = True

    def __init__(self, *a: Any) -> None:
        super().__init__(*a)
        self.conns: set[socket.socket] = set()

    def get_request(self) -> tuple[socket.socket, Any]:
        sock, addr = super().get_request()
        self.conns.add(sock)
        return sock, addr

    def drop_connections(self) -> None:
        for sock in list(self.conns):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.conns.clear()


@dataclass
class Server:
    api: FakeApi
    url: str
    httpd: _Httpd

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd.drop_connections()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def server() -> Iterator[Server]:
    api = FakeApi()
    httpd = _Httpd(("127.0.0.1", 0), _handler(api))
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    srv = Server(api, f"http://127.0.0.1:{httpd.server_address[1]}", httpd)
    yield srv
    try:
        srv.stop()
    except OSError:
        pass


@pytest.fixture
def down_url() -> str:
    """Адрес, на котором никто не слушает (утилита остановлена)."""
    return f"http://127.0.0.1:{_free_port()}"


ALLOWED: set[int] = set()


@pytest.fixture(autouse=True)
def egress_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """Сеть только к адресу API: разрешены loopback-порты фейкового API и «остановленного» API."""
    attempts: list[Any] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def check(sock: socket.socket, address: Any) -> None:
        if sock.family == getattr(socket, "AF_UNIX", None):
            return
        if str(address[0]) != "127.0.0.1" or int(address[1]) not in ALLOWED:
            attempts.append(address)
            raise ConnectionRefusedError(f"соединение не к адресу API: {address!r}")

    def guarded(sock: socket.socket, address: Any) -> Any:
        check(sock, address)
        return real_connect(sock, address)

    def guarded_ex(sock: socket.socket, address: Any) -> Any:
        check(sock, address)
        return real_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_ex)
    yield attempts
    ALLOWED.clear()
    if attempts:
        pytest.fail(f"клиент открывал соединения не к адресу API: {attempts!r}")


def _allow(url: str) -> str:
    ALLOWED.add(int(url.rsplit(":", 1)[1]))
    return url


class Sleeps:
    """Подмена time.sleep для пауз flush(): записывает длительности, не спит."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, s: float) -> None:
        self.calls.append(s)


def make_client(url: str, state: Path, **kw: Any) -> BudgetClient:
    kw.setdefault("background", False)
    return BudgetClient(_allow(url), API_KEY, state, **kw)


SERVICE = {"type": "service"}
IVAN = {"type": "employee", "login": "ivanov"}


def _report(c: BudgetClient, rid: str | None = None, **kw: Any) -> str:
    return c.report(
        decision_id=kw.pop("decision_id", None),
        tokens={"input": 10, "output": 5},
        call_status="ok",
        report_id=rid or str(uuid.uuid4()),
        subject=SERVICE,
        model="m1",
        **kw,
    )


# ---------------------------------------------------------------- буфер


def test_buffer_is_sqlite_with_synchronous_full(tmp_path: Path) -> None:
    buf = ReportBuffer(tmp_path / "buffer.sqlite3")
    try:
        assert buf.pragma("synchronous") == 2  # FULL
        assert buf.pragma("journal_mode") == "wal"
    finally:
        buf.close()


def test_buffer_keyed_by_report_id_and_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "buffer.sqlite3"
    buf = ReportBuffer(path)
    rid = str(uuid.uuid4())
    assert buf.put(rid, {"report_id": rid, "n": 1}) is True
    assert buf.put(rid, {"report_id": rid, "n": 2}) is False  # повтор того же report_id не дублирует
    buf.close()

    again = ReportBuffer(path)  # «перезапуск процесса»
    try:
        assert again.pending_count() == 1
        [(got_rid, body)] = again.take(10)
        assert got_rid == rid and body["n"] == 1
    finally:
        again.close()


def test_buffer_rows_sqlite_readable_by_stdlib(tmp_path: Path) -> None:
    """Формат буфера — обычная SQLite: оператор может посмотреть его без клиента."""
    buf = ReportBuffer(tmp_path / "b.sqlite3")
    buf.put("r1", {"report_id": "r1"})
    buf.close()
    with sqlite3.connect(tmp_path / "b.sqlite3") as conn:
        assert conn.execute("SELECT report_id, state FROM report").fetchall() == [("r1", "pending")]


# ---------------------------------------------------------------- report()


def test_report_writes_buffer_first_with_caller_report_id(down_url: str, tmp_path: Path) -> None:
    c = make_client(down_url, tmp_path)
    try:
        rid = str(uuid.uuid4())
        t0 = time.monotonic()
        assert _report(c, rid) == rid
        assert time.monotonic() - t0 < 0.5
        assert c.pending() == 1
        [(got, body)] = c._buffer.take(1)
        assert got == rid
        assert body["report_id"] == rid and body["subject"] == SERVICE and body["model"] == "m1"
        assert body["tokens"] == {"input": 10, "output": 5} and body["call_status"] == "ok"
        assert "decision_id" not in body and body["occurred_at"]  # время вызова, а не досылки
    finally:
        c.close()


def test_report_generates_report_id_when_absent(down_url: str, tmp_path: Path) -> None:
    c = make_client(down_url, tmp_path)
    try:
        rid = c.report(decision_id=str(uuid.uuid4()), tokens={"input": 1, "output": 1}, call_status="ok")
        assert str(uuid.UUID(rid)) == rid
        assert c.pending() == 1
    finally:
        c.close()


def test_report_without_decision_requires_subject_and_model(down_url: str, tmp_path: Path) -> None:
    c = make_client(down_url, tmp_path)
    try:
        with pytest.raises(ValueError, match="subject"):
            c.report(decision_id=None, tokens={"input": 1, "output": 1}, call_status="ok")
        with pytest.raises(TypeError):
            c.report(decision_id=None, tokens={}, call_status="ok", unknown_field=1)
        assert c.pending() == 0
    finally:
        c.close()


# ---------------------------------------------------------------- flush()


def test_flush_sends_batches_of_500_exactly_once(server: Server, tmp_path: Path) -> None:
    c = make_client(server.url, tmp_path, sleep=Sleeps())
    try:
        ids = [_report(c) for _ in range(1200)]
        assert c.flush() == 1200
        assert server.api.batches == [500, 500, 200]
        assert c.pending() == 0
        assert c.flush() == 0
        assert sorted(server.api.stored) == sorted(ids)
        assert set(server.api.stored.values()) == {1}
    finally:
        c.close()


def test_flush_max_batch_parameter(server: Server, tmp_path: Path) -> None:
    c = make_client(server.url, tmp_path, sleep=Sleeps())
    try:
        for _ in range(7):
            _report(c)
        assert c.flush(max_batch=3) == 7
        assert server.api.batches == [3, 3, 1]
    finally:
        c.close()


def test_flush_retries_with_exponential_pause(server: Server, tmp_path: Path) -> None:
    sleeps = Sleeps()
    c = make_client(server.url, tmp_path, sleep=sleeps)
    try:
        for _ in range(3):
            _report(c)
        server.api.batch_failures = 3
        assert c.flush() == 3
        assert sleeps.calls == [0.5, 1.0, 2.0]
        assert c.pending() == 0
    finally:
        c.close()


def test_flush_gives_up_after_max_retries_and_keeps_rows(down_url: str, tmp_path: Path) -> None:
    sleeps = Sleeps()
    c = make_client(down_url, tmp_path, sleep=sleeps)
    try:
        for _ in range(3):
            _report(c)
        assert c.flush(max_retries=4) == 0
        assert sleeps.calls == [0.5, 1.0, 2.0, 4.0]
        assert c.pending() == 3
    finally:
        c.close()


def test_flush_pause_is_capped(down_url: str, tmp_path: Path) -> None:
    sleeps = Sleeps()
    c = make_client(down_url, tmp_path, sleep=sleeps)
    try:
        _report(c)
        c.flush(max_retries=9)
        assert sleeps.calls[-1] == 30.0 and max(sleeps.calls) == 30.0
    finally:
        c.close()


def test_flush_after_restart_delivers_buffer_of_previous_process(
    server: Server, down_url: str, tmp_path: Path
) -> None:
    """Отчёты из окна отказа лежат на диске; новый процесс клиента досылает их после восстановления."""
    first = make_client(down_url, tmp_path, sleep=Sleeps())
    ids = [_report(first) for _ in range(5)]
    assert first.flush(max_retries=0) == 0
    first.close()

    second = make_client(server.url, tmp_path, sleep=Sleeps())
    try:
        assert second.flush() == 5
        assert sorted(server.api.stored) == sorted(ids)
    finally:
        second.close()


def test_flush_duplicate_counts_as_delivered(server: Server, tmp_path: Path) -> None:
    c = make_client(server.url, tmp_path, sleep=Sleeps())
    try:
        rid = _report(c)
        server.api.stored[rid] = 1  # сервер уже записал (ответ 200 потерялся по дороге)
        assert c.flush() == 1
        assert c.pending() == 0 and server.api.stored[rid] == 1
    finally:
        c.close()


def test_flush_row_errors_permanent_rejected_transient_kept(server: Server, tmp_path: Path) -> None:
    sleeps = Sleeps()
    c = make_client(server.url, tmp_path, sleep=sleeps)
    try:
        good = _report(c)
        bad = _report(c)  # 422: строка не примется никогда — в карантин, не блокирует буфер
        later = _report(c)  # 500 у строки — временно, остаётся в буфере
        server.api.row_errors = {bad: 422, later: 500}
        assert c.flush(max_retries=1) == 1
        assert good in server.api.stored
        assert c.pending() == 1
        assert [r for r, _ in c.rejected()] == [bad]
        server.api.row_errors = {}
        assert c.flush() == 1
        assert c.pending() == 0 and later in server.api.stored and bad not in server.api.stored
    finally:
        c.close()


# ---------------------------------------------------------------- decide()


def test_decide_returns_server_decision(server: Server, tmp_path: Path) -> None:
    c = make_client(server.url, tmp_path)
    try:
        d = c.decide(SERVICE, "m1", "chat", input_tokens=10, max_output_tokens=10)
        assert d.outcome == "allow" and d.fail_open is False
        assert uuid.UUID(d.decision_id) and d.model == "m1" and d.fail_mode == "open"
        headers = server.api.headers[-1]
        assert headers["X-Api-Key"] == API_KEY
    finally:
        c.close()


def test_decide_timeout_50ms_fail_open(server: Server, tmp_path: Path) -> None:
    server.api.decision_delay_s = 0.3
    c = make_client(server.url, tmp_path)
    try:
        assert c.timeout_ms == 50
        t0 = time.monotonic()
        d = c.decide(SERVICE, "m1", "chat")
        elapsed = time.monotonic() - t0
        assert d.outcome == "allow" and d.fail_open is True and d.decision_id is None
        assert d.model == "m1"
        assert elapsed < 0.2, f"decide ждал {elapsed:.3f} с при таймауте 50 мс"
    finally:
        c.close()


def test_decide_unreachable_defaults_to_fail_open(down_url: str, tmp_path: Path) -> None:
    c = make_client(down_url, tmp_path)
    try:
        d = c.decide(IVAN, "m1", "chat")
        assert d.fail_open is True and d.outcome == "allow"
    finally:
        c.close()


@pytest.mark.parametrize("status", [500, 502, 503, 429])
def test_decide_server_error_is_failure(server: Server, tmp_path: Path, status: int) -> None:
    server.api.decision_status = status
    c = make_client(server.url, tmp_path)
    try:
        assert c.decide(SERVICE, "m1", "chat").fail_open is True
    finally:
        c.close()


def test_decide_client_error_is_not_masked_as_fail_open(server: Server, tmp_path: Path) -> None:
    """401/403/422 — ошибка интеграции, а не сбой утилиты (ADR-005): fail-open её бы скрыл."""
    server.api.decision_status = 422
    c = make_client(server.url, tmp_path)
    try:
        with pytest.raises(ClientRequestError) as e:
            c.decide(SERVICE, "m1", "chat")
        assert e.value.status == 422
    finally:
        c.close()


def test_fail_closed_cached_on_disk_raises_policy_unavailable(server: Server, tmp_path: Path) -> None:
    server.api.fail_mode = "closed"
    server.api.department_id = DEPT_CLOSED
    c = make_client(server.url, tmp_path)
    c.decide(SERVICE, "m1", "chat")  # прогрев: fail_mode субъекта из ответа
    server.stop()  # утилита остановлена
    try:
        with pytest.raises(PolicyUnavailable) as e:
            c.decide(SERVICE, "m1", "chat")
        assert e.value.code == "policy_unavailable"
    finally:
        c.close()
    # новый процесс клиента без единого ответа утилиты берёт fail_mode с диска
    restarted = make_client(server.url, tmp_path)
    try:
        with pytest.raises(PolicyUnavailable):
            restarted.decide(SERVICE, "m1", "chat")
    finally:
        restarted.close()


def test_fail_mode_is_per_subject(server: Server, tmp_path: Path) -> None:
    c = make_client(server.url, tmp_path)
    try:
        server.api.fail_mode, server.api.department_id = "closed", DEPT_CLOSED
        c.decide({"type": "employee", "login": "petrov"}, "m1", "chat")
        server.api.fail_mode, server.api.department_id = "open", DEPT_OPEN
        c.decide(IVAN, "m1", "chat")
        server.api.decision_status = 503
        assert c.decide(IVAN, "m1", "chat").fail_open is True
        with pytest.raises(PolicyUnavailable):
            c.decide({"type": "employee", "login": "petrov"}, "m1", "chat")
    finally:
        c.close()


# ---------------------------------------------------------------- integration-config


def test_config_cached_on_disk_and_used_for_unknown_subject(server: Server, tmp_path: Path) -> None:
    server.api.default_fail_mode = "closed"
    c = make_client(server.url, tmp_path)
    assert c.refresh_config() is True
    c.close()
    server.stop()
    restarted = make_client(server.url, tmp_path)
    try:
        with pytest.raises(PolicyUnavailable):  # субъект не встречался — default_fail_mode из кэша
            restarted.decide({"type": "employee", "login": "new"}, "m1", "chat")
    finally:
        restarted.close()


def test_config_service_key_uses_its_department(server: Server, tmp_path: Path) -> None:
    server.api.departments = [{"department_id": DEPT_CLOSED, "fail_mode": "closed"}]
    c = make_client(server.url, tmp_path)
    try:
        c.refresh_config()
        server.api.decision_status = 503
        with pytest.raises(PolicyUnavailable):
            c.decide(SERVICE, "m1", "chat")
    finally:
        c.close()


def test_config_refresh_every_60s_with_etag(server: Server, tmp_path: Path) -> None:
    now = [1_000_000.0]
    c = make_client(server.url, tmp_path, clock=lambda: now[0])
    try:
        assert c.config_stale() is True
        c.refresh_config()
        assert c.config_stale() is False
        now[0] += 59
        assert c.config_stale() is False
        now[0] += 2
        assert c.config_stale() is True
        c.refresh_config()  # не изменилась → 304
        assert server.api.config_requests[0] is None
        assert server.api.config_requests[1] is not None
        assert c.config_stale() is False
    finally:
        c.close()


def test_config_change_overrides_older_subject_cache(server: Server, tmp_path: Path) -> None:
    now = [1_000_000.0]
    c = make_client(server.url, tmp_path, clock=lambda: now[0])
    try:
        c.decide(SERVICE, "m1", "chat")  # подразделение DEPT_OPEN, open
        now[0] += 61
        server.api.departments = [{"department_id": DEPT_OPEN, "fail_mode": "closed"}]
        c.refresh_config()  # администратор переключил подразделение в closed
        server.api.decision_status = 503
        with pytest.raises(PolicyUnavailable):
            c.decide(SERVICE, "m1", "chat")
    finally:
        c.close()


def test_config_refresh_failure_keeps_cache(server: Server, tmp_path: Path) -> None:
    server.api.default_fail_mode = "closed"
    c = make_client(server.url, tmp_path)
    try:
        assert c.refresh_config() is True
        server.stop()
        assert c.refresh_config() is False
        with pytest.raises(PolicyUnavailable):
            c.decide(IVAN, "m1", "chat")
    finally:
        c.close()


# ---------------------------------------------------------------- фоновая досылка и обновление


def test_background_flushes_reports_and_refreshes_config(server: Server, tmp_path: Path) -> None:
    c = make_client(server.url, tmp_path, background=True)
    try:
        ids = [_report(c) for _ in range(3)]
        deadline = time.monotonic() + 5
        while (c.pending() or c.config_stale()) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert c.pending() == 0
        assert sorted(server.api.stored) == sorted(ids)
        assert server.api.config_requests, "фон не запросил integration-config"
    finally:
        c.close()


def test_explicit_flush_concurrent_with_background_no_duplicates(server: Server, tmp_path: Path) -> None:
    c = make_client(server.url, tmp_path, background=True)
    try:
        ids = [_report(c) for _ in range(800)]
        c.flush()
        deadline = time.monotonic() + 5
        while c.pending() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert sorted(server.api.stored) == sorted(ids)
        assert set(server.api.stored.values()) == {1}
        assert sum(server.api.batches) == 800  # каждая строка отправлена ровно один раз
    finally:
        c.close()


# ---------------------------------------------------------------- безопасность и сеть


def test_api_key_never_written_to_disk(server: Server, tmp_path: Path) -> None:
    c = make_client(server.url, tmp_path)
    try:
        c.refresh_config()
        c.decide(SERVICE, "m1", "chat")
        _report(c)
    finally:
        c.close()
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert files
    for p in files:
        assert API_KEY.encode() not in p.read_bytes(), p.name


def test_proxy_environment_is_ignored(
    server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, egress_guard: list[Any]
) -> None:
    """Сеть только к адресу API: HTTP(S)_PROXY окружения не перенаправляет трафик клиента."""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    c = make_client(server.url, tmp_path)
    try:
        assert c.decide(SERVICE, "m1", "chat").fail_open is False
    finally:
        c.close()
    assert egress_guard == []


@pytest.mark.parametrize("url", ["ftp://127.0.0.1/", "file:///etc/passwd", "127.0.0.1:8080", "http://"])
def test_base_url_must_be_http(url: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        BudgetClient(url, API_KEY, tmp_path, background=False)
