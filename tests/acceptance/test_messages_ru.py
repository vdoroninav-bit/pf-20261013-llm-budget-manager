"""T020: сообщения на русском (принцип IX, 12_plan.md §4): `Decision.message` и `Problem.detail`.

- `message` любого решения содержит кириллицу;
- при отказе по бюджету в `message` есть дата и время сброса по МСК (сценарий 1.4) — и они не зависят
  от часового пояса процесса;
- `detail` ошибок вызывающей системы (401, 403, 422) — на русском.

Написаны до реализации; пометка xfail модуля снята в T021.
"""

import os
import time

import asyncpg
import httpx
import pytest

from tests.fixtures.qa import CYRILLIC, catalog, decide, decision_body, post_decision, spend
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db


async def _setup(
    seed: Seed, db: asyncpg.Connection, *, dept_spent: int = 0, emp_day: int | None = None
) -> str:
    await catalog(seed)
    dep = await seed.department("Маркетинг", limits={"month": 10_000_000}, allowed=("A", "C"))
    emp = await seed.employee(
        "ivanov", department=dep, limits={"day": emp_day} if emp_day is not None else None
    )
    if dept_spent:
        await spend(db, "department", dep, dept_spent)
    if emp_day is not None:
        await spend(db, "employee", emp.id, emp_day)
        await spend(db, "department", dep, emp_day)
    _, key = await seed.caller_key()
    return key


@pytest.mark.parametrize(
    "kw, outcome",
    [
        (dict(model="A"), "allow"),
        (dict(model="B"), "deny"),  # не разрешена
        (dict(model="X-404"), "deny"),  # нет в каталоге
        (dict(login="ghost"), "deny"),  # нет сотрудника
    ],
)
async def test_message_is_russian_for_every_outcome(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, kw: dict[str, str], outcome: str
) -> None:
    key = await _setup(seed, db)
    d = await decide(client, key, decision_body(**kw))  # type: ignore[arg-type]
    assert d["outcome"] == outcome
    assert CYRILLIC.search(d["message"]), d["message"]


async def test_redirect_message_names_both_models(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    key = await _setup(seed, db, dept_spent=9_998_000)
    d = await decide(client, key, decision_body(model="A"))
    assert d["outcome"] == "redirect"
    assert CYRILLIC.search(d["message"]) and "A" in d["message"] and "C" in d["message"]


@pytest.mark.parametrize("tz", ["UTC", "America/New_York", "Asia/Vladivostok"])
async def test_budget_message_has_msk_reset_month(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, tz: str
) -> None:
    # TZ меняется вручную: monkeypatch.undo() снял бы и подмену socket из egress_guard
    old = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    try:
        key = await _setup(seed, db, dept_spent=9_999_900)  # остаток 1 ₽
        d = await decide(client, key, decision_body(model="A"))
        assert d["outcome"] == "deny" and d["code"] == "budget_exceeded"
        msg = d["message"]
        assert CYRILLIC.search(msg)
        assert "01.11.2026 00:00" in msg and "МСК" in msg, msg
        assert d["applied_limit"]["resets_at"].endswith("+03:00")
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


async def test_budget_message_has_msk_reset_day(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    key = await _setup(
        seed, db, emp_day=50_000
    )  # личный дневной лимит 500 ₽ исчерпан, Z в каталоге не разрешена
    d = await decide(client, key, decision_body(model="A"))
    assert d["outcome"] == "deny"
    assert "15.10.2026 00:00" in d["message"] and "МСК" in d["message"], d["message"]


async def test_observe_message_explains_violation(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    await catalog(seed)
    dep = await seed.department("Маркетинг", policy_mode="observe", limits={"month": 100_000}, allowed=("A",))
    await seed.employee("ivanov", department=dep)
    await spend(db, "department", dep, 100_000)
    _, key = await seed.caller_key()
    d = await decide(client, key, decision_body(model="A"))
    assert d["outcome"] == "allow" and d["observed_violation"] == "budget_exceeded"
    assert "наблюд" in d["message"].lower(), d["message"]


async def test_problem_detail_is_russian(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    key = await _setup(seed, db)
    unauth = await client.post("/api/v1/decisions", json=decision_body())
    assert unauth.status_code == 401 and CYRILLIC.search(unauth.json()["detail"])
    invalid = await post_decision(client, key, decision_body() | {"kind": "image"})
    assert invalid.status_code == 422 and CYRILLIC.search(invalid.json()["detail"])
