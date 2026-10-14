"""Снимок конфигурации в памяти процесса (12_arch_decision.md §4.4, ADR-002).

Горячий путь решения не читает справочники из БД: `SnapshotCache.ensure_fresh` делает один запрос
к `config_version` и перечитывает снимок, только если:
- версия изменилась (любой `config.apply` увеличивает её в той же транзакции, что и изменение);
- наступили следующие сутки МСК (назначения сотрудников «на сегодня»);
- вступила в силу цена модели с `valid_from` в будущем.

Лимиты хранятся в снимке; расход и резервы — нет (их читает транзакция решения под блокировкой).
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg

from bm.domain.periods import MSK, msk_date
from bm.domain.types import Kop, ModelInfo, PeriodKind, Price


@dataclass(frozen=True, slots=True)
class DepartmentCfg:
    id: UUID
    name: str
    ad_group: str | None
    policy_mode: str  # observe | warn | enforce
    fail_mode: str  # open | closed
    subs_warn_pct: int
    allowed: frozenset[str]  # пустое множество — «ничего не разрешено»


@dataclass(frozen=True, slots=True)
class EmployeeCfg:
    """Сотрудник с действующим на сегодня (МСК) назначением."""

    id: UUID
    pseudonym: str
    login: str
    department_id: UUID


@dataclass(frozen=True, slots=True)
class Assignment:
    employee_id: UUID
    department_id: UUID
    valid_from: date
    valid_to: date | None


@dataclass(frozen=True, slots=True)
class CallerKey:
    id: UUID
    name: str
    scope_type: str  # service | department_employees | any_employee
    scope_department_id: UUID | None
    provider_source_id: UUID | None
    active: bool  # False — ключ отозван (401)


@dataclass(frozen=True, slots=True)
class SubscriptionCfg:
    id: UUID
    provider: str
    plan: str
    seat_price: Decimal
    currency: Literal["RUB", "USD"]
    seats: int
    start_date: date
    end_date: date | None
    assignee_type: str  # employee | department | unallocated
    assignee_id: UUID | None


type OwnerKey = tuple[str, UUID]


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    version: int
    loaded_at: datetime
    valid_until: datetime  # после этого момента снимок перечитывается даже без смены версии
    departments: Mapping[UUID, DepartmentCfg]
    employees_by_login: Mapping[str, EmployeeCfg]
    assignments: tuple[Assignment, ...]
    limits: Mapping[OwnerKey, Mapping[PeriodKind, Kop]]
    catalog: Mapping[str, ModelInfo]
    fx: Mapping[date, Decimal]
    keys: Mapping[bytes, CallerKey]  # SHA-256 ключа → ключ
    subscriptions: tuple[SubscriptionCfg, ...]

    def fx_for(self, month: date) -> Decimal | None:
        return self.fx.get(month.replace(day=1))

    def limits_for(self, owner_type: str, owner_id: UUID) -> Mapping[PeriodKind, Kop]:
        return self.limits.get((owner_type, owner_id), {})


async def _read(conn: asyncpg.Connection, now: datetime) -> ConfigSnapshot:
    # Версия читается первой: данные могут оказаться только новее версии, но не старше — тогда следующий
    # ensure_fresh увидит большую версию и перечитает снимок.
    version: int = await conn.fetchval("SELECT version FROM config_version")
    today = msk_date(now)

    allowed: dict[UUID, set[str]] = {}
    for r in await conn.fetch("SELECT department_id, model_id FROM dept_model_allow"):
        allowed.setdefault(r["department_id"], set()).add(r["model_id"])
    departments = {
        r["id"]: DepartmentCfg(
            id=r["id"],
            name=r["name"],
            ad_group=r["ad_group"],
            policy_mode=r["policy_mode"],
            fail_mode=r["fail_mode"],
            subs_warn_pct=r["subs_warn_pct"],
            allowed=frozenset(allowed.get(r["id"], ())),
        )
        for r in await conn.fetch(
            "SELECT id, name, ad_group, policy_mode, fail_mode, subs_warn_pct FROM department"
        )
    }

    assignments = tuple(
        Assignment(r["employee_id"], r["department_id"], r["valid_from"], r["valid_to"])
        for r in await conn.fetch(
            "SELECT employee_id, department_id, valid_from, valid_to FROM employee_assignment ORDER BY id"
        )
    )
    employees_by_login = {
        r["ad_login"]: EmployeeCfg(r["id"], r["pseudonym"], r["ad_login"], r["department_id"])
        for r in await conn.fetch(
            "SELECT e.id, e.pseudonym, e.ad_login, a.department_id FROM employee e "
            "JOIN employee_assignment a ON a.employee_id = e.id "
            "WHERE e.active AND a.valid_from <= $1 AND (a.valid_to IS NULL OR a.valid_to >= $1)",
            today,
        )
    }

    limits: dict[OwnerKey, dict[PeriodKind, Kop]] = {}
    for r in await conn.fetch("SELECT owner_type, owner_id, period, amount_kop FROM limit_def"):
        limits.setdefault((r["owner_type"], r["owner_id"]), {})[PeriodKind(r["period"])] = r["amount_kop"]

    catalog = {
        r["id"]: ModelInfo(
            id=r["id"],
            kind=r["kind"],
            supports_tools=r["supports_tools"],
            supports_json=r["supports_json"],
            max_context=r["max_context"],
            default_in=r["default_in"],
            default_out=r["default_out"],
            tie_order=r["tie_order"],
            active=r["active"],
            price=Price(
                id=r["price_id"],
                in_per_1k=r["in_per_1k"],
                out_per_1k=r["out_per_1k"],
                cached_per_1k=r["cached_per_1k"],
                currency=r["currency"],
            ),
        )
        for r in await conn.fetch(
            "SELECT m.id, m.kind, m.supports_tools, m.supports_json, m.max_context, m.default_in, "
            "m.default_out, "
            "m.tie_order, m.active, p.id AS price_id, p.in_per_1k, p.out_per_1k, p.cached_per_1k, p.currency "
            "FROM model m JOIN LATERAL (SELECT * FROM model_price mp "
            "WHERE mp.model_id = m.id AND mp.valid_from <= $1 "
            "ORDER BY mp.valid_from DESC LIMIT 1) p ON true",
            now,
        )
    }
    next_price: datetime | None = await conn.fetchval(
        "SELECT min(valid_from) FROM model_price WHERE valid_from > $1", now
    )

    fx = {r["month"]: r["usd_rub"] for r in await conn.fetch("SELECT month, usd_rub FROM fx_rate")}

    keys = {
        bytes(r["key_sha256"]): CallerKey(
            id=r["id"],
            name=r["name"],
            scope_type=r["scope_type"],
            scope_department_id=r["scope_department_id"],
            provider_source_id=r["provider_source_id"],
            active=r["status"] == "active",
        )
        for r in await conn.fetch(
            "SELECT id, name, key_sha256, scope_type, scope_department_id, provider_source_id, status "
            "FROM caller_system"
        )
    }

    subscriptions = tuple(
        SubscriptionCfg(
            id=r["id"],
            provider=r["provider"],
            plan=r["plan"],
            seat_price=r["seat_price"],
            currency=r["currency"],
            seats=r["seats"],
            start_date=r["start_date"],
            end_date=r["end_date"],
            assignee_type=r["assignee_type"],
            assignee_id=r["assignee_id"],
        )
        for r in await conn.fetch(
            "SELECT id, provider, plan, seat_price, currency, seats, start_date, end_date, assignee_type, "
            "assignee_id "
            "FROM subscription WHERE deleted_at IS NULL ORDER BY start_date, id"
        )
    )

    next_midnight = datetime.combine(today + timedelta(days=1), time(0), tzinfo=MSK)
    valid_until = min(next_midnight, next_price) if next_price is not None else next_midnight
    return ConfigSnapshot(
        version=version,
        loaded_at=now,
        valid_until=valid_until,
        departments=departments,
        employees_by_login=employees_by_login,
        assignments=assignments,
        limits=limits,
        catalog=catalog,
        fx=fx,
        keys=keys,
        subscriptions=subscriptions,
    )


async def load(conn: asyncpg.Connection, now: datetime) -> ConfigSnapshot:
    """Читает снимок. Вне транзакции — в одной транзакции REPEATABLE READ (согласованное чтение)."""
    if conn.is_in_transaction():
        return await _read(conn, now)
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        return await _read(conn, now)


class SnapshotCache:
    """Снимок процесса api. Один экземпляр на приложение; безопасен для конкурентных запросов."""

    def __init__(self) -> None:
        self._snapshot: ConfigSnapshot | None = None
        self._lock = asyncio.Lock()

    def _fresh(self, version: int, now: datetime) -> ConfigSnapshot | None:
        snap = self._snapshot
        if snap is not None and snap.version == version and now < snap.valid_until:
            return snap
        return None

    async def ensure_fresh(self, conn: asyncpg.Connection, now: datetime) -> ConfigSnapshot:
        """Один запрос к `config_version`, если снимок актуален; иначе перечитывает снимок."""
        version: int = await conn.fetchval("SELECT version FROM config_version")
        snap = self._fresh(version, now)
        if snap is not None:
            return snap
        async with self._lock:
            snap = self._fresh(version, now)
            if snap is None:
                snap = await load(conn, now)
                self._snapshot = snap
            return snap
