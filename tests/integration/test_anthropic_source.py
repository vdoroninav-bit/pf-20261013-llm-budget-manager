"""T062: коннектор Anthropic `GET /v1/organizations/cost_report` на подменном транспорте httpx (без сети).

Формат ответа — синтетический (tests/fixtures/anthropic/README.md), сверка со спайком не проводилась: допущения
перечислены в docstring `bm.provider_import.sources.anthropic` и в 14_dev_backend.md (T062).
Основные сценарии (страницы, классы ошибок, флаг) — в tests/acceptance/test_us5_import.py; здесь — правила
разбора, граничные случаи и прокси импорта.
"""

import json
import logging
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from bm.provider_import.sources.base import ImportFailed
from tests.fixtures.imports import (
    ANTHROPIC_KEY,
    FakeProvider,
    last_run,
    load_pages,
    provider_source,
    run_import,
)
from tests.fixtures.seed import Seed
from tests.security.test_no_content import db_dump, log_text

pytestmark = [pytest.mark.db]

OCT12, OCT13 = date(2026, 10, 12), date(2026, 10, 13)
PROXY_PASSWORD = "Pr0xy-Secret-77"
PROXY = f"http://svc-import:{PROXY_PASSWORD}@192.0.2.10:3128"


@pytest.fixture
def settings(settings: Any) -> Any:
    return settings.model_copy(
        update={"import_enabled": True, "anthropic_admin_key": SecretStr(ANTHROPIC_KEY)}
    )


def source(settings: Any, transport: httpx.AsyncBaseTransport | None) -> Any:
    from bm.provider_import.sources.anthropic import AnthropicSource

    return AnthropicSource(settings, transport=transport)


def bucket(day: date, *results: dict[str, Any]) -> dict[str, Any]:
    nxt = date.fromordinal(day.toordinal() + 1)
    return {"starting_at": f"{day}T00:00:00Z", "ending_at": f"{nxt}T00:00:00Z", "results": list(results)}


def result(amount: str, workspace: str | None = "wrkspc_a", **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "currency": "USD",
        "amount": amount,
        "workspace_id": workspace,
        "description": "Claude Sonnet 4.5 Usage - Input Tokens",
        "model": "claude-sonnet-4-5",
    }
    return out | kw


def page(*buckets: dict[str, Any], next_page: str | None = None) -> dict[str, Any]:
    return {"data": list(buckets), "has_more": next_page is not None, "next_page": next_page}


async def fetch(settings: Any, *pages: dict[str, Any], day_from: date = OCT12, day_to: date = OCT13) -> Any:
    fake = FakeProvider(list(pages))
    lines = await source(settings, fake.transport).fetch(day_from, day_to)
    return fake, lines


async def fails(settings: Any, handler: Any) -> ImportFailed:
    with pytest.raises(ImportFailed) as err:
        await source(settings, httpx.MockTransport(handler)).fetch(OCT12, OCT13)
    assert ANTHROPIC_KEY not in str(err.value)
    return err.value


# ---------------------------------------------------------------- запрос


async def test_request_window_grouping_and_headers(settings: Any) -> None:
    fake, _ = await fetch(settings, page())
    req = fake.requests[0]
    assert req.method == "GET" and req.url.path == "/v1/organizations/cost_report"
    params = req.url.params
    assert params["starting_at"] == "2026-10-12T00:00:00Z"  # сутки UTC, начало включительно
    assert params["ending_at"] == "2026-10-14T00:00:00Z"  # конец — исключительно: день после day_to
    assert params["bucket_width"] == "1d"
    assert params.get_list("group_by[]") == ["workspace_id", "description"]
    assert req.headers["x-api-key"] == ANTHROPIC_KEY
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in req.headers


# ---------------------------------------------------------------- разбор


async def test_amount_in_cents_and_lines_aggregated_per_model_day(settings: Any) -> None:
    _, lines = await fetch(
        settings,
        page(
            bucket(
                OCT13,
                result("1234.5"),
                result("765.5", description="Claude Sonnet 4.5 Usage - Output Tokens"),
                result("0.25", description="Claude Sonnet 4.5 Usage - Cache Read"),
            )
        ),
    )
    assert len(lines) == 1  # ключ строки — (источник, модель, сутки): вход и выход складываются
    line = lines[0]
    assert (line.provider, line.source_kind, line.source_ref, line.model, line.day, line.currency) == (
        "anthropic",
        "workspace",
        "wrkspc_a",
        "claude-sonnet-4-5",
        OCT13,
        "USD",
    )
    assert line.amount == Decimal("20.0025") and isinstance(line.amount, Decimal)  # без округления до цента


async def test_model_from_description_when_model_field_absent(settings: Any) -> None:
    _, lines = await fetch(
        settings,
        page(
            bucket(
                OCT13,
                result("100", model=None, description="Claude Opus 4.1 Usage - Output Tokens"),
                {
                    "currency": "USD",
                    "amount": "50",
                    "workspace_id": "wrkspc_a",
                    "description": "Web Search Usage",
                },
                {"currency": "USD", "amount": "7", "workspace_id": "wrkspc_a", "cost_type": "code_execution"},
            )
        ),
    )
    assert {x.model: x.amount for x in lines} == {
        "claude-opus-4-1": Decimal("1"),
        "web-search": Decimal("0.5"),
        "code_execution": Decimal("0.07"),
    }


async def test_default_workspace_and_days_outside_window(settings: Any) -> None:
    _, lines = await fetch(
        settings,
        page(
            bucket(date(2026, 10, 11), result("999")),
            bucket(OCT12, result("100", workspace=None)),
            bucket(date(2026, 10, 14), result("999")),
        ),
    )
    assert [(x.source_ref, x.day, x.amount) for x in lines] == [("default", OCT12, Decimal("1"))]


async def test_pagination_follows_next_page(settings: Any) -> None:
    fake, lines = await fetch(
        settings,
        page(bucket(OCT12, result("100")), next_page="p2"),
        page(bucket(OCT12, result("100", workspace="wrkspc_b")), next_page="p3"),
        page(bucket(OCT13, result("100"))),
    )
    assert [r.url.params.get("page") for r in fake.requests] == [None, "p2", "p3"]
    assert all(r.url.params["starting_at"] == "2026-10-12T00:00:00Z" for r in fake.requests)
    assert len(lines) == 3


# ---------------------------------------------------------------- классы ошибок


@pytest.mark.parametrize(
    ("status", "error_class"),
    [
        (401, "auth_rejected"),
        (403, "auth_rejected"),
        (429, "provider_unavailable"),
        (500, "provider_unavailable"),
        (529, "provider_unavailable"),
        (400, "bad_response"),
        (404, "bad_response"),
    ],
)
async def test_http_status_classified(settings: Any, status: int, error_class: str) -> None:
    err = await fails(settings, lambda r: httpx.Response(status, json={"error": {"message": ANTHROPIC_KEY}}))
    assert err.error_class == error_class


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
async def test_transport_errors_are_provider_unavailable(settings: Any, exc: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    assert (await fails(settings, handler)).error_class == "provider_unavailable"


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        json.dumps({"data": "x", "has_more": False}).encode(),
        json.dumps(page(bucket(OCT13, result("abc")))).encode(),
        json.dumps(page(bucket(OCT13, result("1", currency="EUR")))).encode(),
        json.dumps(page(bucket(OCT13, result("1")) | {"starting_at": "13.10.2026"})).encode(),
        json.dumps({"data": [], "has_more": True, "next_page": None}).encode(),
        json.dumps(page(bucket(OCT13, result("NaN")))).encode(),
    ],
)
async def test_unexpected_body_is_bad_response(settings: Any, body: bytes) -> None:
    err = await fails(settings, lambda r: httpx.Response(200, content=body))
    assert err.error_class == "bad_response"


async def test_repeating_cursor_is_bad_response(settings: Any) -> None:
    err = await fails(settings, lambda r: httpx.Response(200, json=page(next_page="same")))
    assert err.error_class == "bad_response"


async def test_missing_key_is_auth_rejected_without_request(settings: Any) -> None:
    fake = FakeProvider([page()])
    no_key = settings.model_copy(update={"anthropic_admin_key": None})
    with pytest.raises(ImportFailed) as err:
        await source(no_key, fake.transport).fetch(OCT12, OCT13)
    assert err.value.error_class == "auth_rejected" and fake.requests == []


# ---------------------------------------------------------------- конвейер и прокси


async def test_pipeline_with_fixture_pages(
    app: Any, settings: Any, seed: Seed, db: asyncpg.Connection
) -> None:
    dep = await seed.department("Аналитика")
    await seed.fx(date(2026, 10, 1), "95.5")
    await provider_source(db, "wrkspc_analytics", dep, "import")
    fake = FakeProvider(
        load_pages("anthropic", "cost_report_page1.synthetic.json", "cost_report_page2.synthetic.json")
    )
    res = await run_import(app, source(settings, fake.transport), OCT12, OCT13)
    assert (res.status, res.lines_charged, res.lines_unallocated) == ("ok", 2, 1)


async def test_import_through_proxy_connects_only_to_proxy_and_hides_password(
    app: Any,
    settings: Any,
    db: asyncpg.Connection,
    expect_egress_attempts: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Прокси задан, сети нет: единственная попытка — к прокси; сбой — provider_unavailable; пароля прокси
    нет ни в журнале, ни в import_run и дампе БД."""
    with_proxy = settings.model_copy(update={"import_https_proxy": SecretStr(PROXY)})
    with caplog.at_level(logging.DEBUG):
        await run_import(app, source(with_proxy, None), OCT13)
    assert [(str(a[0]), int(a[1])) for a in expect_egress_attempts] == [("192.0.2.10", 3128)]
    run = await last_run(db)
    assert (run["status"], run["error_class"]) == ("failed", "provider_unavailable")
    for where, text in (("журнал", log_text(caplog.records)), ("дамп БД", await db_dump(db))):
        assert PROXY_PASSWORD not in text, where
        assert ANTHROPIC_KEY not in text, where
