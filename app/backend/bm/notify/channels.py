"""Каналы доставки уведомлений. Канал «в админке» обязателен (FR-029): уведомление — это сама строка
`notification`, её показывает лента (`listNotifications`). Внешний канал `Webhook` (через `bm.egress`) — T073.
"""

from typing import Protocol
from uuid import UUID

import asyncpg


class Channel(Protocol):
    name: str

    async def deliver(self, conn: asyncpg.Connection, notification_id: UUID) -> None: ...


class InApp:
    """Лента админки: доставка не требует действий — строка уже видна пользователям с правами."""

    name = "in_app"

    async def deliver(self, conn: asyncpg.Connection, notification_id: UUID) -> None:
        return None
