"""T020: приёмочные тесты сценария 1 — решение перед вызовом (10_tech_spec.md, сценарии 1.1–1.7).

Все решения идут через `POST /api/v1/decisions`, каждый ответ проверяется `assert_matches_contract`.
Алгоритм — 12_arch_decision.md §4.5. Суммы — по каталогу tests/fixtures/qa.py (A = 30 ₽, C = 5 ₽, Z = 0 ₽).

Тесты написаны до реализации; пометка xfail модуля снята в T021 (реализация готова).
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from tests.fixtures.qa import (
    call_record,
    catalog,
    decide,
    decision_body,
    msk,
    reserved,
    same_instant,
    spend,
)
from tests.fixtures.seed import Emp, Seed

pytestmark = pytest.mark.db

TODAY_MINUS_1 = msk(2026, 10, 13).date()  # вторник той же недели


@dataclass
class World:
    dep: UUID
    emp: Emp
    caller_id: UUID
    key: str


async def world(
    seed: Seed,
    db: asyncpg.Connection,
    *,
    allowed: tuple[str, ...] = ("A", "B"),
    month_limit: int | None = 10_000_000,
    dept_spent: int = 0,
    mode: str = "enforce",
    emp_limits: dict[str, int] | None = None,
    emp_spent_today: int = 0,
) -> World:
    await catalog(seed)
    limits = {"month": month_limit} if month_limit is not None else None
    dep = await seed.department("Маркетинг", policy_mode=mode, limits=limits, allowed=allowed)
    emp = await seed.employee("ivanov", department=dep, limits=emp_limits)
    if dept_spent:
        await spend(db, "department", dep, dept_spent)
    if emp_spent_today:
        await spend(db, "employee", emp.id, emp_spent_today)
        await spend(db, "department", dep, emp_spent_today)
    caller_id, key = await seed.caller_key("gateway")
    return World(dep, emp, caller_id, key)


# ---------------------------------------------------------------- 1.1 разрешить


async def test_1_1_allow_creates_reserve(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    w = await world(seed, db, allowed=("A", "B"), month_limit=10_000_000, dept_spent=5_000_000)
    d = await decide(client, w.key, decision_body(model="A"))
    assert d["outcome"] == "allow"
    assert d["model"] == "A" and d["requested_model"] == "A"
    assert d["reserve_rub"] == "30.00"
    assert d["code"] is None and d.get("reason") is None
    assert d["http_status_hint"] == 200
    assert d["policy_mode"] == "enforce" and d["fail_mode"] == "open"
    assert d["department_id"] == str(w.dep)
    assert d["budget_blocked_by_subscriptions"] is False
    UUID(d["decision_id"])
    assert same_instant(d["expires_at"], clock.now() + timedelta(minutes=15))
    assert d["estimate"] == {"input_tokens": 1000, "output_tokens": 400, "source": "request"}
    # резерв учтён у подразделения и в журнале
    assert await reserved(db, "department", w.dep) == 3000
    row = await call_record(db, d["decision_id"])
    assert (row["outcome"], row["final_model"], row["reserve_kop"], row["status"]) == (
        "allow",
        "A",
        3000,
        "open",
    )
    assert row["employee_id"] == w.emp.id and row["department_id"] == w.dep
    assert row["caller_id"] == w.caller_id


async def test_1_1_estimate_defaults_from_catalog(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    """Нет оценки токенов — берутся default_in/default_out модели (допущения спецификации)."""
    w = await world(seed, db)
    d = await decide(client, w.key, decision_body(model="A", input_tokens=None, max_output_tokens=None))
    assert d["outcome"] == "allow" and d["reserve_rub"] == "30.00"
    assert d["estimate"] == {"input_tokens": 1000, "output_tokens": 400, "source": "catalog_default"}


# ---------------------------------------------------------------- 1.2 модель не разрешена


async def test_1_2_model_not_allowed(client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection) -> None:
    w = await world(seed, db, allowed=("B",))
    d = await decide(client, w.key, decision_body(model="A"))
    assert d["outcome"] == "deny" and d["code"] == "model_not_allowed"
    assert d["http_status_hint"] == 403
    assert d["model"] is None and d["reserve_rub"] == "0.00"
    assert await reserved(db, "department", w.dep) == 0
    row = await call_record(db, d["decision_id"])  # событие пишется в журнал
    assert (row["outcome"], row["code"], row["reserve_kop"]) == ("deny", "model_not_allowed", 0)


# ---------------------------------------------------------------- 1.3 перенаправить


async def test_1_3_redirect_to_cheapest_compatible(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    # остаток 20 ₽; A = 30 ₽ не помещается; N дешевле C, но без инструментов — значит C (5 ₽)
    w = await world(seed, db, allowed=("A", "C", "N"), month_limit=10_000_000, dept_spent=9_998_000)
    d = await decide(client, w.key, decision_body(model="A", tools=True))
    assert d["outcome"] == "redirect"
    assert d["requested_model"] == "A" and d["model"] == "C"
    assert d["reason"] == "budget_exceeded" and d["code"] is None
    assert d["reserve_rub"] == "5.00" and d["http_status_hint"] == 200
    assert await reserved(db, "department", w.dep) == 500
    row = await call_record(db, d["decision_id"])
    assert (row["outcome"], row["requested_model"], row["final_model"]) == ("redirect", "A", "C")


# ---------------------------------------------------------------- 1.4 отказ budget_exceeded


async def test_1_4_deny_when_nothing_fits(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    # остаток 1 ₽: не помещаются ни A (30 ₽), ни C (5 ₽)
    w = await world(seed, db, allowed=("A", "C"), month_limit=10_000_000, dept_spent=9_999_900)
    d = await decide(client, w.key, decision_body(model="A"))
    assert d["outcome"] == "deny" and d["code"] == "budget_exceeded"
    assert d["http_status_hint"] == 429 and d["reserve_rub"] == "0.00" and d["model"] is None
    lim = d["applied_limit"]
    assert lim["owner_type"] == "department" and lim["owner_ref"] == str(w.dep)
    assert lim["period"] == "month"
    assert lim["limit_rub"] == "100000.00" and lim["remaining_rub"] == "1.00"
    assert same_instant(lim["period_start"], msk(2026, 10, 1))
    assert same_instant(lim["resets_at"], msk(2026, 11, 1))
    assert await reserved(db, "department", w.dep) == 0


async def test_1_4_deny_when_cheap_models_lack_capabilities(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    # остаток 20 ₽: N (2.50 ₽) помещается, но не умеет инструменты и JSON
    w = await world(seed, db, allowed=("A", "N", "Z"), month_limit=10_000_000, dept_spent=9_998_000)
    d = await decide(client, w.key, decision_body(model="A", tools=True, json_mode=True))
    assert d["outcome"] == "deny" and d["code"] == "budget_exceeded"
    assert d["applied_limit"]["period"] == "month"


async def test_1_4_context_requirement_excludes_small_models(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    # Z бесплатна, но контекст 8k < 16k
    w = await world(seed, db, allowed=("A", "Z"), month_limit=10_000_000, dept_spent=10_000_000)
    d = await decide(client, w.key, decision_body(model="A", context_tokens=16_000))
    assert d["outcome"] == "deny" and d["code"] == "budget_exceeded"


# ---------------------------------------------------------------- 1.5 эмбеддинги


async def test_1_5_embeddings_never_redirected(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    w = await world(seed, db, allowed=("E", "Z", "C"), month_limit=10_000_000, dept_spent=10_000_000)
    d = await decide(client, w.key, decision_body(model="E", kind="embedding", max_output_tokens=0))
    assert d["outcome"] == "deny" and d["code"] == "budget_exceeded"
    assert d["model"] is None and d["http_status_hint"] == 429


# ---------------------------------------------------------------- 1.6 самый строгий лимит


async def test_1_6_strictest_limit_personal_day_redirects_to_zero_price(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    w = await world(
        seed,
        db,
        allowed=("A", "C", "Z"),
        month_limit=10_000_000,
        emp_limits={"day": 50_000},
        emp_spent_today=50_000,
    )
    d = await decide(client, w.key, decision_body(model="A"))
    assert d["outcome"] == "redirect" and d["model"] == "Z"
    assert d["reserve_rub"] == "0.00" and d["reason"] == "budget_exceeded"
    lim = d["applied_limit"]
    assert (lim["owner_type"], lim["owner_ref"], lim["period"]) == ("employee", w.emp.pseudonym, "day")
    assert lim["remaining_rub"] == "0.00"
    assert same_instant(lim["resets_at"], msk(2026, 10, 15))


async def test_1_6_strictest_limit_personal_day_denies(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    w = await world(
        seed,
        db,
        allowed=("A", "C"),
        month_limit=10_000_000,
        emp_limits={"day": 50_000},
        emp_spent_today=50_000,
    )
    d = await decide(client, w.key, decision_body(model="A"))
    assert d["outcome"] == "deny" and d["code"] == "budget_exceeded"
    lim = d["applied_limit"]
    assert (lim["owner_type"], lim["period"]) == ("employee", "day")
    assert same_instant(lim["resets_at"], msk(2026, 10, 15))


# ---------------------------------------------------------------- 1.7 режим наблюдения


@pytest.mark.parametrize("mode", ["observe", "warn"])
async def test_1_7_observe_allows_over_limit_and_logs(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, mode: str
) -> None:
    w = await world(seed, db, allowed=("A", "C"), month_limit=10_000_000, dept_spent=10_000_000, mode=mode)
    d = await decide(client, w.key, decision_body(model="A"))
    assert d["outcome"] == "allow" and d["model"] == "A"
    assert d["observed_violation"] == "budget_exceeded"
    assert d["policy_mode"] == mode
    assert d["reserve_rub"] == "30.00" and d["http_status_hint"] == 200
    row = await call_record(db, d["decision_id"])
    assert "over_limit_observed" in row["flags"]


async def test_1_7_observe_model_not_allowed_is_allowed(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    w = await world(seed, db, allowed=("B",), mode="observe")
    d = await decide(client, w.key, decision_body(model="A"))
    assert d["outcome"] == "allow" and d["observed_violation"] == "model_not_allowed"
    row = await call_record(db, d["decision_id"])
    assert "model_not_allowed_observed" in row["flags"]


# ---------------------------------------------------------------- все лимиты одновременно (3.3 в решениях)


async def test_all_applicable_limits_are_checked(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    """Лимиты сотрудника и подразделения × день/неделя/месяц: решение по минимальному остатку."""
    await catalog(seed)
    dep = await seed.department(
        "Маркетинг", limits={"day": 500_000, "week": 2_000_000, "month": 6_000_000}, allowed=("A", "C")
    )
    emp = await seed.employee("ivanov", department=dep, limits={"week": 100_000})
    await spend(db, "employee", emp.id, 99_000, day=TODAY_MINUS_1)
    await spend(db, "department", dep, 99_000, day=TODAY_MINUS_1)
    _, key = await seed.caller_key()
    d = await decide(client, key, decision_body(model="A"))
    # личный недельный остаток 10 ₽ — самый строгий: A (30 ₽) не помещается, C (5 ₽) — да
    assert d["outcome"] == "redirect" and d["model"] == "C"
    lim = d["applied_limit"]
    assert (lim["owner_type"], lim["period"], lim["remaining_rub"]) == ("employee", "week", "10.00")
    assert same_instant(lim["period_start"], msk(2026, 10, 12))
    assert same_instant(lim["resets_at"], msk(2026, 10, 19))
