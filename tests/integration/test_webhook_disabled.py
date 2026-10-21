"""T073: `WEBHOOK_URL` пуст — внешний канал выключен (14_tech_rulings.md, пакет 3).

Уведомления всех видов создаются с `webhook_status=disabled`, задачи доставки в worker нет, исходящих
соединений нет (это проверяет autouse-фикстура `egress_guard`).
"""

from datetime import date
from typing import Any, get_args

import asyncpg
import pytest

from bm.notify.service import NotificationKind, create_once
from tests.fixtures.qa import spend, worker_tick
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db


async def test_all_kinds_disabled_without_webhook(app: Any, db: asyncpg.Connection, seed: Seed) -> None:
    assert app.state.settings.webhook_url is None
    dep = await seed.department("Маркетинг")
    for kind in get_args(NotificationKind.__value__):
        await create_once(db, kind, dep, None, date(2026, 10, 14), f"уведомление {kind}")
    assert {r["webhook_status"] for r in await db.fetch("SELECT webhook_status FROM notification")} == {
        "disabled"
    }


async def test_no_webhook_job_and_no_connections(
    app: Any, db: asyncpg.Connection, seed: Seed, egress_guard: list[Any]
) -> None:
    from bm.worker.jobs import default_jobs

    assert "webhook" not in {j.name for j in default_jobs(app.state.budget, app.state.settings)}
    dep = await seed.department("Маркетинг", limits={"month": 10_000})
    await spend(db, "department", dep, 9_000)
    ran = await worker_tick(app)
    assert "notifications" in ran and "webhook" not in ran
    rows = await db.fetch("SELECT webhook_status FROM notification")
    assert rows and {r["webhook_status"] for r in rows} == {"disabled"}
    assert egress_guard == []
