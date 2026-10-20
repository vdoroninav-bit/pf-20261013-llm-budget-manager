"""T050: приёмочные тесты сценария 4 — подписки с фиксированной платой (10_tech_spec.md, 4.1–4.6, FR-017–FR-020).

Через API админки (`createSubscription`, `previewSubscription`, `endSubscription`, `deleteSubscription`,
`putFxRate`) и Decision API (`getCallerBalance`, `createDecision`); ответы проверяются по контракту.

Правила, на которые опираются ожидаемые суммы:
- накопительная амортизация (ADR-003, APPROVAL вопрос 3А): cum(d) = round_half_up(M × активные дни 1..d / дней
  в месяце), доля окна = cum(конец) − cum(начало − 1); пример 4.1 даёт 241,93 ₽ за день;
- M месяца фиксируется в `subscription_month` при первом расчёте вместе с курсом: курс, изменённый задним
  числом, M не меняет (12_plan.md §10, пограничные случаи спецификации);
- USD: курс месяца → иначе последний введённый с `fx_warning=last_known_rate_used` → иначе 422 `fx_rate_missing`;
- доля подписок для предупреждения (FR-020) — амортизация за **весь** период против лимита периода
  (предупреждение «при наступлении нового периода» иначе невозможно: в первый день доля ничтожна).

Написаны до реализации (T051). Отчёт «не распределено» (4.6) — tests/acceptance/test_us6_reports_roles.py (T071).
"""

from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest

from bm.clock import FrozenClock
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.qa import (
    admin_client_login,
    balance,
    catalog,
    decide,
    decision_body,
    limit_of,
    msk,
    notifications,
    relogin,
    session_as,
    worker_tick,
)
from tests.fixtures.seed import Seed

pytestmark = [pytest.mark.db]


def sub_body(
    assignee_type: str,
    ref: str | UUID | None,
    *,
    seats: int = 3,
    seat_price: str = "2500",
    currency: str = "RUB",
    start: str = "2026-10-01",
    end: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "provider": "anthropic",
        "plan": "Team",
        "seat_price": seat_price,
        "currency": currency,
        "seats": seats,
        "start_date": start,
        "assignee": {"type": assignee_type, "ref": None if ref is None else str(ref)},
    }
    if end is not None:
        body["end_date"] = end
    return body


async def create_sub(client: httpx.AsyncClient, csrf: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post("/api/v1/subscriptions", json=body, headers=csrf)
    assert resp.status_code == 201, f"createSubscription: {resp.status_code} {resp.text[:300]}"
    assert_matches_contract(resp, "createSubscription")
    data: dict[str, Any] = resp.json()
    return data


async def preview(client: httpx.AsyncClient, csrf: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post("/api/v1/subscriptions/preview", json=body, headers=csrf)
    assert resp.status_code == 200, f"previewSubscription: {resp.status_code} {resp.text[:300]}"
    assert_matches_contract(resp, "previewSubscription")
    data: dict[str, Any] = resp.json()
    return data


def period_of(pv: dict[str, Any], period: str) -> dict[str, Any]:
    found = [p for p in pv["periods"] if p["period"] == period]
    assert len(found) == 1, f"в предпросмотре нет периода {period} (ожидалась одна строка): {pv['periods']}"
    return found[0]


async def service_key(seed: Seed, dep: UUID) -> str:
    """Ключ служебного субъекта подразделения: `getCallerBalance` без login отдаёт лимиты подразделения."""
    _, key = await seed.caller_key("svc", scope_type="service", department=dep)
    return key


def subs(report: dict[str, Any], owner_type: str, period: str) -> str:
    return str(limit_of(report, owner_type, period)["spent"]["subscriptions_rub"])


# ---------------------------------------------------------------- 4.1 амортизация по дням


async def test_4_1_department_amortization_by_days(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    """3 места × 2 500 ₽, с 1-го, октябрь — 31 день; 10-е число (суббота, неделя с 05.10)."""
    dep = await seed.department("Разработка", limits={"day": 100_000, "week": 500_000, "month": 3_000_000})
    key = await service_key(seed, dep)
    clock.set(msk(2026, 10, 1, 10))
    csrf = await admin_client_login(client, seed)
    sub = await create_sub(client, csrf, sub_body("department", dep))
    assert sub["monthly_total_rub"] == "7500.00" and sub["fx_warning"] is None
    assert sub["assignee"] == {"type": "department", "ref": str(dep)}

    clock.set(msk(2026, 10, 10, 12))
    b = await balance(client, key, login=None)
    assert subs(b, "department", "month") == "2419.35"  # 7 500 × 10 / 31
    assert subs(b, "department", "day") == "241.93"  # cum(10) − cum(9), накопительное округление
    assert subs(b, "department", "week") == "1451.61"  # cum(10) − cum(4): 05.10–10.10
    month = limit_of(b, "department", "month")
    assert month["remaining_rub"] == "27580.65" and month["spent"]["token_rub"] == "0.00"
    m = await db.fetchrow(
        "SELECT m_total_kop FROM subscription_month WHERE subscription_id = $1", UUID(sub["id"])
    )
    assert m is not None and m["m_total_kop"] == 750_000  # M октября зафиксирована


async def test_4_1_amortization_applies_to_decisions(
    client: httpx.AsyncClient, seed: Seed, clock: FrozenClock
) -> None:
    """Дневной лимит 250 ₽ − 241,93 ₽ амортизации = 8,07 ₽: A (30 ₽) не помещается → перенаправление на C (5 ₽)."""
    await catalog(seed)
    dep = await seed.department("Разработка", limits={"day": 25_000}, allowed=("A", "C"))
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key()
    clock.set(msk(2026, 10, 10, 12))
    csrf = await admin_client_login(client, seed)
    await create_sub(client, csrf, sub_body("department", dep))
    d = await decide(client, key, decision_body(model="A"))
    assert (d["outcome"], d["model"], d["reserve_rub"]) == ("redirect", "C", "5.00")
    assert d["applied_limit"]["period"] == "day" and d["applied_limit"]["remaining_rub"] == "8.07"
    assert d["budget_blocked_by_subscriptions"] is False


# ---------------------------------------------------------------- 4.2 подписка сотрудника


async def test_4_2_employee_subscription_charges_employee_and_department(
    client: httpx.AsyncClient, seed: Seed
) -> None:
    """1 место × 3 100 ₽ → 100 ₽ в сутки; 14.10 (среда, неделя с 12.10)."""
    dep = await seed.department("Разработка", limits={"day": 100_000, "month": 3_000_000})
    emp = await seed.employee("ivanov", department=dep, limits={"day": 50_000, "week": 200_000})
    other = await seed.employee("petrov", department=dep)
    _, key = await seed.caller_key()
    csrf = await admin_client_login(client, seed)
    sub = await create_sub(client, csrf, sub_body("employee", emp.pseudonym, seats=1, seat_price="3100"))
    assert sub["assignee"] == {"type": "employee", "ref": emp.pseudonym}

    b = await balance(client, key, login="ivanov")
    assert subs(b, "employee", "day") == "100.00"
    assert subs(b, "employee", "week") == "300.00"  # 12.10–14.10
    assert subs(b, "department", "day") == "100.00"  # и из бюджета подразделения
    assert subs(b, "department", "month") == "1400.00"  # 3 100 × 14 / 31
    assert limit_of(b, "employee", "day")["remaining_rub"] == "400.00"
    # подписка другого сотрудника на него не влияет, подразделение видит ту же сумму
    b2 = await balance(client, key, login=other.login)
    assert subs(b2, "department", "day") == "100.00"
    assert all(x["owner_type"] == "department" for x in b2["limits"])


# ---------------------------------------------------------------- 4.3 подписка в USD


async def test_4_3_usd_uses_rate_of_month(client: httpx.AsyncClient, seed: Seed) -> None:
    dep = await seed.department("Разработка", limits={"month": 3_000_000})
    key = await service_key(seed, dep)
    csrf = await admin_client_login(client, seed)
    resp = await client.put("/api/v1/fx-rates/2026-10", json={"usd_rub": "95.5000"}, headers=csrf)
    assert resp.status_code == 200, resp.text
    await client.put("/api/v1/fx-rates/2026-09", json={"usd_rub": "90.0000"}, headers=csrf)
    sub = await create_sub(
        client, csrf, sub_body("department", dep, seats=2, seat_price="25", currency="USD")
    )
    assert sub["currency"] == "USD" and sub["seat_price"] in ("25", "25.00", "25.000000")
    assert sub["monthly_total_rub"] == "4775.00" and sub["fx_warning"] is None  # 2 × 25 $ × 95,50
    b = await balance(client, key, login=None)
    assert subs(b, "department", "month") == "2156.45"  # 477 500 коп. × 14 / 31


async def test_4_3_usd_without_rate_of_month_uses_last_known_with_warning(
    client: httpx.AsyncClient, seed: Seed
) -> None:
    dep = await seed.department("Разработка", limits={"month": 3_000_000})
    key = await service_key(seed, dep)
    csrf = await admin_client_login(client, seed)
    await client.put("/api/v1/fx-rates/2026-08", json={"usd_rub": "85.0000"}, headers=csrf)
    await client.put("/api/v1/fx-rates/2026-09", json={"usd_rub": "90.0000"}, headers=csrf)
    body = sub_body("department", dep, seats=2, seat_price="25", currency="USD")
    pv = await preview(client, csrf, body)
    assert pv["fx_warning"] == "last_known_rate_used"
    sub = await create_sub(client, csrf, body)
    assert sub["monthly_total_rub"] == "4500.00"  # последний введённый курс — сентябрь, 90,00
    assert sub["fx_warning"] == "last_known_rate_used"  # предупреждение в админке
    got = await client.get(f"/api/v1/subscriptions/{sub['id']}")
    assert got.status_code == 200 and got.json()["fx_warning"] == "last_known_rate_used"
    assert_matches_contract(got, "getSubscription")
    b = await balance(client, key, login=None)
    assert subs(b, "department", "month") == "2032.26"  # 450 000 × 14 / 31 = 203 225,8 → 203 226


async def test_4_3_usd_without_any_rate_is_rejected(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    """Курс не вводился никогда: подписка не списывается, администратор видит ошибку «нет курса»."""
    dep = await seed.department("Разработка", limits={"month": 3_000_000})
    csrf = await admin_client_login(client, seed)
    body = sub_body("department", dep, seats=2, seat_price="25", currency="USD")
    pv = await preview(client, csrf, body)
    assert pv["fx_warning"] == "no_rate"
    resp = await client.post("/api/v1/subscriptions", json=body, headers=csrf)
    assert resp.status_code == 422 and resp.json()["code"] == "fx_rate_missing"
    assert_matches_contract(resp, "createSubscription")
    assert "курс" in resp.json()["detail"].lower()
    assert await db.fetchval("SELECT count(*) FROM subscription") == 0


async def test_4_3_retroactive_fx_change_keeps_month_total(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    dep = await seed.department("Разработка", limits={"month": 3_000_000})
    key = await service_key(seed, dep)
    csrf = await admin_client_login(client, seed)
    await client.put("/api/v1/fx-rates/2026-10", json={"usd_rub": "95.5000"}, headers=csrf)
    sub = await create_sub(
        client, csrf, sub_body("department", dep, seats=2, seat_price="25", currency="USD")
    )
    before = await balance(client, key, login=None)
    assert subs(before, "department", "month") == "2156.45"

    resp = await client.put("/api/v1/fx-rates/2026-10", json={"usd_rub": "120.0000"}, headers=csrf)
    assert resp.status_code == 200
    after = await balance(client, key, login=None)
    assert subs(after, "department", "month") == "2156.45"  # уже амортизированное не пересчитано
    m = await db.fetchrow(
        "SELECT m_total_kop, fx FROM subscription_month WHERE subscription_id = $1 AND month = '2026-10-01'",
        UUID(sub["id"]),
    )
    assert m is not None and (m["m_total_kop"], str(m["fx"])) == (477_500, "95.5000")


# ---------------------------------------------------------------- 4.4 доля подписок


async def test_4_4_preview_shows_share_before_saving(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    """2 × 3 000 ₽ = 6 000 ₽ в месяц; дневной лимит 300 ₽: за 14.10 — 193,55 ₽ = 64,5% ≥ 50%."""
    dep = await seed.department("Разработка", limits={"day": 30_000})
    csrf = await admin_client_login(client, seed)
    body = sub_body("department", dep, seats=2, seat_price="3000")
    pv = await preview(client, csrf, body)
    assert pv["department_id"] == str(dep) and pv["warn_threshold_pct"] == 50
    day = period_of(pv, "day")
    assert (day["limit_rub"], day["amortization_rub"], day["share_pct"]) == ("300.00", "193.55", "64.5")
    assert pv["warn"] is True and pv["fx_warning"] is None
    # предпросмотр ничего не сохраняет и не уведомляет
    assert await db.fetchval("SELECT count(*) FROM subscription") == 0
    assert await db.fetchval("SELECT count(*) FROM subscription_month") == 0
    assert await notifications(client, "subs_share") == []

    await db.execute("UPDATE limit_def SET amount_kop = 100000 WHERE owner_id = $1", dep)  # 1 000 ₽ → 19,4%
    await db.execute("UPDATE config_version SET version = version + 1")
    pv2 = await preview(client, csrf, body)
    assert period_of(pv2, "day")["share_pct"] == "19.4" and pv2["warn"] is False


async def test_4_4_preview_month_counts_whole_month_with_existing_subscriptions(
    client: httpx.AsyncClient, seed: Seed
) -> None:
    """Доля за месяц — амортизация всех подписок подразделения с новой за весь месяц против лимита месяца."""
    dep = await seed.department("Разработка", limits={"month": 1_000_000}, subs_warn_pct=70)
    csrf = await admin_client_login(client, seed)
    await create_sub(client, csrf, sub_body("department", dep, seats=1, seat_price="1000"))
    pv = await preview(client, csrf, sub_body("department", dep, seats=2, seat_price="3000"))
    month = period_of(pv, "month")
    assert (month["limit_rub"], month["amortization_rub"], month["share_pct"]) == (
        "10000.00",
        "7000.00",
        "70.0",
    )
    assert pv["warn_threshold_pct"] == 70 and pv["warn"] is True  # порог подразделения, «≥»
    for p in pv["periods"]:  # лимита недели и дня нет: доли нет (период можно и не показывать)
        if p["period"] != "month":
            assert p.get("limit_rub") is None and p.get("share_pct") is None, p


async def test_4_4_subs_share_notified_once_to_admin_and_owner(client: httpx.AsyncClient, seed: Seed) -> None:
    dep = await seed.department("Разработка", limits={"day": 30_000})
    csrf = await admin_client_login(client, seed)
    await create_sub(client, csrf, sub_body("department", dep, seats=2, seat_price="3000"))  # 64,5% дня
    first = await notifications(client, "subs_share")
    assert len(first) == 1
    n = first[0]
    assert (n["department_id"], n["period"], n["period_start"]) == (str(dep), "day", "2026-10-14")
    assert "подписк" in n["message"].lower() and "%" in n["message"]

    await create_sub(client, csrf, sub_body("department", dep, seats=1, seat_price="500"))  # тот же период
    assert len(await notifications(client, "subs_share")) == 1  # одно уведомление на период

    async with session_as(client, seed, "owner", role="budget_owner", department=dep) as (owner, _):
        assert [x["id"] for x in await notifications(owner, "subs_share")] == [n["id"]]
    other = await seed.department("Маркетинг")
    async with session_as(client, seed, "owner2", role="budget_owner", department=other) as (owner2, _):
        assert await notifications(owner2, "subs_share") == []


async def test_4_4_new_month_job_materializes_month_and_warns(
    client: httpx.AsyncClient, app: Any, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    """Задача worker 1-го числа 00:05 МСК: M ноября зафиксирована, доля подписок проверена заново."""
    dep = await seed.department("Разработка", limits={"month": 1_000_000})  # 10 000 ₽, M = 6 000 ₽ → 60%
    csrf = await admin_client_login(client, seed)
    sub = await create_sub(client, csrf, sub_body("department", dep, seats=2, seat_price="3000"))
    october = await notifications(client, "subs_share")
    assert [(x["period"], x["period_start"]) for x in october] == [("month", "2026-10-01")]

    clock.set(msk(2026, 11, 1, 0, 5))
    await worker_tick(app)
    m = await db.fetchrow(
        "SELECT m_total_kop FROM subscription_month WHERE subscription_id = $1 AND month = '2026-11-01'",
        UUID(sub["id"]),
    )
    assert m is not None and m["m_total_kop"] == 600_000
    await relogin(client)
    starts = sorted(x["period_start"] for x in await notifications(client, "subs_share"))
    assert starts == ["2026-10-01", "2026-11-01"]
    await worker_tick(app)  # повторный такт — без дублей
    assert len(await notifications(client, "subs_share")) == 2


# ---------------------------------------------------------------- 4.5 закрытие и удаление


async def test_4_5_end_keeps_past_days_and_stops_future(
    client: httpx.AsyncClient, seed: Seed, clock: FrozenClock
) -> None:
    """3 100 ₽ в месяц (100 ₽/сутки) закрыта 14.10 включительно."""
    dep = await seed.department("Разработка", limits={"day": 100_000, "month": 3_000_000})
    key = await service_key(seed, dep)
    csrf = await admin_client_login(client, seed)
    sub = await create_sub(client, csrf, sub_body("department", dep, seats=1, seat_price="3100"))
    resp = await client.patch(
        f"/api/v1/subscriptions/{sub['id']}", json={"end_date": "2026-10-14"}, headers=csrf
    )
    assert resp.status_code == 200, resp.text
    assert_matches_contract(resp, "endSubscription")
    assert resp.json()["end_date"] == "2026-10-14"
    b = await balance(client, key, login=None)
    assert subs(b, "department", "month") == "1400.00" and subs(b, "department", "day") == "100.00"

    clock.set(msk(2026, 10, 20, 12))
    later = await balance(client, key, login=None)
    assert subs(later, "department", "month") == "1400.00"  # прошедшие дни сохранены
    assert subs(later, "department", "day") == "0.00"  # будущих списаний нет


async def test_4_5_delete_closes_with_today(
    client: httpx.AsyncClient, seed: Seed, clock: FrozenClock
) -> None:
    dep = await seed.department("Разработка", limits={"month": 3_000_000})
    key = await service_key(seed, dep)
    csrf = await admin_client_login(client, seed)
    sub = await create_sub(client, csrf, sub_body("department", dep, seats=1, seat_price="3100"))
    resp = await client.delete(f"/api/v1/subscriptions/{sub['id']}", headers=csrf)
    assert resp.status_code == 204
    assert_matches_contract(resp, "deleteSubscription")
    got = await client.get(f"/api/v1/subscriptions/{sub['id']}")
    assert got.status_code == 200 and got.json()["end_date"] == "2026-10-14"  # сегодня по МСК
    clock.set(msk(2026, 10, 31, 12))
    assert subs(await balance(client, key, login=None), "department", "month") == "1400.00"


async def test_4_5_money_fields_and_assignee_are_immutable(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection
) -> None:
    """Изменить подписку = закрыть и создать новую (ADR-003): PATCH принимает только end_date."""
    dep = await seed.department("Разработка")
    csrf = await admin_client_login(client, seed)
    sub = await create_sub(client, csrf, sub_body("department", dep, seats=1, seat_price="3100"))
    for patch in ({"seats": 5}, {"seat_price": "1"}, {"end_date": "2026-10-31", "seats": 5}):
        resp = await client.patch(f"/api/v1/subscriptions/{sub['id']}", json=patch, headers=csrf)
        assert resp.status_code == 422, f"{patch}: {resp.status_code}"
        assert_matches_contract(resp, "endSubscription")
    row = await db.fetchrow(
        "SELECT seats, seat_price, end_date FROM subscription WHERE id = $1", UUID(sub["id"])
    )
    assert row is not None and (row["seats"], str(row["seat_price"]), row["end_date"]) == (
        1,
        "3100.000000",
        None,
    )


# ---------------------------------------------------------------- 4.6 не распределено


async def test_4_6_unallocated_subscription_is_not_charged(client: httpx.AsyncClient, seed: Seed) -> None:
    dep = await seed.department("Разработка", limits={"day": 100_000, "month": 3_000_000})
    key = await service_key(seed, dep)
    csrf = await admin_client_login(client, seed)
    sub = await create_sub(client, csrf, sub_body("unallocated", None, seats=10, seat_price="3100"))
    assert sub["assignee"] == {"type": "unallocated", "ref": None}
    b = await balance(client, key, login=None)
    assert {x["spent"]["subscriptions_rub"] for x in b["limits"]} == {"0.00"}
    listing = await client.get("/api/v1/subscriptions")
    assert listing.status_code == 200 and listing.json()["total"] == 1
    assert_matches_contract(listing, "listSubscriptions")
