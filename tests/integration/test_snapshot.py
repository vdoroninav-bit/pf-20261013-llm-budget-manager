"""Снимок конфигурации и `config.apply` (T018, 12_arch_decision.md §4.4: снимок в памяти + config_version)."""

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from bm.budget.snapshot import ConfigSnapshot, SnapshotCache, load
from bm.clock import FrozenClock
from bm.config.service import Applied, Change, apply
from bm.domain.periods import MSK
from bm.domain.types import PeriodKind
from tests.fixtures.seed import Seed

AUDIT_AT = datetime(2026, 10, 14, 12, 0, tzinfo=MSK)  # время записи аудита передаётся явно (пакет 3, 3Б7)

pytestmark = pytest.mark.db


class CountingConn:
    """Обёртка соединения: считает обращения к БД."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self.queries: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._conn, name)
        if name in {"fetch", "fetchrow", "fetchval", "execute", "executemany"}:

            async def counted(query: str, *args: Any, **kw: Any) -> Any:
                self.queries.append(query)
                return await attr(query, *args, **kw)

            return counted
        return attr


async def _version(db: asyncpg.Connection) -> int:
    v: int = await db.fetchval("SELECT version FROM config_version")
    return v


# ---------------------------------------------------------------- config.apply


def _create_department(name: str, *, fail_after_insert: bool = False) -> Change:
    async def run(conn: asyncpg.Connection) -> Applied:
        dep_id = await conn.fetchval("INSERT INTO department(name) VALUES ($1) RETURNING id", name)
        if fail_after_insert:
            raise RuntimeError("сбой после записи")
        return Applied(object_id=str(dep_id), after={"name": name, "policy_mode": "observe"}, result=dep_id)

    return Change(action="create", object_type="department", run=run)


async def test_apply_writes_change_audit_and_bumps_version(db: asyncpg.Connection) -> None:
    v0 = await _version(db)
    applied = await apply(db, _create_department("Маркетинг"), actor="admin", at=AUDIT_AT)
    assert isinstance(applied.result, UUID)
    assert await db.fetchval("SELECT name FROM department WHERE id = $1", applied.result) == "Маркетинг"
    row = await db.fetchrow(
        "SELECT actor, action, object_type, object_id, before, after::text FROM audit_log"
    )
    assert row is not None
    assert (row["actor"], row["action"], row["object_type"], row["object_id"]) == (
        "admin",
        "create",
        "department",
        str(applied.result),
    )
    assert row["before"] is None and "Маркетинг" in row["after"]
    assert await _version(db) == v0 + 1


async def test_apply_is_atomic(db: asyncpg.Connection) -> None:
    v0 = await _version(db)
    with pytest.raises(RuntimeError):
        await apply(db, _create_department("Сбой", fail_after_insert=True), actor="admin", at=AUDIT_AT)
    assert await db.fetchval("SELECT count(*) FROM department") == 0
    assert await db.fetchval("SELECT count(*) FROM audit_log") == 0
    assert await _version(db) == v0


async def test_apply_inside_outer_transaction_rolls_back_with_it(db: asyncpg.Connection) -> None:
    v0 = await _version(db)
    with pytest.raises(LookupError):
        async with db.transaction():
            await apply(db, _create_department("Внутри"), actor="admin", at=AUDIT_AT)
            raise LookupError
    assert await db.fetchval("SELECT count(*) FROM department") == 0
    assert await _version(db) == v0


# ---------------------------------------------------------------- load


@pytest.fixture
async def world(db: asyncpg.Connection, seed: Seed) -> dict[str, Any]:
    dep = await seed.department(
        "Маркетинг", policy_mode="enforce", fail_mode="closed", limits={"month": 10_000_000}
    )
    other = await seed.department("Продажи", policy_mode="observe")
    await seed.model("A", in_per_1k="10", out_per_1k="50")
    await seed.model("B", in_per_1k="1", out_per_1k="2", currency="USD", tools=False)
    await db.execute("INSERT INTO dept_model_allow(department_id, model_id) VALUES ($1, 'A'), ($1, 'B')", dep)
    ivanov = await seed.employee("ivanov", department=dep, limits={"day": 50_000})
    gone = await seed.employee("petrov", department=other, valid_from=date(2026, 9, 1))
    await db.execute("UPDATE employee_assignment SET valid_to = '2026-10-13' WHERE employee_id = $1", gone.id)
    future = await seed.employee("sidorov", department=dep, valid_from=date(2026, 10, 20))
    await seed.fx(date(2026, 10, 1), "92.5")
    caller_id, key = await seed.caller_key("gateway")
    revoked_id, revoked_key = await seed.caller_key("old", status="revoked")
    sub_id = await db.fetchval(
        "INSERT INTO subscription(provider, plan, seat_price, currency, seats, start_date, assignee_type, "
        "assignee_id) VALUES ('anthropic', 'Team', 30, 'USD', 10, '2026-10-01', 'department', $1) RETURNING id",
        dep,
    )
    return {
        "dep": dep,
        "other": other,
        "ivanov": ivanov,
        "gone": gone,
        "future": future,
        "caller_id": caller_id,
        "key": key,
        "revoked_id": revoked_id,
        "revoked_key": revoked_key,
        "sub_id": sub_id,
    }


async def test_load_contents(db: asyncpg.Connection, world: dict[str, Any], clock: FrozenClock) -> None:
    snap = await load(db, clock.now())
    assert isinstance(snap, ConfigSnapshot)
    assert snap.version == await _version(db)

    dep = snap.departments[world["dep"]]
    assert (dep.name, dep.policy_mode, dep.fail_mode, dep.subs_warn_pct) == (
        "Маркетинг",
        "enforce",
        "closed",
        50,
    )
    assert dep.allowed == frozenset({"A", "B"})
    assert snap.departments[world["other"]].allowed == frozenset()

    emp = snap.employees_by_login["ivanov"]
    assert (emp.id, emp.pseudonym, emp.department_id) == (
        world["ivanov"].id,
        world["ivanov"].pseudonym,
        world["dep"],
    )
    assert "petrov" not in snap.employees_by_login  # назначение закрыто вчера
    assert "sidorov" not in snap.employees_by_login  # назначение начнётся 20.10
    assert (
        len([a for a in snap.assignments if a.employee_id == world["gone"].id]) == 1
    )  # история для подписок

    assert snap.limits[("department", world["dep"])] == {PeriodKind.MONTH: 10_000_000}
    assert snap.limits[("employee", world["ivanov"].id)] == {PeriodKind.DAY: 50_000}

    a, b = snap.catalog["A"], snap.catalog["B"]
    assert (a.price.in_per_1k, a.price.out_per_1k, a.price.currency) == (Decimal("10"), Decimal("50"), "RUB")
    assert (b.price.currency, b.supports_tools) == ("USD", False)

    assert snap.fx_for(date(2026, 10, 1)) == Decimal("92.5")
    assert snap.fx_for(date(2026, 11, 1)) is None

    key = snap.keys[hashlib.sha256(world["key"].encode()).digest()]
    assert (key.id, key.name, key.scope_type, key.active) == (
        world["caller_id"],
        "gateway",
        "any_employee",
        True,
    )
    assert snap.keys[hashlib.sha256(world["revoked_key"].encode()).digest()].active is False

    (sub,) = snap.subscriptions
    assert (sub.id, sub.seats, sub.seat_price, sub.assignee_type) == (
        world["sub_id"],
        10,
        Decimal("30"),
        "department",
    )


async def test_load_picks_current_price(db: asyncpg.Connection, seed: Seed, clock: FrozenClock) -> None:
    await seed.model("A", in_per_1k="10", out_per_1k="50")
    await seed.price("A", "20", "60", valid_from=datetime(2026, 10, 10, tzinfo=MSK))
    await seed.price("A", "30", "70", valid_from=datetime(2026, 10, 14, 15, 0, tzinfo=MSK))  # в будущем
    snap = await load(db, clock.now())
    assert snap.catalog["A"].price.in_per_1k == Decimal("20")
    assert snap.valid_until == datetime(2026, 10, 14, 15, 0, tzinfo=MSK)


async def test_model_without_current_price_is_not_in_catalog(
    db: asyncpg.Connection, seed: Seed, clock: FrozenClock
) -> None:
    await seed.model("Z", price_from=datetime(2026, 12, 1, tzinfo=MSK))
    snap = await load(db, clock.now())
    assert "Z" not in snap.catalog


async def test_deleted_subscriptions_excluded(db: asyncpg.Connection, clock: FrozenClock) -> None:
    await db.execute(
        "INSERT INTO subscription(provider, plan, seat_price, currency, seats, start_date, assignee_type, deleted_at) "
        "VALUES ('openai', 'Team', 25, 'USD', 1, '2026-10-01', 'unallocated', now())"
    )
    assert (await load(db, clock.now())).subscriptions == ()


# ---------------------------------------------------------------- ensure_fresh


async def test_ensure_fresh_one_query_when_unchanged(
    db: asyncpg.Connection, world: dict[str, Any], clock: FrozenClock
) -> None:
    cache = SnapshotCache()
    first = await cache.ensure_fresh(db, clock.now())
    counting = CountingConn(db)
    again = await cache.ensure_fresh(counting, clock.now())  # type: ignore[arg-type]
    assert again is first
    assert len(counting.queries) == 1 and "config_version" in counting.queries[0]


async def test_ensure_fresh_reloads_after_apply(
    db: asyncpg.Connection, world: dict[str, Any], clock: FrozenClock
) -> None:
    cache = SnapshotCache()
    first = await cache.ensure_fresh(db, clock.now())

    async def run(conn: asyncpg.Connection) -> Applied:
        await conn.execute("UPDATE department SET policy_mode = 'warn' WHERE id = $1", world["dep"])
        return Applied(
            object_id=str(world["dep"]), before={"policy_mode": "enforce"}, after={"policy_mode": "warn"}
        )

    await apply(db, Change(action="update", object_type="department", run=run), actor="admin", at=AUDIT_AT)
    fresh = await cache.ensure_fresh(db, clock.now())
    assert fresh is not first and fresh.version == first.version + 1
    assert fresh.departments[world["dep"]].policy_mode == "warn"


async def test_ensure_fresh_reloads_at_msk_midnight(
    db: asyncpg.Connection, world: dict[str, Any], clock: FrozenClock
) -> None:
    """Назначения «на сегодня» меняются со сменой суток МСК без изменения config_version."""
    cache = SnapshotCache()
    clock.set(datetime(2026, 10, 19, 23, 59, 59, tzinfo=MSK))
    before = await cache.ensure_fresh(db, clock.now())
    assert "sidorov" not in before.employees_by_login
    clock.set(datetime(2026, 10, 20, 0, 0, 0, tzinfo=MSK))
    after = await cache.ensure_fresh(db, clock.now())
    assert "sidorov" in after.employees_by_login


async def test_ensure_fresh_reloads_when_new_price_starts(
    db: asyncpg.Connection, seed: Seed, clock: FrozenClock
) -> None:
    await seed.model("A", in_per_1k="10", out_per_1k="50")
    await seed.price("A", "30", "70", valid_from=datetime(2026, 10, 14, 15, 0, tzinfo=MSK))
    cache = SnapshotCache()
    assert (await cache.ensure_fresh(db, clock.now())).catalog["A"].price.in_per_1k == Decimal("10")
    clock.set(datetime(2026, 10, 14, 15, 0, tzinfo=MSK))
    assert (await cache.ensure_fresh(db, clock.now())).catalog["A"].price.in_per_1k == Decimal("30")
