"""Коннектор Anthropic (T062, ADR-004): Admin API `GET /v1/organizations/cost_report`.

Запрос: `starting_at` / `ending_at` — начало суток UTC (конец исключительно), `bucket_width=1d`,
`group_by[]=workspace_id`, `group_by[]=description`, пагинация `page` = `next_page`. Ключ — только
`ANTHROPIC_ADMIN_KEY(_FILE)` в заголовке `x-api-key`; HTTP-клиент — только
`bm.egress.get_client("anthropic")`, поэтому `IMPORT_ENABLED` и `IMPORT_HTTPS_PROXY` действуют здесь
автоматически.

Разбор ответа. Формат НЕ сверен с записанными ответами (спайка не было, фикстуры `# synthetic`). Допущения
[Гипотеза], которые нужно проверить на первом реальном ответе:
1. страница — `{data: [{starting_at, ending_at, results: [...]}], has_more, next_page}`;
2. `amount` — десятичная строка в наименьших единицах валюты (центах): `"1234.5"` = 12,345 $;
3. `currency` — `"USD"`; другая валюта — `bad_response` (в рубли пересчитывается только USD);
4. модель — поле `model`, если оно есть; иначе извлекается из `description` («Claude Sonnet 4.5 Usage - Input
   Tokens» → `claude-sonnet-4-5`); нет и его — `cost_type` (веб-поиск, исполнение кода), иначе `unknown`;
5. `workspace_id = null` — рабочее пространство по умолчанию, источник `default`;
6. сутки строки — дата `starting_at` в UTC; корзины вне [day_from, day_to] отбрасываются.
Суммы складываются по ключу строки импорта (источник, модель, сутки) без округления: вход, выход и кэш одной
модели — одна строка; в ₽ конвейер переводит один раз (принцип VI).
"""

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from bm.egress.client import Transport, get_client
from bm.provider_import.sources import _http
from bm.provider_import.sources.base import CostLine, ImportFailed, Provider
from bm.settings import Settings

PATH = "/v1/organizations/cost_report"
API_VERSION = "2023-06-01"
DEFAULT_WORKSPACE = "default"
_USAGE_SUFFIX = re.compile(r"\s+usage\b.*$", re.IGNORECASE)
_NON_ID = re.compile(r"[^a-z0-9]+")


def model_from_description(description: str) -> str:
    """«Claude Opus 4.1 Usage - Output Tokens» → `claude-opus-4-1`; «Web Search Usage» → `web-search`."""
    name = _USAGE_SUFFIX.sub("", description.strip())
    return _NON_ID.sub("-", name.lower()).strip("-") or "unknown"


def _day(value: Any) -> date:
    if not isinstance(value, str):
        raise ImportFailed("bad_response", "starting_at: ожидалась строка")
    try:
        return datetime.fromisoformat(value).astimezone(UTC).date()
    except ValueError:
        raise ImportFailed("bad_response", "starting_at: не дата ISO 8601") from None


def _model(row: dict[str, Any]) -> str:
    model = _http.optional_str(row.get("model"), "model")
    if model:
        return model
    description = _http.optional_str(row.get("description"), "description")
    if description:
        return model_from_description(description)
    return _http.optional_str(row.get("cost_type"), "cost_type") or "unknown"


def parse(pages: Iterable[dict[str, Any]], day_from: date, day_to: date) -> list[CostLine]:
    """Строки расхода из страниц ответа: сумма в USD по (рабочее пространство, модель, сутки UTC)."""
    totals: dict[tuple[str, str, date], Decimal] = {}
    for page in pages:
        for bucket in _http.as_list(page.get("data"), "data"):
            b = _http.as_dict(bucket, "data[]")
            day = _day(b.get("starting_at"))
            results = _http.as_list(b.get("results"), "results")
            if not day_from <= day <= day_to:
                continue
            for item in results:
                row = _http.as_dict(item, "results[]")
                currency = row.get("currency")
                if not isinstance(currency, str) or currency.upper() != "USD":
                    raise ImportFailed("bad_response", "currency: ожидалась USD")
                cents = _http.as_amount(row.get("amount"), "amount")
                ref = _http.optional_str(row.get("workspace_id"), "workspace_id") or DEFAULT_WORKSPACE
                key = (ref, _model(row), day)
                totals[key] = totals.get(key, Decimal(0)) + cents / 100
    return [
        CostLine(
            provider="anthropic",
            source_kind="workspace",
            source_ref=ref,
            model=model,
            day=day,
            amount=amount,
            currency="USD",
        )
        for (ref, model, day), amount in sorted(totals.items())
    ]


class AnthropicSource:
    provider: Provider = "anthropic"

    def __init__(self, settings: Settings, *, transport: Transport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def fetch(self, day_from: date, day_to: date) -> list[CostLine]:
        # Сначала клиент: при IMPORT_ENABLED=false — EgressDisabled и ни одного соединения.
        client = get_client("anthropic", self._settings, transport=self._transport)
        async with client:
            key = self._settings.anthropic_admin_key
            if key is None or not key.get_secret_value():
                raise ImportFailed("auth_rejected", "ANTHROPIC_ADMIN_KEY не задан")
            start, end = _http.window_utc(day_from, day_to)
            params = {
                "starting_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ending_at": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "bucket_width": "1d",
                "group_by[]": ["workspace_id", "description"],
            }
            headers = {"x-api-key": key.get_secret_value(), "anthropic-version": API_VERSION}
            pages = [p async for p in _http.pages(client, PATH, params, headers)]
        return parse(pages, day_from, day_to)
