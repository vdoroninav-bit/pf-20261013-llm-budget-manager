"""T060, SC-007: остаток в API и в админке совпадает с ручным пересчётом по FR-026 до копейки на эталонном наборе —
токенные события, импорт, подписка в USD, курс, переход месяца.

FR-026: остаток = лимит − (факт токенов + открытые резервы) − импорт «import» за загруженные сутки периода
− амортизация подписок за дни периода по текущий включительно.

Эталонный набор (подразделение «Аналитика»; лимиты: день 10 000 ₽, неделя 30 000 ₽, месяц 100 000 ₽):
- курсы: октябрь 95,50; ноябрь 100,00;
- подписка подразделения с 01.10: 3 места × 30 $ → M(окт) = 90 × 95,50 = 8 595,00 ₽; M(ноя) = 9 000,00 ₽;
  cum(d) = round_half_up(859 500 × d / 31) коп.: cum(11) = 304 984, cum(13) = 360 435, cum(14) = 388 161;
- 14.10 (среда, неделя с 12.10), модель A (10 + 50 ₽ за 1k): отчёт 2 000/1 000 → 70,00 ₽; отчёт 1 000/400 → 30,00 ₽;
  решение без отчёта → резерв 30,00 ₽;
- импорт Anthropic, источник «import»: 12.10 — 1,00 $ → 95,50 ₽; 13.10 — 12,34 $ → 1 178,47 ₽ (1 178,47 = 12,34 × 95,5);
- 14.10 12:20 резерв истёк → списан по оценке 30,00 ₽ датой 14.10 (`no_report`);
- 02.11 (понедельник): импорт 31.10 — 2,00 $ → 191,00 ₽ (поздний, курс октября); 01.11 — 1,00 $ → 100,00 ₽;
  отчёт 1 000/400 → 30,00 ₽; амортизация ноября 9 000 / 30 = 300,00 ₽ в сутки.

Ручной расчёт (коп.):
14.10  день:   1 000 000 − 10 000 −       0 −  27 726 − 3 000 =   959 274 → 9 592,74
       неделя: 3 000 000 − 10 000 − 127 397 −  83 177 − 3 000 = 2 776 426 → 27 764,26
       месяц: 10 000 000 − 10 000 − 127 397 − 388 161 − 3 000 = 9 471 442 → 94 714,42
02.11  день:   1 000 000 −  3 000 −       0 −  30 000          =   967 000 → 9 670,00
       неделя: 3 000 000 −  3 000 −       0 −  30 000          = 2 967 000 → 29 670,00
       месяц: 10 000 000 −  3 000 −  10 000 −  60 000          = 9 927 000 → 99 270,00
31.10 (итог октября): 10 000 000 − 13 000 − 146 497 − 859 500  = 8 981 003 → 89 810,03 (сумма дней = M ровно)

Написаны до реализации: подписки — T051, импорт — T061, баланс подразделения в админке — T071.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from bm.clock import FrozenClock
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.imports import ANTHROPIC_KEY, cost_line, recorded, run_import
from tests.fixtures.qa import (
    admin_client_login,
    balance,
    catalog,
    decide,
    decision_body,
    expire_reserves,
    msk,
    relogin,
    report,
    usage_body,
)
from tests.fixtures.seed import Seed

pytestmark = [pytest.mark.db]

EXPECTED_OCT14 = {
    "day": {
        "token": "100.00",
        "import": "0.00",
        "subs": "277.26",
        "reserved": "30.00",
        "remaining": "9592.74",
    },
    "week": {
        "token": "100.00",
        "import": "1273.97",
        "subs": "831.77",
        "reserved": "30.00",
        "remaining": "27764.26",
    },
    "month": {
        "token": "100.00",
        "import": "1273.97",
        "subs": "3881.61",
        "reserved": "30.00",
        "remaining": "94714.42",
    },
}
EXPECTED_NOV02 = {
    "day": {"token": "30.00", "import": "0.00", "subs": "300.00", "reserved": "0.00", "remaining": "9670.00"},
    "week": {
        "token": "30.00",
        "import": "0.00",
        "subs": "300.00",
        "reserved": "0.00",
        "remaining": "29670.00",
    },
    "month": {
        "token": "30.00",
        "import": "100.00",
        "subs": "600.00",
        "reserved": "0.00",
        "remaining": "99270.00",
    },
}
EXPECTED_OCT_TOTAL = {
    "token": "130.00",
    "import": "1464.97",
    "subs": "8595.00",
    "reserved": "0.00",
    "remaining": "89810.03",
}


@pytest.fixture
def settings(settings: Any) -> Any:
    return settings.model_copy(
        update={"import_enabled": True, "anthropic_admin_key": SecretStr(ANTHROPIC_KEY)}
    )


@dataclass
class Ref:
    dep: UUID
    gateway: str
    service: str
    csrf: dict[str, str]


def dept_rows(b: dict[str, Any]) -> dict[str, dict[str, str]]:
    """{period: {token, import, subs, reserved, remaining}} по строкам подразделения `BalanceReport`."""
    out: dict[str, dict[str, str]] = {}
    for x in b["limits"]:
        if x["owner_type"] == "department":
            s = x["spent"]
            out[x["period"]] = {
                "token": s["token_rub"],
                "import": s["import_rub"],
                "subs": s["subscriptions_rub"],
                "reserved": x["reserved_rub"],
                "remaining": x["remaining_rub"],
            }
    return out


async def build_october(app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection) -> Ref:
    await catalog(seed, only="A")
    dep = await seed.department(
        "Аналитика", limits={"day": 1_000_000, "week": 3_000_000, "month": 10_000_000}, allowed=("A",)
    )
    await seed.employee("ivanov", department=dep)
    _, gateway = await seed.caller_key("gateway")
    _, service = await seed.caller_key("svc", scope_type="service", department=dep)
    csrf = await admin_client_login(client, seed)
    for month, rate in (("2026-10", "95.5000"), ("2026-11", "100.0000")):
        resp = await client.put(f"/api/v1/fx-rates/{month}", json={"usd_rub": rate}, headers=csrf)
        assert resp.status_code == 200, resp.text
    sub = await client.post(
        "/api/v1/subscriptions",
        json={
            "provider": "anthropic",
            "plan": "Team",
            "seat_price": "30",
            "currency": "USD",
            "seats": 3,
            "start_date": "2026-10-01",
            "assignee": {"type": "department", "ref": str(dep)},
        },
        headers=csrf,
    )
    assert sub.status_code == 201, f"createSubscription: {sub.status_code} {sub.text[:200]}"
    assert sub.json()["monthly_total_rub"] == "8595.00"
    src = await client.post(
        "/api/v1/provider-sources",
        json={
            "provider": "anthropic",
            "kind": "workspace",
            "ref": "wrkspc_analytics",
            "department_id": str(dep),
            "mode": "import",
        },
        headers=csrf,
    )
    assert src.status_code == 201, f"createProviderSource: {src.status_code} {src.text[:200]}"

    for tokens in ((2000, 1000), (1000, 400)):
        d = await decide(client, gateway, decision_body(model="A"))
        r = await report(client, gateway, usage_body(d["decision_id"], *tokens))
        assert r["status"] == "charged"
    open_reserve = await decide(client, gateway, decision_body(model="A"))
    assert open_reserve["reserve_rub"] == "30.00"
    lines = [
        cost_line("wrkspc_analytics", date(2026, 10, 12), "1.00"),
        cost_line("wrkspc_analytics", date(2026, 10, 13), "12.34"),
    ]
    await run_import(app, recorded("anthropic", lines), date(2026, 10, 12), date(2026, 10, 13))
    return Ref(dep, gateway, service, csrf)


async def move_to_november(app: Any, client: httpx.AsyncClient, ref: Ref, clock: FrozenClock) -> None:
    clock.advance(minutes=20)
    assert await expire_reserves(app, clock.now()) == 1
    clock.set(msk(2026, 11, 2, 12))
    lines = [
        cost_line("wrkspc_analytics", date(2026, 10, 31), "2.00"),
        cost_line("wrkspc_analytics", date(2026, 11, 1), "1.00"),
    ]
    await run_import(app, recorded("anthropic", lines), date(2026, 10, 31), date(2026, 11, 1))
    d = await decide(client, ref.gateway, decision_body(model="A"))
    await report(client, ref.gateway, usage_body(d["decision_id"], 1000, 400))


async def test_sc007_api_balance_matches_manual_calculation(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    ref = await build_october(app, client, seed, db)
    oct14 = await balance(client, ref.service, login=None)
    assert dept_rows(oct14) == EXPECTED_OCT14
    # остаток сотрудника показывает те же строки подразделения
    assert dept_rows(await balance(client, ref.gateway, login="ivanov")) == EXPECTED_OCT14

    await move_to_november(app, client, ref, clock)
    nov02 = await balance(client, ref.service, login=None)
    assert dept_rows(nov02) == EXPECTED_NOV02
    month = next(x for x in nov02["limits"] if x["period"] == "month")
    assert month["period_start"].startswith("2026-11-01T00:00:00") and month["used_pct"] == "0.7"
    assert month["resets_at"].startswith("2026-12-01T00:00:00")


async def test_sc007_admin_balance_and_items_match_manual_calculation(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, clock: FrozenClock
) -> None:
    ref = await build_october(app, client, seed, db)
    resp = await client.get(f"/api/v1/departments/{ref.dep}/balance")
    assert resp.status_code == 200, resp.text
    assert_matches_contract(resp, "getDepartmentBalance")
    assert dept_rows(resp.json()) == EXPECTED_OCT14
    assert dept_rows(resp.json()) == dept_rows(
        await balance(client, ref.service, login=None)
    )  # API = админка

    # прослеживаемость (FR-026): каждая сумма месяца раскладывается на события, строки импорта и подписки
    items = await client.get(
        f"/api/v1/departments/{ref.dep}/balance/items", params={"period": "month", "page_size": 200}
    )
    assert items.status_code == 200, items.text
    assert_matches_contract(items, "listDepartmentBalanceItems")
    by_source: dict[str, int] = {}
    for it in items.json()["items"]:
        kop = int(Decimal(it["amount_rub"]) * 100)
        by_source[it["source"]] = by_source.get(it["source"], 0) + kop
    assert by_source == {"token": 10_000, "import": 127_397, "subscription": 388_161, "reservation": 3_000}

    await move_to_november(app, client, ref, clock)
    await relogin(client)
    nov = await client.get(f"/api/v1/departments/{ref.dep}/balance")
    assert dept_rows(nov.json()) == EXPECTED_NOV02
    end_of_october = (msk(2026, 11, 1) - timedelta(seconds=1)).isoformat()
    octo = await client.get(f"/api/v1/departments/{ref.dep}/balance", params={"at": end_of_october})
    assert octo.status_code == 200, octo.text
    assert dept_rows(octo.json())["month"] == EXPECTED_OCT_TOTAL
