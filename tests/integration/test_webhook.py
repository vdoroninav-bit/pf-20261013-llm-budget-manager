"""T073: внешний канал уведомлений — webhook (FR-029; 14_tech_rulings.md, пакет 3; 14_checkpoint_week5.md).

- при заданном `WEBHOOK_URL` уведомления **всех** видов создаются с `webhook_status=pending`;
- доставка — задачей worker `webhook`: `pending` → `delivered` (ответ 2xx) или → `failed` после 3 неудачных попыток;
- тело — JSON с полями `id`, `kind`, `department_id`, `period`, `period_start`, `message`, `created_at`,
  без идентификаторов сотрудников;
- `WEBHOOK_URL` пуст → `disabled`, задачи доставки нет, 0 соединений;
- публичный адрес → отказ `egress` (EgressForbidden), 0 соединений, уведомления остаются `pending`.

Получатель — локальный HTTP-сервер на 127.0.0.1 (порт временно добавляется в список разрешённых
соединений `egress_guard` из tests/conftest.py).
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any, get_args
from uuid import UUID

import asyncpg
import pytest

from bm.egress.client import EgressForbidden
from bm.notify import channels
from bm.notify.service import NotificationKind, create_once
from tests import conftest
from tests.fixtures.qa import spend, worker_tick
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db

PAYLOAD_KEYS = {"id", "kind", "department_id", "period", "period_start", "message", "created_at"}
DAY = date(2026, 10, 14)


@dataclass
class Receiver:
    """Минимальный HTTP/1.1-сервер: отвечает статусами из `statuses` (по умолчанию 200), пишет запросы."""

    port: int = 0
    statuses: list[int] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, path, _ = lines[0].split(" ", 2)
        headers = {k.strip().lower(): v.strip() for k, v in (h.split(":", 1) for h in lines[1:] if ":" in h)}
        body = await reader.readexactly(int(headers.get("content-length", "0")))
        self.requests.append({"method": method, "path": path, "headers": headers, "body": body})
        status = self.statuses.pop(0) if self.statuses else 200
        writer.write(f"HTTP/1.1 {status} X\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        writer.close()


@pytest.fixture
async def receiver() -> AsyncIterator[Receiver]:
    rec = Receiver()
    server = await asyncio.start_server(rec.handle, "127.0.0.1", 0)
    rec.port = server.sockets[0].getsockname()[1]
    endpoint = ("127.0.0.1", rec.port)
    conftest.ALLOWED_ENDPOINTS.add(endpoint)
    try:
        yield rec
    finally:
        conftest.ALLOWED_ENDPOINTS.discard(endpoint)
        server.close()
        await server.wait_closed()


@pytest.fixture
def settings(settings: Any, receiver: Receiver) -> Any:
    """Настройки приложения с `WEBHOOK_URL` на локальный получатель."""
    return settings.model_copy(update={"webhook_url": f"{receiver.url}/hook?src=bm"})


async def _no_sleep(_: float) -> None:
    return None


def _webhook(app: Any) -> channels.Webhook:
    return channels.Webhook(app.state.settings, sleep=_no_sleep)


async def _status(db: asyncpg.Connection, nid: UUID) -> str:
    v: str = await db.fetchval("SELECT webhook_status FROM notification WHERE id = $1", nid)
    return v


async def _notification(db: asyncpg.Connection, dep: UUID | None, kind: str = "threshold_80") -> UUID:
    assert await create_once(db, kind, dep, "month", date(2026, 10, 1), "Маркетинг: 80% лимита на месяц.")  # type: ignore[arg-type]
    nid: UUID = await db.fetchval("SELECT id FROM notification WHERE kind = $1", kind)
    return nid


# ---------------------------------------------------------------- статус при создании


async def test_all_kinds_pending_when_webhook_configured(
    app: Any, db: asyncpg.Connection, seed: Seed
) -> None:
    dep = await seed.department("Маркетинг")
    for kind in get_args(NotificationKind.__value__):
        await create_once(db, kind, dep, None, DAY, f"уведомление {kind}")
    rows = await db.fetch("SELECT kind, webhook_status FROM notification")
    assert len(rows) == 7
    assert {r["webhook_status"] for r in rows} == {"pending"}


async def test_lifespan_end_restores_disabled(app: Any, db: asyncpg.Connection) -> None:
    async with app.router.lifespan_context(app):
        pass  # второй цикл жизни приложения закончился — настройка процесса сброшена
    await create_once(db, "import_failure", None, None, DAY, "сбой")
    assert await db.fetchval("SELECT webhook_status FROM notification") == "disabled"


# ---------------------------------------------------------------- доставка


async def test_delivers_pending_as_json(
    app: Any, db: asyncpg.Connection, seed: Seed, receiver: Receiver
) -> None:
    dep = await seed.department("Маркетинг")
    nid = await _notification(db, dep)
    result = await channels.deliver_pending(app.state.pool, _webhook(app))
    assert (result.delivered, result.failed) == (1, 0)
    assert await _status(db, nid) == "delivered"

    req = receiver.requests[0]
    assert req["method"] == "POST" and req["path"] == "/hook?src=bm"
    assert req["headers"]["content-type"] == "application/json"
    body = json.loads(req["body"])
    assert set(body) == PAYLOAD_KEYS
    assert body["id"] == str(nid) and body["kind"] == "threshold_80" and body["department_id"] == str(dep)
    assert body["period"] == "month" and body["period_start"] == "2026-10-01"
    assert body["message"] == "Маркетинг: 80% лимита на месяц."
    assert body["created_at"].endswith("+03:00")  # время — МСК, как в API

    again = await channels.deliver_pending(app.state.pool, _webhook(app))
    assert (again.delivered, again.failed) == (0, 0) and len(receiver.requests) == 1


async def test_three_failures_mark_failed(
    app: Any, db: asyncpg.Connection, seed: Seed, receiver: Receiver
) -> None:
    nid = await _notification(db, await seed.department("Маркетинг"))
    receiver.statuses = [500, 503, 302]  # 3xx — тоже неудача: редиректы не выполняются
    result = await channels.deliver_pending(app.state.pool, _webhook(app))
    assert (result.delivered, result.failed) == (0, 1)
    assert await _status(db, nid) == "failed"
    assert len(receiver.requests) == 3
    await channels.deliver_pending(app.state.pool, _webhook(app))
    assert len(receiver.requests) == 3  # failed не отправляется повторно


async def test_retry_succeeds_on_third_attempt(
    app: Any, db: asyncpg.Connection, seed: Seed, receiver: Receiver
) -> None:
    nid = await _notification(db, await seed.department("Маркетинг"))
    receiver.statuses = [500, 500, 204]
    delays: list[float] = []

    async def sleep(s: float) -> None:
        delays.append(s)

    result = await channels.deliver_pending(app.state.pool, channels.Webhook(app.state.settings, sleep=sleep))
    assert (result.delivered, result.failed) == (1, 0)
    assert await _status(db, nid) == "delivered"
    assert len(receiver.requests) == 3 and len(delays) == 2 and all(d > 0 for d in delays)


async def test_unreachable_receiver_marks_failed(app: Any, db: asyncpg.Connection, seed: Seed) -> None:
    """Получатель не слушает порт: 3 неудачные попытки соединения → failed."""
    nid = await _notification(db, await seed.department("Маркетинг"))
    probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = probe.sockets[0].getsockname()[1]
    probe.close()
    await probe.wait_closed()
    conftest.ALLOWED_ENDPOINTS.add(("127.0.0.1", port))
    try:
        settings = app.state.settings.model_copy(update={"webhook_url": f"http://127.0.0.1:{port}/hook"})
        result = await channels.deliver_pending(app.state.pool, channels.Webhook(settings, sleep=_no_sleep))
    finally:
        conftest.ALLOWED_ENDPOINTS.discard(("127.0.0.1", port))
    assert (result.delivered, result.failed) == (0, 1)
    assert await _status(db, nid) == "failed"


async def test_no_employee_identifiers_in_body(
    app: Any, db: asyncpg.Connection, seed: Seed, receiver: Receiver
) -> None:
    """Сквозной путь worker: порог → pending → задача `webhook` → delivered; в теле нет данных сотрудников."""
    dep = await seed.department("Маркетинг", limits={"month": 10_000})
    emp = await seed.employee("ivanov", department=dep, display_name="Иванов Иван", limits={"month": 1_000})
    await spend(db, "department", dep, 9_000)
    await spend(db, "employee", emp.id, 900)
    ran = await worker_tick(app)
    assert "webhook" in ran
    rows = await db.fetch("SELECT kind, webhook_status FROM notification")
    assert rows and {r["webhook_status"] for r in rows} == {"delivered"}
    assert len(receiver.requests) == len(rows)
    for req in receiver.requests:
        text = req["body"].decode()
        assert set(json.loads(text)) == PAYLOAD_KEYS
        for ident in (emp.pseudonym, "ivanov", "Иванов", str(emp.id)):
            assert ident not in text


# ---------------------------------------------------------------- отказы egress


async def test_public_address_refused_by_egress(app: Any, db: asyncpg.Connection, seed: Seed) -> None:
    nid = await _notification(db, await seed.department("Маркетинг"))
    settings = app.state.settings.model_copy(update={"webhook_url": "http://8.8.8.8/hook"})
    with pytest.raises(EgressForbidden):
        await channels.deliver_pending(app.state.pool, channels.Webhook(settings, sleep=_no_sleep))
    assert await _status(db, nid) == "pending"  # исправят адрес — доставка продолжится


async def test_webhook_job_failure_recorded_in_job_run(app: Any, db: asyncpg.Connection, seed: Seed) -> None:
    from bm.worker.jobs import Scheduler, default_jobs

    await _notification(db, await seed.department("Маркетинг"))
    settings = app.state.settings.model_copy(update={"webhook_url": "http://8.8.8.8/hook"})
    await Scheduler(app.state.pool, default_jobs(app.state.budget, settings), app.state.clock).tick()
    row = await db.fetchrow("SELECT ok, error_class FROM job_run WHERE job = 'webhook'")
    assert row is not None and row["ok"] is False and row["error_class"] == "EgressForbidden"
