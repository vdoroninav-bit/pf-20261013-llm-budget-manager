"""Проверки worker раз в минуту (FR-029, 14_tech_rulings.md Б5 и пакет 2, В1-5): пороги лимитов подразделений,
нет курса USD месяца, резервы без отчёта.

- **Пороги 80% и 100%** — по `used_pct` из `Budget.balance` (расход без резервов): те же правила, что у
  getCallerBalance и getDepartmentBalance. Одно уведомление на (вид, подразделение, период, начало периода).
- **`fx_missing`** — курс текущего месяца не введён, а USD нужен (модель с ценой в USD или подписка в USD):
  `department_id=NULL`, `period=NULL`, `period_start` = 1-е число месяца. Одно на месяц.
- **`reservation_no_report`** — резервы подразделения закрыты по сроку без отчёта (FR-010): подразделение
  резерва, `period=NULL`, `period_start` = дата МСК истечения (обнаружения). Одно на подразделение в сутки.

Во внешний канал уведомление уходит, если задан `WEBHOOK_URL` (`webhook_status=pending`, доставка — T073).
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg

from bm.budget.errors import BudgetError
from bm.budget.schemas import LimitBalance
from bm.budget.service import Budget, OwnerRef
from bm.domain.money import kop_to_str, str_to_kop
from bm.domain.periods import MSK, month_start, msk_date
from bm.domain.types import PeriodKind
from bm.notify.service import create_once

log = logging.getLogger("bm.notify")

PERIOD_RU = {"day": "день", "week": "неделю", "month": "месяц"}
MONTHS_RU = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)
THRESHOLDS: tuple[tuple[int, Literal["threshold_80", "threshold_100"]], ...] = (
    (80, "threshold_80"),
    (100, "threshold_100"),
)
NO_REPORT_LOOKBACK = timedelta(days=2)

type WebhookStatus = Literal["disabled", "pending"]


def _reached(lb: LimitBalance, pct: int) -> bool:
    """Порог считается по `used_pct` (расход без резервов, один знак, ROUND_HALF_UP; пакет 2, В1-5)."""
    return Decimal(lb.used_pct) >= pct


def threshold_message(department: str, lb: LimitBalance, pct: int) -> str:
    spent = sum(str_to_kop(v) for v in (lb.spent.token_rub, lb.spent.import_rub, lb.spent.subscriptions_rub))
    period = PERIOD_RU[PeriodKind(lb.period).value]
    return (
        f"Подразделение «{department}» израсходовало {pct}% лимита на {period} "
        f"с {lb.period_start.astimezone(MSK):%d.%m.%Y}: {kop_to_str(spent)} ₽ из {lb.limit_rub} ₽ "
        f"(использовано {lb.used_pct}%)."
    )


async def check_thresholds(budget: Budget, conn: asyncpg.Connection, webhook: WebhookStatus) -> int:
    """Пороги 80% и 100% по каждому лимиту подразделений. Возвращает число созданных уведомлений."""
    created = 0
    rows = await conn.fetch(
        "SELECT DISTINCT d.id, d.name FROM department d "
        "JOIN limit_def l ON l.owner_type = 'department' AND l.owner_id = d.id ORDER BY d.name"
    )
    for r in rows:
        try:
            report = await budget.balance(OwnerRef("department", r["id"]))
        except BudgetError:  # подразделение ещё не попало в снимок конфигурации — проверим в следующую минуту
            continue
        for lb in report.limits:
            for pct, kind in THRESHOLDS:
                if _reached(lb, pct) and await create_once(
                    conn,
                    kind,
                    r["id"],
                    PeriodKind(lb.period).value,  # type: ignore[arg-type]
                    lb.period_start.astimezone(MSK).date(),
                    threshold_message(r["name"], lb, pct),
                    webhook_status=webhook,
                ):
                    created += 1
    return created


async def check_fx_missing(conn: asyncpg.Connection, now: datetime, webhook: WebhookStatus) -> int:
    """Нет курса USD текущего месяца, хотя USD нужен (модели или подписки в USD)."""
    month = month_start(msk_date(now))
    if await conn.fetchval("SELECT 1 FROM fx_rate WHERE month = $1", month):
        return 0
    need_usd = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM model m JOIN LATERAL (SELECT currency FROM model_price p "
        "  WHERE p.model_id = m.id AND p.valid_from <= $1 ORDER BY p.valid_from DESC LIMIT 1) p ON true "
        "  WHERE m.active AND p.currency = 'USD') "
        "OR EXISTS (SELECT 1 FROM subscription WHERE currency = 'USD' AND deleted_at IS NULL "
        "  AND start_date <= $3 AND (end_date IS NULL OR end_date >= $2))",
        now,
        month,
        msk_date(now),
    )
    if not need_usd:
        return 0
    last = await conn.fetchrow(
        "SELECT month, usd_rub FROM fx_rate WHERE month < $1 ORDER BY month DESC LIMIT 1", month
    )
    tail = (
        f"До ввода курса используется последний известный — {last['usd_rub']} ₽ за "
        f"{MONTHS_RU[last['month'].month - 1]} "
        f"{last['month'].year}."
        if last is not None
        else "Других курсов нет: модели и подписки в USD не могут быть посчитаны."
    )
    message = f"Не введён курс USD/RUB на {MONTHS_RU[month.month - 1]} {month.year}. {tail}"
    return int(await create_once(conn, "fx_missing", None, None, month, message, webhook_status=webhook))


async def check_reservations_no_report(
    conn: asyncpg.Connection, now: datetime, webhook: WebhookStatus
) -> int:
    """Резервы, закрытые по сроку без отчёта о расходе (флаг `no_report`), — по подразделению за сутки МСК."""
    rows = await conn.fetch(
        "SELECT r.department_id, d.name, (r.expires_at AT TIME ZONE 'Europe/Moscow')::date AS day, "
        "count(*) AS n, sum(r.fact_kop) AS kop FROM call_record r JOIN department d ON d.id = "
        "r.department_id "
        "WHERE r.status = 'expired' AND 'no_report' = ANY(r.flags) AND r.created_at >= $1 "
        "AND r.expires_at > $2 AND r.expires_at <= $3 GROUP BY 1, 2, 3",
        now - NO_REPORT_LOOKBACK - timedelta(days=1),
        now - NO_REPORT_LOOKBACK,
        now,
    )
    created = 0
    for r in rows:
        day: date = r["day"]
        department_id: UUID = r["department_id"]
        message = (
            f"Подразделение «{r['name']}»: {r['n']} резерв(ов) закрыто без отчёта о расходе {day:%d.%m.%Y}, "
            f"списано по оценке {kop_to_str(int(r['kop'] or 0))} ₽. Проверьте, что вызывающая система "
            "отправляет отчёты (POST /usage)."
        )
        if await create_once(
            conn, "reservation_no_report", department_id, None, day, message, webhook_status=webhook
        ):
            created += 1
    return created


async def evaluate(budget: Budget, pool: asyncpg.Pool, *, webhook_enabled: bool = False) -> int:
    """Все проверки уведомлений одного такта worker. Возвращает число созданных уведомлений."""
    webhook: WebhookStatus = "pending" if webhook_enabled else "disabled"
    now = budget.now()
    async with pool.acquire() as conn:
        created = await check_thresholds(budget, conn, webhook)
        created += await check_fx_missing(conn, now, webhook)
        created += await check_reservations_no_report(conn, now, webhook)
    if created:
        log.info("notifications_created", extra={"count": created})
    return created
