"""T070, принцип II / R-7: значения `ANTHROPIC_ADMIN_KEY` и `OPENAI_ADMIN_KEY` не встречаются в журналах,
ответах API и дампе БД — ни при обычной работе, ни при загрузке импорта, ни при отказе провайдера.

Ключи — тестовые строки из tests/fixtures/imports.py. Провайдер подменён транспортом httpx (без сети).
"""

import logging
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from tests.fixtures.imports import ANTHROPIC_KEY, OPENAI_KEY, FakeProvider, provider_source, run_import
from tests.fixtures.qa import admin_client_login
from tests.fixtures.seed import Seed
from tests.security.test_no_content import db_dump, log_text

pytestmark = pytest.mark.db

KEYS = (ANTHROPIC_KEY, OPENAI_KEY)
T061 = pytest.mark.xfail(strict=True, raises=(AssertionError, ImportError), reason="T061: импорт и его API")
T062 = pytest.mark.xfail(
    strict=True, raises=(AssertionError, ImportError), reason="T062: коннектор Anthropic"
)


@pytest.fixture
def settings(settings: Any) -> Any:
    return settings.model_copy(
        update={
            "import_enabled": True,
            "anthropic_admin_key": SecretStr(ANTHROPIC_KEY),
            "openai_admin_key": SecretStr(OPENAI_KEY),
        }
    )


def assert_no_keys(where: str, text: str) -> None:
    for k in KEYS:
        assert k not in text, f"значение ключа администратора провайдера найдено: {where}"


def test_settings_never_print_keys(settings: Any, tmp_path: Path) -> None:
    from bm.settings import Settings

    assert_no_keys("repr/str Settings", repr(settings) + str(settings))
    assert_no_keys("model_dump_json", settings.model_dump_json())
    key_file = tmp_path / "anthropic_key"
    key_file.write_text(ANTHROPIC_KEY + "\n", encoding="utf-8")
    from_file = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql://x@127.0.0.1/x_test",
        session_secret="s" * 32,
        anthropic_admin_key_file=key_file,
    )
    assert from_file.anthropic_admin_key is not None
    assert from_file.anthropic_admin_key.get_secret_value() == ANTHROPIC_KEY  # *_FILE читается
    assert_no_keys("Settings из *_FILE", repr(from_file) + from_file.model_dump_json())


async def test_api_logs_and_db_without_keys(
    client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        texts = [(await client.get(p)).text for p in ("/healthz", "/readyz", "/metrics")]
        await admin_client_login(client, seed)
        texts += [(await client.get(p)).text for p in ("/api/v1/auth/me", "/api/v1/departments")]
    assert_no_keys("ответы API", "\n".join(texts))
    assert_no_keys("журнал", log_text(caplog.records))
    assert_no_keys("дамп БД", await db_dump(db))


@T061
async def test_import_api_never_returns_keys(
    app: Any, client: httpx.AsyncClient, seed: Seed, db: asyncpg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    csrf = await admin_client_login(client, seed)
    with caplog.at_level(logging.DEBUG):
        status = await client.get("/api/v1/imports/status")
        assert status.status_code == 200, status.text
        assert {p["provider"]: p["configured"] for p in status.json()["providers"]} == {
            "anthropic": True,
            "openai": True,
        }
        queued = await client.post("/api/v1/imports/run", json={"provider": "openai"}, headers=csrf)
        assert queued.status_code == 202, queued.text
        runs = await client.get("/api/v1/imports/runs")
        sources = await client.get("/api/v1/provider-sources")
    assert_no_keys("ответы API импорта", status.text + queued.text + runs.text + sources.text)
    assert_no_keys("журнал", log_text(caplog.records))
    assert_no_keys("дамп БД", await db_dump(db))


@T062
@pytest.mark.parametrize("status", [200, 401, 500])
async def test_connector_run_never_logs_or_stores_key(
    app: Any, settings: Any, seed: Seed, db: asyncpg.Connection, caplog: pytest.LogCaptureFixture, status: int
) -> None:
    """Ключ уходит только в заголовок запроса к провайдеру — не в журнал, не в import_run, не в БД."""
    from bm.provider_import.sources.anthropic import AnthropicSource
    from tests.fixtures.imports import load_pages

    dep = await seed.department("Аналитика")
    await seed.fx(date(2026, 10, 1), "95.5")
    await provider_source(db, "wrkspc_analytics", dep, "import")
    pages = load_pages("anthropic", "cost_report_page1.synthetic.json", "cost_report_page2.synthetic.json")
    fake = FakeProvider(pages, status=status)
    with caplog.at_level(logging.DEBUG):
        await run_import(
            app, AnthropicSource(settings, transport=fake.transport), date(2026, 10, 12), date(2026, 10, 13)
        )
    assert fake.requests and fake.requests[0].headers["x-api-key"] == ANTHROPIC_KEY  # ключ ушёл провайдеру
    assert_no_keys("журнал", log_text(caplog.records))
    assert_no_keys("дамп БД", await db_dump(db))
