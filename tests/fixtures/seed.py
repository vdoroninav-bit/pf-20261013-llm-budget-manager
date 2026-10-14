"""Построитель тестовых данных прямыми INSERT (T014). Без бизнес-логики: только то, что лежит в БД.

Пример:
    dep = await seed.department("Маркетинг", policy_mode="enforce", limits={"month": 10_000_000}, allowed=["A"])
    emp = await seed.employee("ivanov", department=dep, limits={"day": 50_000})
    await seed.model("A", in_per_1k="10", out_per_1k="50")
    caller_id, api_key = await seed.caller_key("gateway")
"""

import hashlib
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from bm.domain.periods import MSK

PRICE_FROM = datetime(2026, 1, 1, tzinfo=MSK)


@dataclass(frozen=True)
class Emp:
    id: UUID
    pseudonym: str
    login: str


class Seed:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self.conn = conn

    async def department(
        self,
        name: str = "Маркетинг",
        *,
        policy_mode: str = "enforce",
        fail_mode: str = "open",
        ad_group: str | None = None,
        subs_warn_pct: int = 50,
        limits: Mapping[str, int] | None = None,
        allowed: Iterable[str] = (),
    ) -> UUID:
        dep: UUID = await self.conn.fetchval(
            "INSERT INTO department(name, ad_group, policy_mode, fail_mode, subs_warn_pct) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            name,
            ad_group,
            policy_mode,
            fail_mode,
            subs_warn_pct,
        )
        if limits:
            await self.limits("department", dep, **limits)
        models = list(allowed)
        if models:
            await self.allow(dep, *models)
        return dep

    async def employee(
        self,
        login: str = "ivanov",
        *,
        department: UUID | None = None,
        valid_from: date = date(2026, 10, 1),
        display_name: str | None = None,
        limits: Mapping[str, int] | None = None,
    ) -> Emp:
        pseudonym = f"emp-{secrets.token_hex(5)}"
        emp_id: UUID = await self.conn.fetchval(
            "INSERT INTO employee(pseudonym, ad_login, display_name) VALUES ($1, $2, $3) RETURNING id",
            pseudonym,
            login,
            display_name,
        )
        if department is not None:
            await self.conn.execute(
                "INSERT INTO employee_assignment(employee_id, department_id, valid_from) VALUES ($1, $2, $3)",
                emp_id,
                department,
                valid_from,
            )
        if limits:
            await self.limits("employee", emp_id, **limits)
        return Emp(emp_id, pseudonym, login)

    async def model(
        self,
        model_id: str = "A",
        *,
        provider: str = "internal",
        kind: str = "chat",
        in_per_1k: str = "10",
        out_per_1k: str = "50",
        cached_per_1k: str | None = None,
        currency: str = "RUB",
        tools: bool = True,
        json: bool = True,
        max_context: int = 200_000,
        default_in: int = 1000,
        default_out: int = 400,
        tie_order: int = 100,
        active: bool = True,
        price_from: datetime = PRICE_FROM,
    ) -> str:
        await self.conn.execute(
            "INSERT INTO model(id, provider, kind, supports_tools, supports_json, max_context, default_in, "
            "default_out, tie_order, active) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            model_id,
            provider,
            kind,
            tools,
            json,
            max_context,
            default_in,
            default_out,
            tie_order,
            active,
        )
        await self.price(model_id, in_per_1k, out_per_1k, cached_per_1k=cached_per_1k, currency=currency, valid_from=price_from)
        return model_id

    async def price(
        self,
        model_id: str,
        in_per_1k: str,
        out_per_1k: str,
        *,
        cached_per_1k: str | None = None,
        currency: str = "RUB",
        valid_from: datetime = PRICE_FROM,
    ) -> UUID:
        price_id: UUID = await self.conn.fetchval(
            "INSERT INTO model_price(model_id, valid_from, in_per_1k, out_per_1k, cached_per_1k, currency) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            model_id,
            valid_from,
            Decimal(in_per_1k),
            Decimal(out_per_1k),
            None if cached_per_1k is None else Decimal(cached_per_1k),
            currency,
        )
        return price_id

    async def limits(
        self, owner_type: str, owner_id: UUID, *, day: int | None = None, week: int | None = None, month: int | None = None
    ) -> None:
        for period, amount in (("day", day), ("week", week), ("month", month)):
            if amount is None:
                continue
            await self.conn.execute(
                "INSERT INTO limit_def(owner_type, owner_id, period, amount_kop) VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (owner_type, owner_id, period) DO UPDATE SET amount_kop = EXCLUDED.amount_kop",
                owner_type,
                owner_id,
                period,
                amount,
            )

    async def allow(self, department: UUID, *models: str) -> None:
        await self.conn.executemany(
            "INSERT INTO dept_model_allow(department_id, model_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            [(department, m) for m in models],
        )

    async def fx(self, month: date, usd_rub: str, entered_by: str = "admin") -> None:
        await self.conn.execute(
            "INSERT INTO fx_rate(month, usd_rub, entered_by) VALUES ($1, $2, $3)",
            month,
            Decimal(usd_rub),
            entered_by,
        )

    async def caller_key(
        self,
        name: str = "gateway",
        *,
        scope_type: str = "any_employee",
        department: UUID | None = None,
        status: str = "active",
    ) -> tuple[UUID, str]:
        """Ключ вызывающей системы `bm_<prefix>_<secret>`; в БД — только SHA-256. Возвращает (id, ключ)."""
        prefix = secrets.token_hex(4)
        key = f"bm_{prefix}_{secrets.token_urlsafe(24)}"
        caller_id: UUID = await self.conn.fetchval(
            "INSERT INTO caller_system(name, key_prefix, key_sha256, scope_type, scope_department_id, status) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            name,
            prefix,
            hashlib.sha256(key.encode()).digest(),
            scope_type,
            department,
            status,
        )
        return caller_id, key

    async def admin_user(
        self,
        login: str = "admin",
        password: str = "correct-horse-battery",
        *,
        role: str = "admin",
        department: UUID | None = None,
        active: bool = True,
        must_change_password: bool = False,
    ) -> UUID:
        from bm.access.auth import hash_password

        user_id: UUID = await self.conn.fetchval(
            "INSERT INTO admin_user(login, argon2_hash, role, department_id, active, must_change_password) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            login,
            hash_password(password),
            role,
            department,
            active,
            must_change_password,
        )
        return user_id
