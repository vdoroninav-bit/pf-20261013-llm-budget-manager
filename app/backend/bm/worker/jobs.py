"""Задачи worker и планировщик (ADR-002): у каждой задачи свой интервал, одновременно задачу выполняет
только один процесс (`pg_try_advisory_lock` на имя задачи), каждый запуск пишется в `job_run`.

Новые задачи (импорт T061, пороги T071, подписки T051, webhook T073) добавляются в `default_jobs`.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import asyncpg

from bm import metrics
from bm.budget import subs
from bm.budget.service import Budget
from bm.clock import Clock
from bm.notify import channels, thresholds
from bm.provider_import import pipeline
from bm.reports import maintenance
from bm.settings import Settings

log = logging.getLogger("bm.worker")

EXPIRE_EVERY = timedelta(seconds=60)
PARTITIONS_EVERY = timedelta(hours=1)
SUBSCRIPTIONS_EVERY = timedelta(minutes=5)
IMPORT_EVERY = timedelta(minutes=5)  # T061: ручной запуск подхватывается за ≤ 5 мин
NOTIFY_EVERY = timedelta(seconds=60)  # T071: пороги, нет курса, резервы без отчёта
DAILY = timedelta(days=1)  # T071: agg_daily и ретенция
WEBHOOK_EVERY = timedelta(seconds=60)  # T073: доставка уведомлений во внешний канал


@dataclass(frozen=True, slots=True)
class Job:
    name: str
    every: timedelta
    run: Callable[[], Awaitable[object]]


def default_jobs(budget: Budget, settings: Settings | None = None) -> list[Job]:
    return [
        Job("expire_reserves", EXPIRE_EVERY, lambda: budget.expire_reserves(budget.now())),
        Job("call_record_partitions", PARTITIONS_EVERY, budget.ensure_partitions),
        # T051: с 00:05 МСК — M месяца и доля подписок нового периода (повтор без дублей)
        Job(
            "subscriptions_new_period",
            SUBSCRIPTIONS_EVERY,
            lambda: subs.new_period_check(budget.pool, budget.now()),
        ),
        *report_jobs(budget, settings),
        *import_jobs(budget, settings),
        *webhook_jobs(budget, settings),  # последней: такт доставляет уведомления, созданные в этом же такте
    ]


def webhook_jobs(budget: Budget, settings: Settings | None) -> list[Job]:
    """T073: доставка `pending` во внешний канал раз в минуту. Без `WEBHOOK_URL` задачи нет (0 соединений)."""
    if settings is None or not settings.webhook_url:
        return []
    webhook = channels.Webhook(settings)
    return [Job("webhook", WEBHOOK_EVERY, lambda: channels.deliver_pending(budget.pool, webhook))]


def import_jobs(budget: Budget, settings: Settings | None) -> list[Job]:
    """T061: очередь ручных запусков и плановая загрузка (IMPORT_HOUR_MSK, повтор раз в час до
    IMPORT_RETRY_UNTIL_HOUR_MSK). Без `settings` задачи нет; при IMPORT_ENABLED=false такт ничего не делает.
    """
    if settings is None:
        return []
    return [
        Job("provider_import", IMPORT_EVERY, lambda: pipeline.scheduled(budget.pool, settings, budget.now()))
    ]


def report_jobs(budget: Budget, settings: Settings | None) -> list[Job]:
    """T071: уведомления раз в минуту, `agg_daily` и ретенция раз в сутки.

    Без `settings` (тесты QA вызывают `default_jobs(budget)`) — значения по умолчанию из 12_plan.md §8.
    """
    webhook = bool(settings and settings.webhook_url)
    detail = settings.retention_detail_months if settings else 12
    agg = settings.retention_agg_months if settings else 36
    return [
        Job(
            "notifications",
            NOTIFY_EVERY,
            lambda: thresholds.evaluate(budget, budget.pool, webhook_enabled=webhook),
        ),
        Job("agg_daily", DAILY, lambda: maintenance.daily_aggregates(budget.pool, budget.now())),
        Job(
            "retention",
            DAILY,
            lambda: maintenance.retention(budget.pool, budget.now(), detail_months=detail, agg_months=agg),
        ),
    ]


async def run_job(pool: asyncpg.Pool, job: Job, now: datetime) -> bool | None:
    """Выполняет задачу под advisory-блокировкой. None — задачу уже выполняет другой процесс;
    True/False — успех или сбой (сбой пишется в `job_run.error_class` и метрику, цикл не останавливается)."""
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", f"bm.job.{job.name}"):
            return None
        try:
            run_id: int = await conn.fetchval(
                "INSERT INTO job_run(job, started_at) VALUES ($1, $2) RETURNING id", job.name, now
            )
            error_class: str | None = None
            try:
                await job.run()
            except Exception as exc:
                error_class = type(exc).__name__
                metrics.JOB_FAILURES.labels(job=job.name).inc()
                log.error("job_failed", extra={"job": job.name, "error_class": error_class})
            await conn.execute(
                "UPDATE job_run SET finished_at = clock_timestamp(), ok = $2, error_class = $3 WHERE id = $1",
                run_id,
                error_class is None,
                error_class,
            )
            return error_class is None
        finally:
            await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", f"bm.job.{job.name}")


class Scheduler:
    """Запускает задачи, у которых наступил срок. Время — от `Clock` (в тестах — FrozenClock)."""

    def __init__(self, pool: asyncpg.Pool, jobs: Sequence[Job], clock: Clock) -> None:
        self._pool = pool
        self._jobs = list(jobs)
        self._clock = clock
        self._next: dict[str, datetime] = {}

    async def tick(self) -> list[str]:
        """Один проход: выполняет задачи с наступившим сроком; возвращает их имена."""
        ran: list[str] = []
        for job in self._jobs:
            now = self._clock.now()
            due = self._next.get(job.name)
            if due is not None and now < due:
                continue
            result = await run_job(self._pool, job, now)
            if result is not None:
                ran.append(job.name)
            self._next[job.name] = now + job.every
        return ran
