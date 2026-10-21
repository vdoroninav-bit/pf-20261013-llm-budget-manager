"""Каналы доставки уведомлений (FR-029).

- **В админке** (`InApp`) — обязателен: уведомление — это сама строка `notification`, её показывает лента
  (`listNotifications`).
- **Webhook** (T073) — внешний канал во внутреннюю сеть. Включён, если задан `WEBHOOK_URL`: тогда уведомления
  всех видов создаются с `webhook_status=pending` (`bm.notify.service.configure`), а задача worker `webhook`
  (`deliver_pending`) отправляет их POST-запросом с JSON-телом. Ответ 2xx — `delivered`; иначе до
  `ATTEMPTS` попыток с паузой, затем `failed`. Соединение — только через `bm.egress` («webhook»: частные
  адреса, без редиректов и прокси из окружения).
- **Бюджет такта** (T096, 14_tech_rulings.md 4Б4): задачи worker выполняются по очереди, поэтому
  `deliver_pending` не берёт следующее уведомление, если с начала такта прошло ≥ `TICK_BUDGET_S`
  (по монотонным часам). Начатое доводится до конца (худший случай ≈ 30 + 18 с), остальные остаются
  `pending` до следующего такта. HTTP-запрос выполняется без открытой транзакции и занятого соединения БД.

Тело запроса — только поля уведомления (`PAYLOAD_FIELDS`), без идентификаторов сотрудников: тексты уведомлений
говорят о подразделениях и источниках, а не о людях.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import asyncpg

from bm.domain.periods import MSK
from bm.egress.client import get_client
from bm.settings import Settings

log = logging.getLogger("bm.notify")

ATTEMPTS = 3
RETRY_DELAYS_S = (1.0, 2.0)  # пауза перед 2-й и 3-й попыткой
TIMEOUT_S = 5.0  # на одну попытку: задача не должна надолго занимать worker
BATCH = 20  # уведомлений за один такт; остальные — в следующем
TICK_BUDGET_S = 30.0  # с начала такта: дольше следующее уведомление не берётся (4Б4)
PAYLOAD_FIELDS = ("id", "kind", "department_id", "period", "period_start", "message", "created_at")


class Channel(Protocol):
    name: str

    async def deliver(self, conn: asyncpg.Connection, notification_id: UUID) -> None: ...


class InApp:
    """Лента админки: доставка не требует действий — строка уже видна пользователям с правами."""

    name = "in_app"

    async def deliver(self, conn: asyncpg.Connection, notification_id: UUID) -> None:
        return None


def payload(row: asyncpg.Record) -> dict[str, Any]:
    """JSON-тело webhook: только `PAYLOAD_FIELDS`, время — МСК (принцип IX), даты — ISO 8601."""
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "department_id": str(row["department_id"]) if row["department_id"] is not None else None,
        "period": row["period"],
        "period_start": row["period_start"].isoformat() if row["period_start"] is not None else None,
        "message": row["message"],
        "created_at": row["created_at"].astimezone(MSK).isoformat(),
    }


class Webhook:
    """Отправка одного уведомления на `WEBHOOK_URL` с повтором. `sleep` и `monotonic` подменяются в тестах."""

    name = "webhook"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: Any = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._sleep = sleep
        self.monotonic = monotonic

    def client(self) -> Any:
        """httpx-клиент от `bm.egress`. EgressDisabled/EgressForbidden — адрес выключен или не частный."""
        return get_client("webhook", self._settings, transport=self._transport)

    async def send(self, client: Any, body: dict[str, Any]) -> bool:
        """До `ATTEMPTS` попыток; True — получатель ответил 2xx."""
        url = self._settings.webhook_url
        data = json.dumps(body, ensure_ascii=False).encode()
        for attempt in range(ATTEMPTS):
            if attempt:
                await self._sleep(RETRY_DELAYS_S[attempt - 1])
            try:
                resp = await client.post(
                    url, content=data, headers={"Content-Type": "application/json"}, timeout=TIMEOUT_S
                )
            except Exception as exc:  # сеть, тайм-аут, отказ egress-хука: класс — в журнал, адрес — нет
                log.warning(
                    "webhook_attempt_failed", extra={"kind": body["kind"], "error_class": type(exc).__name__}
                )
                continue
            if 200 <= resp.status_code < 300:
                return True
            log.warning("webhook_attempt_failed", extra={"kind": body["kind"], "status": resp.status_code})
        return False


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: int = 0
    failed: int = 0


async def deliver_pending(pool: asyncpg.Pool, webhook: Webhook) -> DeliveryResult:
    """Задача worker: до `BATCH` уведомлений `pending` по порядку создания → `delivered` или `failed`.

    Перед каждым уведомлением проверяется бюджет такта `TICK_BUDGET_S`; соединение БД берётся только на чтение
    очереди и на смену статуса (autocommit), на время HTTP-запроса оно возвращено в пул.

    Клиент создаётся до чтения очереди: если адрес не проходит `egress`, исключение уходит в `job_run`,
    а уведомления остаются `pending` (после исправления `WEBHOOK_URL` они будут доставлены). Задачу выполняет
    один процесс (advisory-блокировка планировщика), статус меняется только из `pending`.
    """
    started = webhook.monotonic()
    client = webhook.client()
    delivered = failed = 0
    async with client:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, kind, department_id, period, period_start, message, created_at FROM notification "
                "WHERE webhook_status = 'pending' ORDER BY created_at, id LIMIT $1",
                BATCH,
            )
        for row in rows:
            if webhook.monotonic() - started >= TICK_BUDGET_S:
                break  # остальные — pending до следующего такта
            ok = await webhook.send(client, payload(row))
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE notification SET webhook_status = $2 "
                    "WHERE id = $1 AND webhook_status = 'pending'",
                    row["id"],
                    "delivered" if ok else "failed",
                )
            delivered += ok
            failed += not ok
    if left := len(rows) - delivered - failed:
        log.info("webhook_tick_budget", extra={"count": left, "status": "pending"})
    if delivered or failed:
        log.info("webhook_delivery", extra={"count": delivered, "status": "delivered"})
        if failed:
            log.warning("webhook_delivery", extra={"count": failed, "status": "failed"})
    return DeliveryResult(delivered, failed)
