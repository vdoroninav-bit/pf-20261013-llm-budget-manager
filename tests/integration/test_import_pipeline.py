"""T060: конвейер импорта на уровне БД (ADR-004, FR-024): ключ строки, дельта в реестре, конкурентные запуски,
строки без курса. Источник — `RecordedSource` (без сети); договорённости — tests/fixtures/imports.py.

Написаны до реализации (T061).
"""

import asyncio
from datetime import date
from typing import Any

import asyncpg
import pytest

from tests.fixtures.imports import (
    cost_line,
    import_ledger,
    import_lines,
    last_run,
    provider_source,
    recorded,
    run_import,
)
from tests.fixtures.qa import one
from tests.fixtures.seed import Seed

pytestmark = [
    pytest.mark.db,
    pytest.mark.xfail(
        strict=True,
        raises=(AssertionError, ImportError),
        reason="T061: конвейер импорта и справочник источников",
    ),
]

OCT13 = date(2026, 10, 13)


async def test_line_key_is_provider_source_model_day(app: Any, seed: Seed, db: asyncpg.Connection) -> None:
    dep = await seed.department("Аналитика")
    await seed.fx(date(2026, 10, 1), "95.5")
    await provider_source(db, "wrkspc_analytics", dep, "import")
    await provider_source(db, "proj_analytics", dep, "import", provider="openai", kind="project")
    lines = [
        cost_line("wrkspc_analytics", OCT13, "1.00", model="claude-sonnet-4-5"),
        cost_line("wrkspc_analytics", OCT13, "2.00", model="claude-haiku-4-5"),
        cost_line("wrkspc_analytics", date(2026, 10, 12), "3.00", model="claude-sonnet-4-5"),
    ]
    await run_import(app, recorded("anthropic", lines), date(2026, 10, 12), OCT13)
    await run_import(
        app,
        recorded(
            "openai", [cost_line("proj_analytics", OCT13, "4.00", provider="openai", source_kind="project")]
        ),
        OCT13,
    )
    keys = {(r["provider"], r["source_ref"], r["model"], r["day"]) for r in await import_lines(db)}
    assert len(keys) == 4
    assert await import_ledger(db, dep, OCT13) == 9550 + 19_100 + 38_200
    assert await import_ledger(db, dep, date(2026, 10, 12)) == 28_650
    assert (await last_run(db, "openai"))["lines_charged"] == 1


async def test_concurrent_runs_do_not_double_charge(app: Any, seed: Seed, db: asyncpg.Connection) -> None:
    """Ручной запуск совпал с плановым: итог как от одной загрузки."""
    dep = await seed.department("Аналитика")
    await seed.fx(date(2026, 10, 1), "95.5")
    await provider_source(db, "wrkspc_analytics", dep, "import")
    lines = [cost_line("wrkspc_analytics", OCT13, "12.34")]
    results = await asyncio.gather(
        *(run_import(app, recorded("anthropic", lines), OCT13) for _ in range(4)), return_exceptions=True
    )
    assert not [r for r in results if isinstance(r, AssertionError | ImportError)], results
    assert await import_ledger(db, dep) == 117_847
    line = one(await import_lines(db))
    assert line["version"] == 1


async def test_ledger_always_equals_sum_of_charged_lines(
    app: Any, seed: Seed, db: asyncpg.Connection
) -> None:
    """Инвариант FR-026/SC-007: импорт в реестре подразделения = Σ amount_kop строк со статусом charged."""
    dep = await seed.department("Аналитика")
    await seed.fx(date(2026, 10, 1), "95.5")
    await provider_source(db, "wrkspc_analytics", dep, "import")
    for amount in ("1.00", "1.10", "0.90", "0.90", "5.55"):
        await run_import(app, recorded("anthropic", [cost_line("wrkspc_analytics", OCT13, amount)]), OCT13)
        charged = await db.fetchval(
            "SELECT coalesce(sum(amount_kop), 0) FROM import_line WHERE status = 'charged' AND department_id = $1",
            dep,
        )
        assert await import_ledger(db, dep) == charged
    assert await import_ledger(db, dep) == 53_003  # 5,55 × 95,5 = 530,025 → 530,03 ₽
    line = one(await import_lines(db))
    assert line["version"] == 4  # 1,10 / 0,90 / 5,55 меняли сумму, повтор 0,90 — нет


async def test_line_without_rate_is_not_charged_until_rate_entered(
    app: Any, seed: Seed, db: asyncpg.Connection
) -> None:
    """Курса USD нет вообще: строка сохранена без суммы в ₽, бюджет не тронут; курс введён — следующая загрузка
    окна списывает («новый курс применяется к ещё не обработанным данным»)."""
    dep = await seed.department("Аналитика")
    await provider_source(db, "wrkspc_analytics", dep, "import")
    src = recorded("anthropic", [cost_line("wrkspc_analytics", OCT13, "1.00")])
    await run_import(app, src, OCT13)
    line = one(await import_lines(db))
    assert line["amount_kop"] is None and line["fx"] is None
    assert await import_ledger(db, dep) == 0

    await seed.fx(date(2026, 10, 1), "95.5")
    await run_import(app, src, OCT13)
    line = one(await import_lines(db))
    assert line["amount_kop"] == 9550 and await import_ledger(db, dep) == 9550


async def test_failed_run_changes_nothing(app: Any, seed: Seed, db: asyncpg.Connection) -> None:
    dep = await seed.department("Аналитика")
    await seed.fx(date(2026, 10, 1), "95.5")
    await provider_source(db, "wrkspc_analytics", dep, "import")
    await run_import(app, recorded("anthropic", [cost_line("wrkspc_analytics", OCT13, "1.00")]), OCT13)
    await run_import(app, recorded("anthropic", error_class="bad_response"), OCT13)
    assert await import_ledger(db, dep) == 9550
    line = one(await import_lines(db))
    assert line["version"] == 1
    run = await last_run(db)
    assert (run["status"], run["error_class"], run["finished_at"] is not None) == (
        "failed",
        "bad_response",
        True,
    )
