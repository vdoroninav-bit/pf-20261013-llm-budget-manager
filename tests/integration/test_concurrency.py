"""T020: конкурентные решения на границе лимита (сценарий 2.5, SC-004, ADR-002).

3 экземпляра приложения — у каждого свой пул соединений к одной БД (как 3 процесса api) — по 50 одновременных
запросов. Блокировка `budget_lock … FOR UPDATE` должна дать нулевой перерасход от гонок:
Σ резервов ≤ остатка до теста — строго; и при этом ёмкость не теряется (остаток после < цены одного вызова).

Написано до реализации (T021): xfail(strict=True) на уровне модуля.
"""

import asyncio
from contextlib import AsyncExitStack
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.qa import catalog, decision_body, key_headers, reserved, spend
from tests.fixtures.seed import Seed

pytestmark = [
    pytest.mark.db,
    pytest.mark.xfail(
        strict=True,
        raises=(AssertionError, ImportError),
        reason="T021: Budget.decide и POST /api/v1/decisions ещё не реализованы",
    ),
]

POOLS = 3
PER_POOL = 50
PRICE_A = 3000  # 30 ₽ за запрос 1 000 / 400


async def _fire(app: Any, key: str, logins: list[str]) -> list[httpx.Response]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8080") as c:
        return list(
            await asyncio.gather(
                *(
                    c.post(
                        "/api/v1/decisions",
                        json=decision_body(login=login, model="A"),
                        headers=key_headers(key),
                    )
                    for login in logins
                )
            )
        )


@pytest.mark.parametrize("limit_owner", ["department", "employee"])
async def test_three_pools_fifty_concurrent_no_overspend(
    db: asyncpg.Connection, seed: Seed, settings: Any, clock: FrozenClock, limit_owner: str
) -> None:
    from bm.main import create_app

    await catalog(seed, only="A")
    # граница лимита: остаток 1 000 ₽ = 33 вызова по 30 ₽ и 10 ₽ «хвоста»; 150 претендентов
    if limit_owner == "department":
        dep = await seed.department("Маркетинг", limits={"month": 10_000_000}, allowed=("A",))
        emps = [await seed.employee(f"user{i:02d}", department=dep) for i in range(10)]
        await spend(db, "department", dep, 9_900_000)
        owner_type, owner_id = "department", dep
    else:
        dep = await seed.department("Маркетинг", limits={"month": 1_000_000_000}, allowed=("A",))
        emps = [await seed.employee("ivanov", department=dep, limits={"day": 100_000})]
        owner_type, owner_id = "employee", emps[0].id
    remaining_before = 100_000
    _, key = await seed.caller_key()
    logins = [e.login for e in emps]

    async with AsyncExitStack() as stack:
        apps = []
        for _ in range(POOLS):
            app = create_app(settings, clock=clock)
            await stack.enter_async_context(app.router.lifespan_context(app))
            apps.append(app)
        batches = await asyncio.gather(
            *(
                _fire(app, key, [logins[(p * PER_POOL + i) % len(logins)] for i in range(PER_POOL)])
                for p, app in enumerate(apps)
            )
        )

    responses = [r for batch in batches for r in batch]
    assert len(responses) == POOLS * PER_POOL
    for r in responses:
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        assert_matches_contract(r, "createDecision")
    bodies = [r.json() for r in responses]
    allowed = [b for b in bodies if b["outcome"] in ("allow", "redirect")]
    denied = [b for b in bodies if b["outcome"] == "deny"]
    assert all(b["code"] == "budget_exceeded" for b in denied)
    total_reserve = sum(int(Decimal(b["reserve_rub"]) * 100) for b in allowed)

    # строго: гонки не дают перерасхода
    assert total_reserve <= remaining_before, f"перерасход: {total_reserve} > {remaining_before} коп."
    # и не теряют ёмкость: свободного остатка меньше цены одного вызова
    assert remaining_before - total_reserve < PRICE_A, (
        f"недоиспользовано {remaining_before - total_reserve} коп."
    )
    assert len(allowed) == remaining_before // PRICE_A
    # резервы в budget_lock совпадают с ответами, записей ровно 150
    assert await reserved(db, owner_type, owner_id) == total_reserve
    assert await db.fetchval("SELECT count(*) FROM call_record") == POOLS * PER_POOL
    assert len({b["decision_id"] for b in bodies}) == POOLS * PER_POOL
