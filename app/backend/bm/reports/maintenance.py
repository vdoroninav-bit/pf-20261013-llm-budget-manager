"""Фоновое обслуживание данных отчётов (12_plan.md §7, §8): агрегаты `agg_daily` и ретенция.

- **`agg_daily` раз в сутки.** Пересчитываются последние `AGG_WINDOW_DAYS` суток до вчера включительно: отчёт
  без решения принимается до 7 суток назад, а импорт уточняется в окне `IMPORT_WINDOW_DAYS`, поэтому прошлые
  сутки ещё могут измениться. Агрегаты хранят токенный расход и импорт по подразделению и модели и переживают
  удаление детальных партиций (`RETENTION_AGG_MONTHS`, по умолчанию 36 мес.).
- **Ретенция раз в сутки.** Партиции `call_record` создаются на 2 месяца вперёд; партиции старше
  `RETENTION_DETAIL_MONTHS` перед удалением сворачиваются в `agg_daily` (токены), затем `DROP TABLE`. Вместе
  с ними
  удаляются ключи идемпотентности `report_key` того же возраста. Агрегаты старше `RETENTION_AGG_MONTHS` и
  журнал запусков `job_run` старше 90 суток удаляются.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import asyncpg

from bm.budget import repo
from bm.domain.periods import month_end, month_start, msk_date
from bm.reports.service import partition_month

log = logging.getLogger("bm.reports")

AGG_WINDOW_DAYS = 8
JOB_RUN_KEEP = timedelta(days=90)
_RETENTION_LOCK = 0x626D_7265  # «bmre» — advisory-блокировка ретенции


def add_months(d: date, months: int) -> date:
    """1-е число месяца, сдвинутого на `months` (может быть отрицательным)."""
    index = d.year * 12 + (d.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


async def aggregate(conn: asyncpg.Connection, lo: date, hi: date, *, sources: tuple[str, ...]) -> None:
    """Пересчитывает `agg_daily` за сутки [lo, hi] по источникам `sources` ('token', 'import')."""
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM agg_daily WHERE day BETWEEN $1 AND $2 AND source = ANY($3::text[])",
            lo,
            hi,
            list(sources),
        )
        if "token" in sources:
            await conn.execute(
                "INSERT INTO agg_daily(day, department_id, model_id, source, amount_kop, tokens) "
                "SELECT expense_date, department_id, coalesce(final_model, requested_model), 'token', "
                "sum(fact_kop), sum(coalesce(tokens_in, 0) + coalesce(tokens_out, 0) + "
                "coalesce(tokens_cached, 0)) "
                "FROM call_record WHERE fact_kop IS NOT NULL AND status IN ('settled', 'expired') "
                "AND department_id IS NOT NULL AND expense_date BETWEEN $1 AND $2 "
                "AND created_at >= ($1::date)::timestamp AT TIME ZONE 'Europe/Moscow' GROUP BY 1, 2, 3",
                lo,
                hi,
            )
        if "import" in sources:
            await conn.execute(
                "INSERT INTO agg_daily(day, department_id, model_id, source, amount_kop, tokens) "
                "SELECT day, department_id, model, 'import', sum(coalesce(amount_kop, 0)), 0 FROM "
                "import_line "
                "WHERE status = 'charged' AND department_id IS NOT NULL AND day BETWEEN $1 AND $2 GROUP BY "
                "1, 2, 3",
                lo,
                hi,
            )


async def daily_aggregates(pool: asyncpg.Pool, now: datetime) -> None:
    """Задача worker `agg_daily`: последние `AGG_WINDOW_DAYS` суток до вчера включительно."""
    today = msk_date(now)
    async with pool.acquire() as conn:
        await aggregate(
            conn,
            today - timedelta(days=AGG_WINDOW_DAYS),
            today - timedelta(days=1),
            sources=("token", "import"),
        )


@dataclass(frozen=True, slots=True)
class RetentionResult:
    dropped: tuple[str, ...]
    agg_deleted: int


async def retention(
    pool: asyncpg.Pool, now: datetime, *, detail_months: int, agg_months: int
) -> RetentionResult:
    """Задача worker `retention`: партиции на 2 месяца вперёд, удаление старых партиций и агрегатов."""
    today = msk_date(now)
    async with pool.acquire() as conn:
        await repo.ensure_partitions(conn, today)  # текущий и следующий месяц
        await repo.ensure_partitions(conn, add_months(today, 1))  # и ещё один
        detail_cutoff = add_months(month_start(today), -detail_months)
        agg_cutoff = add_months(month_start(today), -agg_months)
        dropped: list[str] = []
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _RETENTION_LOCK)
            names = await conn.fetch(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'call_record' ORDER BY c.relname"
            )
            for r in names:
                month = partition_month(r["relname"])
                if month is None or month >= detail_cutoff:
                    continue
                await aggregate(conn, month, month_end(month), sources=("token",))
                await conn.execute(f'DROP TABLE "{r["relname"]}"')
                dropped.append(r["relname"])
            if dropped:
                await conn.execute(
                    "DELETE FROM report_key WHERE created_at < ($1::date)::timestamp AT TIME ZONE "
                    "'Europe/Moscow'",
                    detail_cutoff,
                )
            status = await conn.execute("DELETE FROM agg_daily WHERE day < $1", agg_cutoff)
            await conn.execute("DELETE FROM job_run WHERE started_at < $1", now - JOB_RUN_KEEP)
    agg_deleted = int(status.split()[-1])
    if dropped or agg_deleted:
        log.info("retention_done", extra={"count": len(dropped) + agg_deleted})
    return RetentionResult(tuple(dropped), agg_deleted)
