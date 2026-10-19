"""Помощники тестов импорта расхода провайдеров (T060): строки расхода, записанный источник, запуск конвейера,
подменный HTTP-транспорт для коннекторов (без сети), справочник источников.

Договорённости с backend (T061–T063), на которые опираются тесты; при другом решении правится только этот модуль:
- `bm.provider_import.sources.base.CostLine(provider, source_kind, source_ref, model, day, amount, currency)` —
  как в 12_plan.md §6; `day` — сутки UTC провайдера (ADR-004), `amount` — Decimal в валюте `currency`;
- `bm.provider_import.sources.recorded.RecordedSource(provider, lines, *, error_class=None)` — источник для тестов:
  `fetch(day_from, day_to)` отдаёт строки `lines` с днём в [day_from, day_to]; при `error_class` (значение
  `ImportErrorClass` контракта) загрузка завершается сбоем этого класса;
- `bm.provider_import.pipeline.run(pool, source, day_from, day_to, now)` — 12_plan.md §6 плюс пул соединений
  (без него конвейеру негде взять БД);
- коннекторы: `AnthropicSource(settings, *, transport=None)` и `OpenAISource(settings, *, transport=None)`
  в `bm.provider_import.sources.{anthropic,openai}`; `transport` передаётся в `bm.egress.get_client`.
"""

import json
from collections.abc import Callable, Iterable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import httpx

FIXTURES = Path(__file__).resolve().parent
ANTHROPIC_KEY = "qa-anthropic-admin-key-NOT-REAL-7f3a9c01b2e4"  # тестовое значение, не ключ
OPENAI_KEY = "qa-openai-admin-key-NOT-REAL-5d1e8b2c4a60"  # тестовое значение, не ключ


def cost_line(
    source_ref: str,
    day: date,
    amount: str,
    *,
    model: str = "claude-sonnet-4-5",
    provider: str = "anthropic",
    source_kind: str = "workspace",
    currency: str = "USD",
) -> Any:
    from bm.provider_import.sources.base import CostLine

    return CostLine(
        provider=provider,
        source_kind=source_kind,
        source_ref=source_ref,
        model=model,
        day=day,
        amount=Decimal(amount),
        currency=currency,
    )


def recorded(provider: str, lines: Iterable[Any] = (), *, error_class: str | None = None) -> Any:
    from bm.provider_import.sources.recorded import RecordedSource

    return RecordedSource(provider, list(lines), error_class=error_class)


async def run_import(app: Any, source: Any, day_from: date, day_to: date | None = None) -> Any:
    """Один запуск конвейера импорта на часах приложения (то, что worker делает в IMPORT_HOUR_MSK)."""
    from bm.provider_import import pipeline

    return await pipeline.run(app.state.pool, source, day_from, day_to or day_from, app.state.clock.now())


async def provider_source(
    conn: asyncpg.Connection,
    ref: str,
    department: UUID | None,
    mode: str = "import",
    *,
    provider: str = "anthropic",
    kind: str = "workspace",
) -> UUID:
    """Строка справочника источников прямым INSERT (для тестов конвейера без API админки)."""
    sid: UUID = await conn.fetchval(
        "INSERT INTO provider_source(provider, kind, ref, department_id, mode) VALUES ($1, $2, $3, $4, $5) "
        "RETURNING id",
        provider,
        kind,
        ref,
        department,
        mode,
    )
    return sid


async def import_ledger(conn: asyncpg.Connection, department: UUID, day: date | None = None) -> int:
    """Сумма импорта в суточном реестре подразделения (за день или за всё время), коп."""
    v = await conn.fetchval(
        "SELECT coalesce(sum(amount_kop), 0) FROM ledger_daily WHERE owner_type = 'department' "
        "AND owner_id = $1 AND source = 'import' AND ($2::date IS NULL OR day = $2)",
        department,
        day,
    )
    return int(v)


async def import_lines(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    rows: list[asyncpg.Record] = await conn.fetch(
        "SELECT * FROM import_line ORDER BY provider, source_ref, day"
    )
    return rows


async def last_run(conn: asyncpg.Connection, provider: str = "anthropic") -> asyncpg.Record:
    row = await conn.fetchrow(
        "SELECT * FROM import_run WHERE provider = $1 ORDER BY created_at DESC, started_at DESC NULLS LAST LIMIT 1",
        provider,
    )
    assert row is not None, f"нет записи import_run для {provider}"
    return row


# ---------------------------------------------------------------- подменный транспорт коннекторов


def load_pages(provider: str, *names: str) -> list[dict[str, Any]]:
    return [json.loads((FIXTURES / provider / name).read_text(encoding="utf-8")) for name in names]


class FakeProvider:
    """httpx.MockTransport с записанными страницами ответа: первая страница — без параметра `page`,
    следующая — по значению `next_page` предыдущей. Все запросы сохраняются в `requests`."""

    def __init__(self, pages: list[dict[str, Any]], *, status: int = 200, raw: bytes | None = None) -> None:
        self.pages = pages
        self.status = status
        self.raw = raw
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"type": "error", "error": {"message": "QA: отказ"}})
        if self.raw is not None:
            return httpx.Response(200, content=self.raw, headers={"content-type": "application/json"})
        cursor = request.url.params.get("page")
        if cursor is None:
            return httpx.Response(200, json=self.pages[0])
        for i, page in enumerate(self.pages[:-1]):
            if page.get("next_page") == cursor:
                return httpx.Response(200, json=self.pages[i + 1])
        return httpx.Response(400, json={"error": f"QA: неизвестный курсор {cursor!r}"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def totals(lines: Iterable[Any], key: Callable[[Any], Any]) -> dict[Any, Decimal]:
    out: dict[Any, Decimal] = {}
    for x in lines:
        out[key(x)] = out.get(key(x), Decimal(0)) + Decimal(x.amount)
    return out
