"""T063: коннектор OpenAI `GET /v1/organization/costs` на подменном транспорте httpx (без сети).

Структура ответа и параметры запроса сверены с типами SDK `openai` 3.35.1 (tests/fixtures/openai/README.md);
суммы и `line_item` в фикстурах условные. Основной сценарий — tests/acceptance/test_us5_import.py.
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from bm.egress.client import EgressDisabled, get_client
from bm.provider_import.sources.base import ImportFailed
from tests.fixtures.imports import OPENAI_KEY, FakeProvider, load_pages, provider_source, run_import
from tests.fixtures.seed import Seed

pytestmark = [pytest.mark.db]

OCT12, OCT13 = date(2026, 10, 12), date(2026, 10, 13)
PROXY = "http://svc-import:Pr0xy-Secret-77@192.0.2.10:3128"


@pytest.fixture
def settings(settings: Any) -> Any:
    return settings.model_copy(update={"import_enabled": True, "openai_admin_key": SecretStr(OPENAI_KEY)})


def source(settings: Any, transport: httpx.AsyncBaseTransport | None) -> Any:
    from bm.provider_import.sources.openai import OpenAISource

    return OpenAISource(settings, transport=transport)


def ts(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def bucket(day: date, *results: dict[str, Any]) -> dict[str, Any]:
    return {"object": "bucket", "start_time": ts(day), "end_time": ts(day) + 86400, "results": list(results)}


def result(
    value: Any, project: str | None = "proj_a", line_item: str | None = "gpt-4o, input"
) -> dict[str, Any]:
    return {
        "object": "organization.costs.result",
        "amount": {"value": value, "currency": "usd"},
        "line_item": line_item,
        "project_id": project,
    }


def page(*buckets: dict[str, Any], next_page: str | None = None) -> dict[str, Any]:
    return {
        "object": "page",
        "data": list(buckets),
        "has_more": next_page is not None,
        "next_page": next_page,
    }


async def fetch(settings: Any, *pages: dict[str, Any]) -> Any:
    fake = FakeProvider(list(pages))
    return fake, await source(settings, fake.transport).fetch(OCT12, OCT13)


async def fails(settings: Any, handler: Any) -> ImportFailed:
    with pytest.raises(ImportFailed) as err:
        await source(settings, httpx.MockTransport(handler)).fetch(OCT12, OCT13)
    assert OPENAI_KEY not in str(err.value)
    return err.value


async def test_request_window_grouping_and_auth(settings: Any) -> None:
    fake, _ = await fetch(settings, page())
    req = fake.requests[0]
    assert (req.url.host, req.url.path) == ("api.openai.com", "/v1/organization/costs")
    params = req.url.params
    assert params["start_time"] == str(ts(OCT12))
    assert params["end_time"] == str(ts(date(2026, 10, 14)))  # исключительно
    assert params["bucket_width"] == "1d"
    assert params.get_list("group_by[]") == ["project_id", "line_item"]  # как в SDK: массив со скобками
    assert req.headers["authorization"] == f"Bearer {OPENAI_KEY}"


async def test_amount_is_exact_decimal_and_lines_aggregated(settings: Any) -> None:
    raw = (
        b'{"object":"page","has_more":false,"next_page":null,"data":[{"object":"bucket",'
        + f'"start_time":{ts(OCT13)},"end_time":{ts(OCT13) + 86400},'.encode()
        + b'"results":[{"object":"organization.costs.result","amount":{"value":0.1,"currency":"usd"},'
        b'"line_item":"gpt-4o-2024-08-06, input","project_id":"proj_a"},'
        b'{"object":"organization.costs.result","amount":{"value":0.2,"currency":"usd"},'
        b'"line_item":"gpt-4o-2024-08-06, output","project_id":"proj_a"}]}]}'
    )
    fake = FakeProvider([], raw=raw)
    lines = await source(settings, fake.transport).fetch(OCT12, OCT13)
    assert [(x.source_kind, x.source_ref, x.model, x.day, x.amount, x.currency) for x in lines] == [
        ("project", "proj_a", "gpt-4o-2024-08-06", OCT13, Decimal("0.3"), "USD")  # 0.1 + 0.2 — ровно 0.3
    ]


async def test_line_item_without_model_null_project_and_window(settings: Any) -> None:
    _, lines = await fetch(
        settings,
        page(
            bucket(date(2026, 10, 11), result(5)),
            bucket(OCT12, result(1, project=None), result(2, line_item="web search tool calls")),
            bucket(OCT13, result(None), result(0.5, line_item=None)),
            bucket(date(2026, 10, 14), result(5)),
        ),
    )
    assert {(x.source_ref, x.model, x.day): x.amount for x in lines} == {
        ("default", "gpt-4o", OCT12): Decimal(1),
        ("proj_a", "web search tool calls", OCT12): Decimal(2),
        ("proj_a", "unknown", OCT13): Decimal("0.5"),
    }


async def test_pagination(settings: Any) -> None:
    fake, lines = await fetch(
        settings, page(bucket(OCT12, result(1)), next_page="c2"), page(bucket(OCT13, result(2)))
    )
    assert [r.url.params.get("page") for r in fake.requests] == [None, "c2"]
    assert len(lines) == 2


@pytest.mark.parametrize(
    ("status", "error_class"),
    [(401, "auth_rejected"), (403, "auth_rejected"), (429, "provider_unavailable"), (503, "provider_unavailable"),
     (400, "bad_response")],
)  # fmt: skip
async def test_http_status_classified(settings: Any, status: int, error_class: str) -> None:
    assert (await fails(settings, lambda r: httpx.Response(status, json={}))).error_class == error_class


async def test_transport_error_is_provider_unavailable(settings: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    assert (await fails(settings, handler)).error_class == "provider_unavailable"


@pytest.mark.parametrize(
    "body",
    [
        b"<html>",
        json.dumps({"object": "page", "has_more": False}).encode(),
        json.dumps(page(bucket(OCT13, result("1.5") | {"amount": {"value": 1, "currency": "eur"}}))).encode(),
        json.dumps(page(bucket(OCT13, result(True)))).encode(),
        json.dumps(page(bucket(OCT13, result(1)) | {"start_time": "2026-10-13"})).encode(),
        json.dumps(page(bucket(OCT13, result(1) | {"amount": 5}))).encode(),
    ],
)
async def test_unexpected_body_is_bad_response(settings: Any, body: bytes) -> None:
    assert (await fails(settings, lambda r: httpx.Response(200, content=body))).error_class == "bad_response"


async def test_missing_key_is_auth_rejected_without_request(settings: Any) -> None:
    fake = FakeProvider([page()])
    with pytest.raises(ImportFailed) as err:
        await source(settings.model_copy(update={"openai_admin_key": None}), fake.transport).fetch(
            OCT12, OCT13
        )
    assert err.value.error_class == "auth_rejected" and fake.requests == []


async def test_disabled_flag_no_connection(settings: Any, egress_guard: list[Any]) -> None:
    fake = FakeProvider([page()])
    off = settings.model_copy(update={"import_enabled": False, "import_https_proxy": SecretStr(PROXY)})
    with pytest.raises(EgressDisabled):
        await source(off, fake.transport).fetch(OCT12, OCT13)
    assert fake.requests == [] and egress_guard == []


def test_proxy_reaches_openai_client(settings: Any) -> None:
    """Своей настройки прокси у коннектора нет: клиент `egress("openai")` получает IMPORT_HTTPS_PROXY."""
    from tests.security.test_no_egress import _proxies

    client = get_client("openai", settings.model_copy(update={"import_https_proxy": SecretStr(PROXY)}))
    assert _proxies(client) == ["http://192.0.2.10:3128"]


async def test_pipeline_with_fixture_pages(
    app: Any, settings: Any, seed: Seed, db: asyncpg.Connection
) -> None:
    dep = await seed.department("Аналитика")
    await seed.fx(date(2026, 10, 1), "95.5")
    await provider_source(db, "proj_analytics", dep, "import", provider="openai", kind="project")
    fake = FakeProvider(load_pages("openai", "costs_page1.synthetic.json", "costs_page2.synthetic.json"))
    res = await run_import(app, source(settings, fake.transport), OCT12, OCT13)
    assert (res.status, res.lines_charged, res.lines_unallocated) == ("ok", 2, 1)
    assert (
        await db.fetchval(
            "SELECT sum(amount_kop) FROM ledger_daily WHERE owner_id = $1 AND source = 'import'", dep
        )
        == 9550 + 35813
    )  # 1,00 $ и 3,75 $ по курсу 95,50: 95,50 ₽ + 358,125 → 358,13 ₽ (ROUND_HALF_UP)
