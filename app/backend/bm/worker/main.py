"""Процесс worker: `python -m bm.worker`. Миграции, затем цикл задач раз в `TICK` до SIGTERM/SIGINT."""

import asyncio
import logging
import signal
from datetime import timedelta

from bm import logging as bm_logging
from bm.budget.service import Budget
from bm.clock import SystemClock
from bm.db.migrate import migrate
from bm.db.pool import create_pool
from bm.settings import Settings
from bm.worker.jobs import Scheduler, default_jobs

log = logging.getLogger("bm.worker")

TICK_S = 5.0


async def run(settings: Settings, stop: asyncio.Event) -> None:
    clock = SystemClock()
    pool = await create_pool(settings.database_url, max_size=4)
    try:
        async with pool.acquire() as conn:
            await migrate(conn)
        budget = Budget(
            pool,
            clock,
            reservation_ttl=timedelta(minutes=settings.reservation_ttl_min),
            timeout_ms=settings.decision_timeout_ms,
            default_fail_mode=settings.default_fail_mode,
        )
        scheduler = Scheduler(pool, default_jobs(budget, settings), clock)
        log.info("worker_started")
        while not stop.is_set():
            await scheduler.tick()
            try:
                await asyncio.wait_for(stop.wait(), timeout=TICK_S)
            except TimeoutError:
                pass
    finally:
        await pool.close()
        log.info("worker_stopped")


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]  # значения — из окружения
    bm_logging.setup(settings)  # JSON-журнал, уровень LOG_LEVEL (T071)

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run(settings, stop)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
