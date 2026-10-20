"""T020: пограничные случаи решений (10_tech_spec.md, «Пограничные случаи»; 12_arch_decision.md §4.5).

subject_unknown, model_unknown, модель удалена из разрешённых, нулевая цена, равная цена, отрицательный
остаток, подписки ≥ 100%, subject_out_of_scope → 403 + аудит, http_status_hint для каждого кода,
отказ без ключа и лишние поля (принцип I).

Написаны до реализации; пометка xfail модуля снята в T021.
Тест подписок ≥ 100% ждал T051 (амортизация в decide); пометка снята в T051.
"""

from datetime import date
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest

from tests.contract.helpers import assert_matches_contract
from tests.fixtures.qa import (
    bump_config,
    call_record,
    catalog,
    decide,
    decision_body,
    post_decision,
    reserved,
    spend,
)
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db


async def _base(
    seed: Seed, *, allowed: tuple[str, ...] = ("A", "C"), month_limit: int = 10_000_000
) -> tuple[UUID, str]:
    await catalog(seed)
    dep = await seed.department("Маркетинг", limits={"month": month_limit}, allowed=allowed)
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key()
    return dep, key


# ---------------------------------------------------------------- субъект


async def test_subject_unknown_login(client: httpx.AsyncClient, seed: Seed) -> None:
    _, key = await _base(seed)
    d = await decide(client, key, decision_body(login="nobody"))
    assert d["outcome"] == "deny" and d["code"] == "subject_unknown"
    assert d["http_status_hint"] == 422 and d["reserve_rub"] == "0.00"


async def test_subject_without_active_assignment_is_unknown(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    """Сотрудник выведен из подразделения (нет в файле AD) — отказ subject_unknown."""
    dep, key = await _base(seed)
    emp = await seed.employee("petrov", department=dep, valid_from=date(2026, 9, 1))
    await db.execute(
        "UPDATE employee_assignment SET valid_to = $2 WHERE employee_id = $1", emp.id, date(2026, 10, 13)
    )
    d = await decide(client, key, decision_body(login="petrov"))
    assert d["outcome"] == "deny" and d["code"] == "subject_unknown"


async def test_service_subject_uses_key_department(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, _ = await _base(seed)
    _, svc_key = await seed.caller_key("batch-job", scope_type="service", department=dep)
    d = await decide(client, svc_key, decision_body(login=None))
    assert d["outcome"] == "allow" and d["department_id"] == str(dep)
    assert await reserved(db, "department", dep) == 3000


# ---------------------------------------------------------------- модель


async def test_model_unknown(client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection) -> None:
    _, key = await _base(seed)
    d = await decide(client, key, decision_body(model="gpt-unknown"))
    assert d["outcome"] == "deny" and d["code"] == "model_unknown"
    assert d["http_status_hint"] == 422
    row = await call_record(db, d["decision_id"])  # событие видно администратору (3.6)
    assert (row["outcome"], row["code"], row["requested_model"]) == ("deny", "model_unknown", "gpt-unknown")


async def test_inactive_model_is_unknown(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    _, key = await _base(seed)
    await db.execute("UPDATE model SET active = false WHERE id = 'A'")
    d = await decide(client, key, decision_body(model="A"))
    assert d["outcome"] == "deny" and d["code"] == "model_unknown"


async def test_model_removed_from_allowed_denies_new_decisions(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, key = await _base(seed, allowed=("A", "C"))
    first = await decide(client, key, decision_body(model="A"))
    assert first["outcome"] == "allow"
    await db.execute("DELETE FROM dept_model_allow WHERE department_id = $1 AND model_id = 'A'", dep)
    await bump_config(db)
    second = await decide(client, key, decision_body(model="A"))
    assert second["outcome"] == "deny" and second["code"] == "model_not_allowed"
    # незакрытый резерв первого решения остаётся до отчёта или срока
    row = await call_record(db, first["decision_id"])
    assert row["status"] == "open" and await reserved(db, "department", dep) == 3000


async def test_empty_allowed_list_denies_everything(client: httpx.AsyncClient, seed: Seed) -> None:
    _, key = await _base(seed, allowed=())
    d = await decide(client, key, decision_body(model="C"))
    assert d["outcome"] == "deny" and d["code"] == "model_not_allowed"


# ---------------------------------------------------------------- цена


async def test_zero_price_model_always_fits(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, key = await _base(seed, allowed=("Z",), month_limit=100_000)
    await spend(db, "department", dep, 100_000)  # остаток 0
    d = await decide(client, key, decision_body(model="Z"))
    assert d["outcome"] == "allow" and d["model"] == "Z" and d["reserve_rub"] == "0.00"


async def test_equal_price_uses_tie_order_then_id(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    await catalog(seed, only="A")
    for mid, tie in (("C2", 20), ("C1", 30), ("C3", 20)):
        await seed.model(mid, in_per_1k="1", out_per_1k="10", tie_order=tie)
    dep = await seed.department("Маркетинг", limits={"month": 10_000_000}, allowed=("A", "C1", "C2", "C3"))
    await seed.employee("ivanov", department=dep)
    await spend(db, "department", dep, 9_998_000)  # остаток 20 ₽
    _, key = await seed.caller_key()
    d = await decide(client, key, decision_body(model="A"))
    # цена равна у C1, C2, C3; tie_order 20 у C2 и C3 → по id: C2
    assert d["outcome"] == "redirect" and d["model"] == "C2"


async def test_negative_remaining_only_zero_price_redirect(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, key = await _base(seed, allowed=("A", "C", "Z"), month_limit=100_000)
    await spend(db, "department", dep, 150_000, source="import")  # поздний импорт: остаток −500 ₽
    d = await decide(client, key, decision_body(model="A"))
    assert d["outcome"] == "redirect" and d["model"] == "Z" and d["reserve_rub"] == "0.00"
    assert d["applied_limit"]["remaining_rub"] == "-500.00"


async def test_negative_remaining_without_zero_price_denies(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep, key = await _base(seed, allowed=("A", "C"), month_limit=100_000)
    await spend(db, "department", dep, 150_000, source="import")
    d = await decide(client, key, decision_body(model="C"))
    assert d["outcome"] == "deny" and d["code"] == "budget_exceeded"
    assert d["applied_limit"]["remaining_rub"] == "-500.00"


async def test_subscriptions_over_100_percent_block_paid_calls(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    await catalog(seed)
    dep = await seed.department("Маркетинг", limits={"day": 5_000}, allowed=("A", "C"))
    await seed.employee("ivanov", department=dep)
    sub_id = await db.fetchval(
        "INSERT INTO subscription(provider, plan, seat_price, currency, seats, start_date, assignee_type, assignee_id) "
        "VALUES ('anthropic', 'Team', 3100, 'RUB', 1, '2026-10-01', 'department', $1) RETURNING id",
        dep,
    )
    # M = 3 100 ₽ в октябре (31 день) → 100 ₽ в сутки ≥ дневного лимита 50 ₽
    await db.execute(
        "INSERT INTO subscription_month(subscription_id, month, m_total_kop) VALUES ($1, '2026-10-01', 310000)",
        sub_id,
    )
    _, key = await seed.caller_key()
    d = await decide(client, key, decision_body(model="C"))
    assert d["outcome"] == "deny" and d["code"] == "budget_exceeded"
    assert d["budget_blocked_by_subscriptions"] is True
    assert d["applied_limit"]["period"] == "day"
    assert "подписк" in d["message"]


# ---------------------------------------------------------------- доступ вызывающей системы


async def test_subject_out_of_scope_is_403_and_audited(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    await catalog(seed)
    dep_a = await seed.department("Маркетинг", limits={"month": 10_000_000}, allowed=("A",))
    dep_b = await seed.department("Разработка", limits={"month": 10_000_000}, allowed=("A",))
    await seed.employee("ivanov", department=dep_a)
    caller_id, key = await seed.caller_key("dev-gw", scope_type="department_employees", department=dep_b)
    resp = await post_decision(client, key, decision_body(login="ivanov"))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "subject_out_of_scope"
    assert_matches_contract(resp, "createDecision")
    row = await db.fetchrow(
        "SELECT actor, action FROM audit_log WHERE actor = $1 ORDER BY id DESC LIMIT 1", f"caller:{caller_id}"
    )
    assert row is not None and row["action"] == "forbidden_access"
    assert await db.fetchval("SELECT count(*) FROM call_record") == 0  # не решение, а ошибка доступа
    assert await reserved(db, "department", dep_a) == 0


async def test_service_key_cannot_speak_for_employee(client: httpx.AsyncClient, seed: Seed) -> None:
    dep, _ = await _base(seed)
    _, svc_key = await seed.caller_key("batch-job", scope_type="service", department=dep)
    resp = await post_decision(client, svc_key, decision_body(login="ivanov"))
    assert resp.status_code == 403 and resp.json()["code"] == "subject_out_of_scope"
    assert_matches_contract(resp, "createDecision")


@pytest.mark.parametrize("variant", ["missing", "garbage", "revoked"])
async def test_without_valid_key_is_401(client: httpx.AsyncClient, seed: Seed, variant: str) -> None:
    await _base(seed)
    headers: dict[str, str] = {}
    if variant == "garbage":
        headers = {"X-Api-Key": "bm_deadbeef_not-a-key"}
    elif variant == "revoked":
        _, key = await seed.caller_key("old", status="revoked")
        headers = {"X-Api-Key": key}
    resp = await client.post("/api/v1/decisions", json=decision_body(), headers=headers)
    assert resp.status_code == 401 and resp.json()["code"] == "unauthorized"
    assert_matches_contract(resp, "createDecision")


@pytest.mark.parametrize("field", ["prompt", "messages"])
async def test_extra_fields_rejected_no_content_accepted(
    client: httpx.AsyncClient, seed: Seed, field: str
) -> None:
    """Принцип I: содержимое запроса модели не принимается — лишние поля дают 422."""
    _, key = await _base(seed)
    body: dict[str, Any] = decision_body() | {field: "секретный текст запроса"}
    resp = await post_decision(client, key, body)
    assert resp.status_code == 422 and resp.json()["code"] == "validation_error"
    assert_matches_contract(resp, "createDecision")
    assert "секретный текст" not in resp.text


# ---------------------------------------------------------------- http_status_hint для каждого кода


HINT_CASES = {
    "allow": (dict(model="C"), "allow", None, 200),
    "redirect": (dict(model="A"), "redirect", None, 200),
    "model_not_allowed": (dict(model="B"), "deny", "model_not_allowed", 403),
    "budget_exceeded": (
        dict(model="E", kind="embedding", max_output_tokens=0),
        "deny",
        "budget_exceeded",
        429,
    ),
    "model_unknown": (dict(model="X-404"), "deny", "model_unknown", 422),
    "subject_unknown": (dict(login="ghost"), "deny", "subject_unknown", 422),
}


@pytest.mark.parametrize("case", list(HINT_CASES))
async def test_http_status_hint_for_each_outcome(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, case: str
) -> None:
    kw, outcome, code, hint = HINT_CASES[case]
    dep, key = await _base(seed, allowed=("A", "C", "E"), month_limit=10_000_000)
    # остаток 10 ₽: C (5 ₽) помещается, A (30 ₽) → C, E (0.10 ₽)… помещается — поэтому для E обнуляем остаток
    await spend(db, "department", dep, 9_999_000 if case != "budget_exceeded" else 10_000_000)
    d = await decide(client, key, decision_body(**kw))  # type: ignore[arg-type]
    assert (d["outcome"], d["code"], d["http_status_hint"]) == (outcome, code, hint)
