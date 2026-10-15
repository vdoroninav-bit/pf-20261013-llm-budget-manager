"""Задачи worker и планировщик (ADR-002): у каждой задачи свой интервал, одновременно задачу выполняет
только один процесс (`pg_try_advisory_lock` на имя задачи), каждый запуск пишется в `job_run`.

Новые задачи (импорт T061, пороги T071, подписки T051) добавляются в `default_jobs`.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import asyncpg

from bm import metrics
from bm.budget.service import Budget
from bm.clock import Clock

log = logging.getLogger("bm.worker")

EXPIRE_EVERY = timedelta(seconds=60)
PARTITIONS_EVERY = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class Job:
    name: str
    every: timedelta
    run: Callable[[], Awaitable[object]]


def default_jobs(budget: Budget) -> list[Job]:
    return [
        Job("expire_reserves", EXPIRE_EVERY, lambda: budget.expire_reserves(budget.now())),
        Job("call_record_partitions", PARTITIONS_EVERY, budget.ensure_partitions),
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
