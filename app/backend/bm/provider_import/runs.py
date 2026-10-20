"""Состояние импорта, журнал запусков и ручной запуск (FR-025, T061): `getImportStatus`, `listImportRuns`,
`runImport`. Ручной запуск только ставится в очередь: загрузку выполняет worker (`pipeline.scheduled`).

Значение ключа администратора провайдера наружу не отдаётся никогда — только «задан / не задан» (принцип II).
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import asyncpg

from bm.domain.periods import MSK
from bm.provider_import.pipeline import consecutive_failures, key_configured, window
from bm.provider_import.sources.base import PROVIDERS
from bm.settings import Settings

_RUN_COLS = (
    "id, provider, status, started_at, finished_at, day_from, day_to, lines_charged, lines_reconcile, "
    "lines_unallocated, lines_changed, error_class"
)


class ImportUnavailable(Exception):
    """Ручной запуск невозможен: импорт выключен или ключ не задан (409)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _ts(v: datetime | None) -> str | None:
    return None if v is None else v.astimezone(MSK).isoformat()


def _run_out(r: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(r["id"]),
        "provider": r["provider"],
        "status": r["status"],
        "started_at": _ts(r["started_at"]),
        "finished_at": _ts(r["finished_at"]),
        "day_from": r["day_from"].isoformat(),
        "day_to": r["day_to"].isoformat(),
        "lines_charged": r["lines_charged"],
        "lines_reconcile": r["lines_reconcile"],
        "lines_unallocated": r["lines_unallocated"],
        "lines_changed": r["lines_changed"],
        "error_class": r["error_class"],
    }


async def list_runs(
    conn: asyncpg.Connection, *, provider: str | None, page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    total: int = await conn.fetchval(
        "SELECT count(*) FROM import_run WHERE $1::text IS NULL OR provider = $1", provider
    )
    rows = await conn.fetch(
        f"SELECT {_RUN_COLS} FROM import_run WHERE $1::text IS NULL OR provider = $1 "  # noqa: S608
        "ORDER BY created_at DESC, id LIMIT $2 OFFSET $3",
        provider,
        page_size,
        (page - 1) * page_size,
    )
    return [_run_out(r) for r in rows], total


async def queue(
    conn: asyncpg.Connection,
    settings: Settings,
    provider: str,
    day_from: date | None,
    day_to: date | None,
    now: datetime,
) -> dict[str, Any]:
    """Ручной запуск → `import_run` со статусом `queued`. Окно по умолчанию — как у плановой загрузки."""
    if not settings.import_enabled:
        raise ImportUnavailable(
            "import_disabled",
            "Импорт выключен (IMPORT_ENABLED=false): исходящих обращений к провайдерам нет.",
        )
    if not key_configured(settings, provider):
        raise ImportUnavailable(
            "provider_key_missing", "Ключ администратора провайдера не задан в переменных окружения сервера."
        )
    default_from, default_to = window(settings, now)
    lo, hi = day_from or default_from, day_to or default_to
    if lo > hi:
        raise ValueError("day_from позже day_to")
    row = await conn.fetchrow(
        "INSERT INTO import_run(provider, status, day_from, day_to) "  # noqa: S608
        "VALUES ($1, 'queued', $2, $3) "
        f"RETURNING {_RUN_COLS}",
        provider,
        lo,
        hi,
    )
    return _run_out(row)


async def status(
    conn: asyncpg.Connection, settings: Settings, now: datetime, *, full: bool
) -> dict[str, Any]:
    """`full=False` — владелец бюджета: без несопоставленных источников и класса ошибки (контракт)."""
    providers = []
    for provider in PROVIDERS:
        agg = await conn.fetchrow(
            "SELECT max(day_to) FILTER (WHERE status = 'ok') AS last_ok, "
            "max(coalesce(finished_at, started_at)) AS last_run FROM import_run WHERE provider = $1",
            provider,
        )
        last = await conn.fetchrow(
            "SELECT status, error_class FROM import_run WHERE provider = $1 AND status IN ('ok', 'failed') "
            "ORDER BY created_at DESC, id LIMIT 1",
            provider,
        )
        unallocated = [
            r["source_ref"]
            for r in await conn.fetch(
                "SELECT DISTINCT source_ref FROM import_line WHERE provider = $1 AND status = 'unallocated' "
                "ORDER BY source_ref",
                provider,
            )
        ]
        last_ok: date | None = agg["last_ok"]
        lag = None
        if last_ok is not None:
            loaded_until = datetime.combine(last_ok + timedelta(days=1), time(0), tzinfo=UTC)
            lag = max(0, int((now - loaded_until) / timedelta(hours=1)))
        error = last["error_class"] if last is not None and last["status"] == "failed" else None
        providers.append(
            {
                "provider": provider,
                "configured": key_configured(settings, provider),
                "last_success_day": None if last_ok is None else last_ok.isoformat(),
                "last_run_at": _ts(agg["last_run"]),
                "lag_hours": lag,
                "consecutive_failures": await consecutive_failures(conn, provider),
                "last_error_class": error if full else None,
                "unallocated_sources": unallocated if full else [],
            }
        )
    return {"enabled": settings.import_enabled, "providers": providers}
