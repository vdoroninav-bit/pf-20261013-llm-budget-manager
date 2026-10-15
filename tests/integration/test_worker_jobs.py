"""T031: worker — цикл задач, `pg_try_advisory_lock` на задачу, журнал `job_run`, истечение резервов раз в 60 с."""

from datetime import timedelta
from typing import Any

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from tests.conftest import DATABASE_URL
from tests.fixtures.qa import call_record, catalog, decide, decision_body
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db


async def _open_reserve(client: httpx.AsyncClient, seed: Seed) -> str:
    await catalog(seed, only="A")
    dep = await seed.department("Маркетинг", allowed=("A",))
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key()
    d = await decide(client, key, decision_body(model="A"))
    return str(d["decision_id"])


async def test_scheduler_runs_expire_reserves_every_60_seconds(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    from bm.worker.jobs import Scheduler, default_jobs

    decision_id = await _open_reserve(client, seed)
    scheduler = Scheduler(app.state.pool, default_jobs(app.state.budget), clock)

    assert "expire_reserves" in await scheduler.tick()
    assert (await call_record(db, decision_id))["status"] == "open"  # срок ещё не вышел

    clock.advance(seconds=30)
    assert "expire_reserves" not in await scheduler.tick()  # интервал 60 с не прошёл

    clock.advance(minutes=15)
    assert "expire_reserves" in await scheduler.tick()
    assert (await call_record(db, decision_id))["status"] == "expired"

    runs = await db.fetch("SELECT ok, error_class, finished_at FROM job_run WHERE job = 'expire_reserves'")
    assert len(runs) == 2 and all(r["ok"] and r["error_class"] is None and r["finished_at"] for r in runs)


async def test_job_skipped_while_other_process_holds_lock(
    app: Any, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    from bm.worker.jobs import Job, run_job

    calls: list[int] = []

    async def body() -> None:
        calls.append(1)

    job = Job("expire_reserves", timedelta(seconds=60), body)
    other = await asyncpg.connect(DATABASE_URL)
    try:
        assert await other.fetchval("SELECT pg_try_advisory_lock(hashtext('bm.job.expire_reserves'))")
        assert await run_job(app.state.pool, job, clock.now()) is None
        assert calls == [] and await db.fetchval("SELECT count(*) FROM job_run") == 0
    finally:
        await other.close()
    assert await run_job(app.state.pool, job, clock.now()) is True
    assert calls == [1]


async def test_failed_job_is_logged_and_does_not_stop_loop(
    app: Any, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    from bm.worker.jobs import Job, Scheduler

    async def broken() -> None:
        raise RuntimeError("сбой")

    async def fine() -> None:
        return None

    scheduler = Scheduler(
        app.state.pool,
        [Job("broken", timedelta(seconds=60), broken), Job("fine", timedelta(seconds=60), fine)],
        clock,
    )
    assert set(await scheduler.tick()) == {"broken", "fine"}
    rows = {r["job"]: r for r in await db.fetch("SELECT job, ok, error_class FROM job_run")}
    assert rows["broken"]["ok"] is False and rows["broken"]["error_class"] == "RuntimeError"
    assert rows["fine"]["ok"] is True
