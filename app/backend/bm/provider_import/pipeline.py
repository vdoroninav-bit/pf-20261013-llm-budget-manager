"""Конвейер импорта расхода провайдеров (FR-021–FR-025, ADR-004, T061).

`run(pool, source, day_from, day_to, now)` — одна загрузка окна:
1. запись `import_run` (running);
2. `source.fetch` — вне транзакции (сеть). Сбой → `import_run.failed` с классом ошибки, данные не меняются;
   после 2 неудач подряд — уведомление `import_failure` (не чаще раза в сутки, 14_tech_rulings.md Б5);
3. одна транзакция под advisory-блокировкой провайдера (параллельные загрузки — по очереди): upsert строки
   по ключу (provider, source_ref, model, day); статус — по текущему справочнику: `import` → charged,
   `decision_api` → reconcile, нет в справочнике → unallocated; в `ledger_daily` подразделения уходит дельта
   «новое − старое» списанной суммы. Поэтому повтор без изменений ничего не добавляет, уточнение заменяет,
   смена `mode` или подразделения источника пересчитывается при следующей загрузке окна;
4. несопоставленные источники → уведомление `import_unallocated` (не чаще раза в сутки).

₽ = сумма × курс USD месяца даты расхода (нет курса месяца — последний введённый до него), одно округление
ROUND_HALF_UP. Курса нет совсем — строка сохраняется без суммы в ₽ и в реестр не идёт: следующая загрузка окна
после ввода курса её спишет.

Расписание (`scheduled`): при IMPORT_ENABLED=false — ничего, ни одного соединения. Иначе — ручные запуски из
очереди, плановая загрузка с IMPORT_HOUR_MSK и повтор раз в час до IMPORT_RETRY_UNTIL_HOUR_MSK.
"""

import importlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import asyncpg

from bm.budget import repo
from bm.domain.money import round_half_up
from bm.domain.periods import MSK, msk_date
from bm.domain.types import Kop
from bm.notify import service as notify
from bm.provider_import.sources.base import PROVIDERS, CostLine, CostSource, ImportFailed
from bm.settings import Settings

log = logging.getLogger("bm.import")

PROVIDER_RU = {"anthropic": "Anthropic", "openai": "OpenAI"}
FAILURES_TO_NOTIFY = 2  # FR-025: уведомление после 2 неудач подряд
_LOCK_BASE = 0x626D_6970  # «bmip» + номер провайдера — advisory-блокировка загрузки провайдера

# Коннекторы (T062, T063): модуль и класс; конструктор — (settings).
CONNECTORS: dict[str, tuple[str, str]] = {
    "anthropic": ("bm.provider_import.sources.anthropic", "AnthropicSource"),
    "openai": ("bm.provider_import.sources.openai", "OpenAISource"),
}


@dataclass(frozen=True, slots=True)
class RunResult:
    id: UUID
    provider: str
    status: str  # ok | failed
    lines_charged: int = 0
    lines_reconcile: int = 0
    lines_unallocated: int = 0
    lines_changed: int = 0
    error_class: str | None = None


@dataclass(slots=True)
class _Counts:
    charged: int = 0
    reconcile: int = 0
    unallocated: int = 0
    changed: int = 0


# ---------------------------------------------------------------- одна загрузка


async def run(
    pool: asyncpg.Pool,
    source: CostSource,
    day_from: date,
    day_to: date,
    now: datetime,
    *,
    run_id: UUID | None = None,
) -> RunResult:
    """Загрузка окна [day_from, day_to] (сутки UTC). `run_id` — запуск из очереди (`runImport`)."""
    provider = source.provider
    async with pool.acquire() as conn:
        if run_id is None:
            run_id = await _new_run(conn, provider, now, day_from, day_to)
        else:
            await conn.execute(
                "UPDATE import_run SET status = 'running', started_at = $2 WHERE id = $1", run_id, now
            )
    try:
        lines = _checked(provider, await source.fetch(day_from, day_to))
    except ImportFailed as exc:
        return await _failed(pool, run_id, provider, exc.error_class, now)
    except Exception as exc:  # коннектор упал не по классу провайдера — подробности только в журнале
        log.error("import_internal_error", extra={"provider": provider, "error_class": type(exc).__name__})
        return await _failed(pool, run_id, provider, "internal", now)

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_BASE + PROVIDERS.index(provider))
        counts, unmapped = await _apply(conn, provider, lines, now)
        await conn.execute(
            "UPDATE import_run SET status = 'ok', finished_at = $2, lines_charged = $3, "
            "lines_reconcile = $4, lines_unallocated = $5, lines_changed = $6, error_class = NULL "
            "WHERE id = $1",
            run_id,
            now,
            counts.charged,
            counts.reconcile,
            counts.unallocated,
            counts.changed,
        )
        if unmapped:
            await notify.create_once(
                conn,
                "import_unallocated",
                None,
                None,
                msk_date(now),
                f"Импорт {PROVIDER_RU[provider]}: источники без сопоставления — "
                f"{', '.join(sorted(unmapped))}. "
                "Расход отнесён в «не распределено» и не списан ни из одного бюджета: добавьте источники "
                "в справочник.",
            )
    log.info(
        "import_ok",
        extra={"provider": provider, "lines_charged": counts.charged, "lines_changed": counts.changed},
    )
    return RunResult(
        run_id, provider, "ok", counts.charged, counts.reconcile, counts.unallocated, counts.changed
    )


async def _new_run(
    conn: asyncpg.Connection, provider: str, now: datetime, day_from: date, day_to: date
) -> UUID:
    run_id: UUID = await conn.fetchval(
        "INSERT INTO import_run(provider, status, started_at, day_from, day_to) "
        "VALUES ($1, 'running', $2, $3, $4) RETURNING id",
        provider,
        now,
        day_from,
        day_to,
    )
    return run_id


def _checked(provider: str, lines: Sequence[CostLine]) -> list[CostLine]:
    """Строки коннектора проверяются до записи: чужой провайдер, валюта или тип суммы — `bad_response`."""
    for x in lines:
        if x.provider != provider or x.currency not in ("RUB", "USD") or not isinstance(x.amount, Decimal):
            raise ImportFailed("bad_response", "строка расхода не разобрана")
        if not x.source_ref or not x.model:
            raise ImportFailed("bad_response", "строка расхода без источника или модели")
    return list(lines)


async def _failed(
    pool: asyncpg.Pool, run_id: UUID, provider: str, error_class: str, now: datetime
) -> RunResult:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE import_run SET status = 'failed', finished_at = $2, error_class = $3 WHERE id = $1",
            run_id,
            now,
            error_class,
        )
        failing = {
            p: n for p in PROVIDERS if (n := await consecutive_failures(conn, p)) >= FAILURES_TO_NOTIFY
        }
        if provider in failing:
            names = ", ".join(f"{PROVIDER_RU[p]} ({n} подряд)" for p, n in failing.items())
            await notify.create_once(
                conn,
                "import_failure",
                None,
                None,
                msk_date(now),
                f"Загрузка расхода не удалась несколько раз подряд: {names}. Решения выдаются как обычно; "
                "загрузка повторится по расписанию. Подробности — на экране «Состояние импорта».",
            )
    log.warning("import_failed", extra={"provider": provider, "error_class": error_class})
    return RunResult(run_id, provider, "failed", error_class=error_class)


async def consecutive_failures(conn: asyncpg.Connection, provider: str) -> int:
    """Число неудачных завершённых загрузок подряд, начиная с последней."""
    n: int = await conn.fetchval(
        "SELECT count(*) FROM import_run WHERE provider = $1 AND status = 'failed' AND created_at > coalesce("
        "(SELECT max(created_at) FROM import_run WHERE provider = $1 AND status = 'ok'), '-infinity')",
        provider,
    )
    return n


def _to_kop(amount: Decimal, currency: str, rate: Decimal | None) -> Kop | None:
    if currency == "RUB":
        return round_half_up(amount * 100)
    if rate is None:
        return None
    return round_half_up(amount * rate * 100)


def _rate(fx: dict[date, Decimal], day: date) -> Decimal | None:
    month = day.replace(day=1)
    known = [m for m in fx if m <= month]
    return fx[max(known)] if known else None


async def _apply(
    conn: asyncpg.Connection, provider: str, lines: Sequence[CostLine], now: datetime
) -> tuple[_Counts, set[str]]:
    sources = {
        r["ref"]: (r["department_id"], r["mode"])
        for r in await conn.fetch(
            "SELECT ref, department_id, mode FROM provider_source WHERE provider = $1", provider
        )
    }
    fx = {r["month"]: r["usd_rub"] for r in await conn.fetch("SELECT month, usd_rub FROM fx_rate")}
    # строки одного ключа (например, вход и выход одной модели) складываются
    merged: dict[tuple[str, str, date, str], Decimal] = {}
    for x in lines:
        key = (x.source_ref, x.model, x.day, x.currency)
        merged[key] = merged.get(key, Decimal(0)) + x.amount
    counts, unmapped = _Counts(), set()
    for (ref, model, day, currency), amount in sorted(merged.items()):
        dept, mode = sources.get(ref, (None, None))
        if dept is None:
            status, dept = "unallocated", None
            unmapped.add(ref)
        else:
            status = "charged" if mode == "import" else "reconcile"
        rate = None if currency == "RUB" else _rate(fx, day)
        kop = _to_kop(amount, currency, rate)
        old = await conn.fetchrow(
            "SELECT amount_orig, currency, amount_kop, department_id, status, version FROM import_line "
            "WHERE provider = $1 AND source_ref = $2 AND model = $3 AND day = $4 FOR UPDATE",
            provider,
            ref,
            model,
            day,
        )
        if old is None:
            await conn.execute(
                "INSERT INTO import_line(provider, source_ref, model, day, amount_orig, currency, fx, "
                "amount_kop, department_id, status, version, fetched_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 1, $11)",
                provider,
                ref,
                model,
                day,
                amount,
                currency,
                rate,
                kop,
                dept,
                status,
                now,
            )
        else:
            changed = old["amount_orig"] != amount or old["currency"] != currency
            counts.changed += changed
            await conn.execute(
                "UPDATE import_line SET amount_orig = $5, currency = $6, fx = $7, amount_kop = $8, "
                "department_id = $9, status = $10, version = version + $11, fetched_at = $12 "
                "WHERE provider = $1 AND source_ref = $2 AND model = $3 AND day = $4",
                provider,
                ref,
                model,
                day,
                amount,
                currency,
                rate,
                kop,
                dept,
                status,
                int(changed),
                now,
            )
        await _ledger_delta(conn, day, old, dept if status == "charged" else None, kop)
        if status == "charged":
            counts.charged += 1
        elif status == "reconcile":
            counts.reconcile += 1
        else:
            counts.unallocated += 1
    return counts, unmapped


async def _ledger_delta(
    conn: asyncpg.Connection,
    day: date,
    old: asyncpg.Record | None,
    new_dept: UUID | None,
    new_kop: Kop | None,
) -> None:
    """Дельта списанной суммы в реестре подразделений: старое снимается, новое добавляется (ADR-004)."""
    deltas: dict[UUID, Kop] = {}
    if (
        old is not None
        and old["status"] == "charged"
        and old["department_id"]
        and old["amount_kop"] is not None
    ):
        deltas[old["department_id"]] = -old["amount_kop"]
    if new_dept is not None and new_kop is not None:
        deltas[new_dept] = deltas.get(new_dept, 0) + new_kop
    for dept, delta in sorted(deltas.items()):
        await repo.add_ledger(conn, [("department", dept)], day, delta, source="import")


# ---------------------------------------------------------------- расписание и очередь


def window(settings: Settings, now: datetime) -> tuple[date, date]:
    """Окно плановой загрузки: прошедшие сутки UTC и IMPORT_WINDOW_DAYS − 1 до них (уточнения провайдера)."""
    day_to = now.astimezone(UTC).date() - timedelta(days=1)
    return day_to - timedelta(days=settings.import_window_days - 1), day_to


def key_configured(settings: Settings, provider: str) -> bool:
    key = settings.anthropic_admin_key if provider == "anthropic" else settings.openai_admin_key
    return key is not None and bool(key.get_secret_value())


def connector(provider: str, settings: Settings) -> CostSource | None:
    """Коннектор провайдера; None — коннектор ещё не поставлен (T062/T063) или урезан (🔻3)."""
    module_name, cls = CONNECTORS[provider]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return None
    source: CostSource = getattr(module, cls)(settings)
    return source


async def scheduled(
    pool: asyncpg.Pool,
    settings: Settings,
    now: datetime,
    *,
    make_source: Callable[[str, Settings], CostSource | None] = connector,
) -> list[RunResult]:
    """Такт задачи worker: очередь ручных запусков, затем плановая загрузка и повтор раз в час.

    IMPORT_ENABLED=false — сразу выход: ни запусков, ни соединений (5.6).
    """
    if not settings.import_enabled:
        return []
    results: list[RunResult] = []
    msk_now = now.astimezone(MSK)
    day_start = datetime.combine(msk_now.date(), time(0), tzinfo=MSK)
    for provider in PROVIDERS:
        if not key_configured(settings, provider):
            continue
        source = make_source(provider, settings)
        async with pool.acquire() as conn:
            queued = await conn.fetch(
                "SELECT id, day_from, day_to FROM import_run WHERE provider = $1 AND status = 'queued' "
                "ORDER BY created_at",
                provider,
            )
            in_hours = settings.import_hour_msk <= msk_now.hour <= settings.import_retry_until_hour_msk
            due = in_hours and not await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM import_run WHERE provider = $1 AND ("
                "(status = 'ok' AND started_at >= $2) OR started_at > $3))",
                provider,
                day_start,
                now - timedelta(hours=1),
            )
        if source is None:
            for q in queued:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE import_run SET status = 'failed', started_at = $2, finished_at = $2, "
                        "error_class = 'internal' WHERE id = $1",
                        q["id"],
                        now,
                    )
            if queued or due:
                log.error("import_connector_missing", extra={"provider": provider})
            continue
        for q in queued:
            results.append(await run(pool, source, q["day_from"], q["day_to"], now, run_id=q["id"]))
        if due and not queued:
            day_from, day_to = window(settings, now)
            results.append(await run(pool, source, day_from, day_to, now))
    return results
