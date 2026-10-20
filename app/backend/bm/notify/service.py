"""Уведомления (FR-020, FR-025, FR-029): создание «ровно один раз» и лента админки.

Ключ уникальности — (kind, department_id, period, period_start), ограничение `notification_once`
с NULLS NOT DISTINCT. Поэтому для уведомлений без периода (сбой импорта, нет курса) вызывающий
передаёт `period_start` — дату, на которую уведомление актуально, иначе оно придёт один раз навсегда.

Оценка порогов (`evaluate`) и доставка во внешний канал (`deliver`) — T071 и T073.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

import asyncpg

type NotificationKind = Literal[
    "threshold_80",
    "threshold_100",
    "subs_share",
    "import_failure",
    "import_unallocated",
    "fx_missing",
    "reservation_no_report",
]
type PeriodName = Literal["day", "week", "month"]


@dataclass(frozen=True, slots=True)
class Notification:
    id: UUID
    kind: str
    department_id: UUID | None
    period: str | None
    period_start: date | None
    message: str
    created_at: datetime
    read: bool
    webhook_status: str


async def create_once(
    conn: asyncpg.Connection,
    kind: NotificationKind,
    department_id: UUID | None,
    period: PeriodName | None,
    period_start: date,
    message: str,
    *,
    webhook_status: Literal["disabled", "pending"] = "disabled",
) -> bool:
    """Создаёт уведомление, если по ключу его ещё нет. True — создано сейчас; False — уже было.

    Конкурентные вызовы безопасны: побеждает ровно один (ON CONFLICT по уникальному ограничению).
    `period_start` обязателен (14_tech_rulings.md Б5): у уведомлений без периода это дата МСК, когда
    обнаружено условие, иначе уведомление пришло бы один раз навсегда. `None` — ValueError.
    """
    if period_start is None:
        raise ValueError("period_start обязателен: для уведомлений без периода — дата МСК обнаружения (Б5)")
    created = await conn.fetchval(
        "INSERT INTO notification(kind, department_id, period, period_start, message, webhook_status) "
        "VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT ON CONSTRAINT notification_once DO NOTHING RETURNING id",
        kind,
        department_id,
        period,
        period_start,
        message,
        webhook_status,
    )
    return created is not None


def _row(r: asyncpg.Record) -> Notification:
    return Notification(
        id=r["id"],
        kind=r["kind"],
        department_id=r["department_id"],
        period=r["period"],
        period_start=r["period_start"],
        message=r["message"],
        created_at=r["created_at"],
        read=r["read"],
        webhook_status=r["webhook_status"],
    )


async def list_for_user(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    only_department: UUID | None,
    restrict_to_department: bool,
    unread: bool | None,
    page: int,
    page_size: int,
) -> tuple[list[Notification], int]:
    """Лента пользователя, новые сверху.

    `restrict_to_department` — владелец бюджета: только `only_department`.
    """
    where = ["$1::uuid IS NOT NULL"]  # $1 (пользователь) участвует в каждом запросе
    args: list[object] = [user_id]
    if restrict_to_department:
        args.append(only_department)
        where.append(f"department_id IS NOT DISTINCT FROM ${len(args)} AND department_id IS NOT NULL")
    if unread is True:
        where.append("NOT ($1 = ANY(read_by))")
    elif unread is False:
        where.append("$1 = ANY(read_by)")
    cond = " AND ".join(where)
    total: int = await conn.fetchval(f"SELECT count(*) FROM notification WHERE {cond}", *args)  # noqa: S608
    args += [page_size, (page - 1) * page_size]
    rows = await conn.fetch(
        "SELECT id, kind, department_id, period, period_start, message, created_at, webhook_status, "  # noqa: S608
        f"($1 = ANY(read_by)) AS read FROM notification WHERE {cond} "
        f"ORDER BY created_at DESC, id LIMIT ${len(args) - 1} OFFSET ${len(args)}",
        *args,
    )
    return [_row(r) for r in rows], total


async def get_department(conn: asyncpg.Connection, notification_id: UUID) -> tuple[bool, UUID | None]:
    """(есть ли уведомление, его подразделение) — для проверки прав до отметки."""
    row = await conn.fetchrow("SELECT department_id FROM notification WHERE id = $1", notification_id)
    if row is None:
        return False, None
    return True, row["department_id"]


async def mark_read(conn: asyncpg.Connection, notification_id: UUID, user_id: UUID) -> bool:
    """Отмечает прочитанным для пользователя; повтор — без изменений. False — уведомления нет."""
    status = await conn.execute(
        "UPDATE notification SET read_by = CASE WHEN $2 = ANY(read_by) THEN read_by "
        "ELSE array_append(read_by, $2) END WHERE id = $1",
        notification_id,
        user_id,
    )
    return status != "UPDATE 0"
