"""Помощники приёмочных тестов QA (T020, T030, T040, T050, T060, T070): вызовы Decision API, засев расхода,
вход в админку, такт worker.

Только тестовый код: прямые INSERT в реестр, сборка запросов по контракту и проверка ответов
`assert_matches_contract`. Бизнес-логики здесь нет.

Каталог для сценариев 1.x подобран под суммы спецификации при оценке 1 000 вход / 400 выход
(как в tests/unit/test_select.py):
  A: 10 + 50 ₽/1k → 30 ₽ (3000 коп.), чат, инструменты, JSON
  B: 5 + 25 → 15 ₽, чат, инструменты, JSON
  C: 1 + 10 → 5 ₽ (500 коп.), чат, инструменты, JSON
  N: 0.5 + 5 → 2.50 ₽, чат, БЕЗ инструментов и JSON
  Z: 0 + 0 → 0 ₽, чат, без инструментов, контекст 8k (своя модель без ставки)
  E: эмбеддинги, 0.1 + 0 → 0.10 ₽
"""

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any
from uuid import UUID

import asyncpg
import httpx

from bm.domain.periods import MSK
from tests.contract.helpers import assert_matches_contract
from tests.fixtures.seed import Seed

TODAY = date(2026, 10, 14)  # совпадает с conftest.START (среда; неделя началась 12.10)
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
ADMIN_PASSWORD = "correct-horse-battery"


# ---------------------------------------------------------------- засев


async def catalog(seed: Seed, *, only: str = "ABCNZE") -> None:
    """Стандартный каталог моделей сценариев 1.x."""
    spec: dict[str, dict[str, Any]] = {
        "A": {"in_per_1k": "10", "out_per_1k": "50"},
        "B": {"in_per_1k": "5", "out_per_1k": "25"},
        "C": {"in_per_1k": "1", "out_per_1k": "10"},
        "N": {"in_per_1k": "0.5", "out_per_1k": "5", "tools": False, "json": False},
        "Z": {"in_per_1k": "0", "out_per_1k": "0", "tools": False, "json": False, "max_context": 8000},
        "E": {"in_per_1k": "0.1", "out_per_1k": "0", "kind": "embedding", "tools": False, "json": False},
    }
    for mid in only:
        await seed.model(mid, **spec[mid])


async def spend(
    conn: asyncpg.Connection,
    owner_type: str,
    owner_id: UUID,
    kop: int,
    *,
    day: date = TODAY,
    source: str = "token",
) -> None:
    """Уже учтённый расход владельца за сутки (строка суточного реестра ledger_daily, ADR-003)."""
    await conn.execute(
        "INSERT INTO ledger_daily(owner_type, owner_id, day, source, amount_kop) VALUES ($1, $2, $3, $4, $5) "
        "ON CONFLICT (owner_type, owner_id, day, source) "
        "DO UPDATE SET amount_kop = ledger_daily.amount_kop + EXCLUDED.amount_kop",
        owner_type,
        owner_id,
        day,
        source,
        kop,
    )


async def bump_config(conn: asyncpg.Connection) -> None:
    """Изменение справочника прямым SQL после первого решения: снимок обязан перечитаться (T018)."""
    await conn.execute("UPDATE config_version SET version = version + 1")


async def reserved(conn: asyncpg.Connection, owner_type: str, owner_id: UUID) -> int:
    v: int = await conn.fetchval(
        "SELECT reserved_kop FROM budget_lock WHERE owner_type = $1 AND owner_id = $2", owner_type, owner_id
    )
    return v


async def ledger_total(conn: asyncpg.Connection, owner_type: str, owner_id: UUID) -> int:
    v: int = await conn.fetchval(
        "SELECT coalesce(sum(amount_kop), 0) FROM ledger_daily WHERE owner_type = $1 AND owner_id = $2",
        owner_type,
        owner_id,
    )
    return int(v)


async def call_record(conn: asyncpg.Connection, decision_id: str) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM call_record WHERE id = $1", UUID(decision_id))
    assert row is not None, f"нет call_record для решения {decision_id}"
    return row


# ---------------------------------------------------------------- Decision API


def key_headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key}


def decision_body(
    login: str | None = "ivanov",
    model: str = "A",
    kind: str = "chat",
    *,
    input_tokens: int | None = 1000,
    max_output_tokens: int | None = 400,
    tools: bool = False,
    json_mode: bool = False,
    context_tokens: int = 0,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "subject": {"type": "employee", "login": login} if login is not None else {"type": "service"},
        "model": model,
        "kind": kind,
    }
    if input_tokens is not None:
        body["input_tokens"] = input_tokens
    if max_output_tokens is not None:
        body["max_output_tokens"] = max_output_tokens
    if tools or json_mode or context_tokens:
        body["needs"] = {"tools": tools, "json_mode": json_mode, "context_tokens": context_tokens}
    return body


async def post_decision(client: httpx.AsyncClient, api_key: str, body: dict[str, Any]) -> httpx.Response:
    return await client.post("/api/v1/decisions", json=body, headers=key_headers(api_key))


async def decide(
    client: httpx.AsyncClient, api_key: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """POST /decisions: ожидается 200 по контракту. Возвращает тело `Decision`."""
    resp = await post_decision(client, api_key, body or decision_body())
    assert resp.status_code == 200, f"createDecision: {resp.status_code} {resp.text[:300]}"
    assert_matches_contract(resp, "createDecision")
    data: dict[str, Any] = resp.json()
    return data


def usage_body(
    decision_id: str | None,
    tokens_in: int = 1000,
    tokens_out: int = 400,
    *,
    cached: int | None = None,
    call_status: str = "ok",
    report_id: str | None = None,
    login: str | None = None,
    model: str | None = None,
    actual_model: str | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    tokens: dict[str, int] = {"input": tokens_in, "output": tokens_out}
    if cached is not None:
        tokens["cached"] = cached
    body: dict[str, Any] = {
        "report_id": report_id or str(uuid.uuid4()),
        "tokens": tokens,
        "call_status": call_status,
    }
    if decision_id is not None:
        body["decision_id"] = decision_id
    if login is not None:
        body["subject"] = {"type": "employee", "login": login}
    if model is not None:
        body["model"] = model
    if actual_model is not None:
        body["actual_model"] = actual_model
    if occurred_at is not None:
        body["occurred_at"] = occurred_at.isoformat()
    return body


async def report(client: httpx.AsyncClient, api_key: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST /usage: ожидается 200 по контракту. Возвращает тело `UsageResult`."""
    resp = await client.post("/api/v1/usage", json=body, headers=key_headers(api_key))
    assert resp.status_code == 200, f"reportUsage: {resp.status_code} {resp.text[:300]}"
    assert_matches_contract(resp, "reportUsage")
    data: dict[str, Any] = resp.json()
    return data


async def balance(client: httpx.AsyncClient, api_key: str, login: str | None = "ivanov") -> dict[str, Any]:
    params = {"login": login} if login is not None else {}
    resp = await client.get("/api/v1/balance", params=params, headers=key_headers(api_key))
    assert resp.status_code == 200, f"getCallerBalance: {resp.status_code} {resp.text[:300]}"
    assert_matches_contract(resp, "getCallerBalance")
    data: dict[str, Any] = resp.json()
    return data


async def expire_reserves(app: Any, now: datetime) -> int:
    """Истечение резервов (FR-010) — то, что worker делает раз в 60 с.

    Договорённость с backend (T031, 12_plan.md §6): экземпляр `Budget` приложения доступен как
    `app.state.budget`, метод `expire_reserves(now) -> int` возвращает число закрытых резервов.
    """
    from bm.budget.service import Budget

    budget = getattr(app.state, "budget", None)
    assert isinstance(budget, Budget), "ожидается app.state.budget: bm.budget.service.Budget (T031)"
    n: int = await budget.expire_reserves(now)
    return n


def limit_of(report_body: dict[str, Any], owner_type: str, period: str) -> dict[str, Any]:
    """Строка `LimitBalance` из `BalanceReport` по владельцу и периоду."""
    found = [x for x in report_body["limits"] if x["owner_type"] == owner_type and x["period"] == period]
    assert len(found) == 1, f"ожидалась одна строка {owner_type}/{period}: {report_body['limits']}"
    return found[0]


def msk(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=MSK)


def same_instant(iso: str, expected: datetime) -> bool:
    return datetime.fromisoformat(iso) == expected


# ---------------------------------------------------------------- админка


async def admin_client_login(
    client: httpx.AsyncClient,
    seed: Seed,
    login: str = "root",
    *,
    role: str = "admin",
    department: UUID | None = None,
) -> dict[str, str]:
    """Создаёт пользователя админки, входит и возвращает заголовок CSRF для изменяющих запросов."""
    await seed.admin_user(login, ADMIN_PASSWORD, role=role, department=department)
    resp = await client.post("/api/v1/auth/login", json={"login": login, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"X-CSRF-Token": str(resp.json()["csrf_token"])}


async def relogin(client: httpx.AsyncClient, login: str = "root") -> dict[str, str]:
    """Повторный вход существующего пользователя (сессия живёт 12 ч — после сдвига часов она истекает)."""
    resp = await client.post("/api/v1/auth/login", json={"login": login, "password": ADMIN_PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"X-CSRF-Token": str(resp.json()["csrf_token"])}


@asynccontextmanager
async def session_as(
    client: httpx.AsyncClient,
    seed: Seed,
    login: str,
    *,
    role: str,
    department: UUID | None = None,
) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str]]]:
    """Второй клиент админки на том же приложении (своя cookie-сессия): (клиент, заголовок CSRF)."""
    async with httpx.AsyncClient(transport=client._transport, base_url=str(client.base_url)) as other:
        csrf = await admin_client_login(other, seed, login, role=role, department=department)
        yield other, csrf


# ---------------------------------------------------------------- worker


async def worker_tick(app: Any) -> list[str]:
    """Один такт worker на часах приложения: выполняются все задачи `default_jobs` (новый планировщик —
    у всех задач наступил срок; задачи «по расписанию» сами сверяются с часами).

    Договорённость с backend (T051, T061, T071): задачи подписок, импорта и порогов входят в
    `bm.worker.jobs.default_jobs`. Если её сигнатура изменится, поправить нужно только этот помощник.
    """
    from bm.worker.jobs import Scheduler, default_jobs

    jobs = default_jobs(app.state.budget, app.state.settings)  # с настройками — и задача импорта (T061)
    ran: list[str] = await Scheduler(app.state.pool, jobs, app.state.clock).tick()
    return ran


async def notifications(client: httpx.AsyncClient, kind: str | None = None) -> list[dict[str, Any]]:
    """Лента уведомлений текущей сессии (`listNotifications`), при необходимости — только вида `kind`."""
    resp = await client.get("/api/v1/notifications", params={"page_size": 200})
    assert resp.status_code == 200, f"listNotifications: {resp.status_code} {resp.text[:300]}"
    assert_matches_contract(resp, "listNotifications")
    items: list[dict[str, Any]] = resp.json()["items"]
    return [n for n in items if kind is None or n["kind"] == kind]


def one(items: list[Any]) -> Any:
    """Единственный элемент списка; иначе AssertionError (а не ValueError распаковки — для xfail(raises=…))."""
    assert len(items) == 1, f"ожидался ровно один элемент, получено {len(items)}: {items!r}"[:500]
    return items[0]
