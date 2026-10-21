"""Коннектор OpenAI (T063, ADR-004): Admin API `GET /v1/organization/costs`.

Запрос: `start_time` / `end_time` — unix-секунды начала суток UTC (конец исключительно), `bucket_width=1d`,
`group_by[]=project_id`, `group_by[]=line_item` (массив в запросе — со скобками, как в SDK `openai` 3.35.1),
пагинация `page` = `next_page`. Ключ — только `OPENAI_ADMIN_KEY(_FILE)` в `Authorization: Bearer`;
HTTP-клиент — только `bm.egress.get_client("openai")`, поэтому `IMPORT_ENABLED` и `IMPORT_HTTPS_PROXY`
действуют автоматически, своей настройки прокси у коннектора нет.

Разбор ответа. Структура сверена с типами SDK `openai` 3.35.1 (`UsageCostsResponse`) [Факт]; значения — нет
(спайка не было, фикстуры `# synthetic`). Правила:
1. корзина `{start_time, end_time, results}`; сутки строки — дата `start_time` в UTC; корзины вне окна
   отбрасываются;
2. `amount = {value, currency}`; `value` читается из JSON сразу как `Decimal` (0.1 + 0.2 = 0.3 ровно);
   `amount` или `value` = null — строки нет; валюта не `usd` — `bad_response`;
3. модель — часть `line_item` до первой запятой («gpt-4o-2024-08-06, input» → `gpt-4o-2024-08-06`): вход и
   выход одной модели складываются в одну строку; `line_item` без запятой берётся целиком, null — `unknown`
   [Гипотеза: формат `line_item` — «модель, вид»];
4. `project_id = null` — проект по умолчанию, источник `default` [Гипотеза].
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from bm.egress.client import Transport, get_client
from bm.provider_import.sources import _http
from bm.provider_import.sources.base import CostLine, ImportFailed, Provider
from bm.settings import Settings

PATH = "/v1/organization/costs"
DEFAULT_PROJECT = "default"


def model_from_line_item(line_item: str | None) -> str:
    if not line_item:
        return "unknown"
    return line_item.split(",", 1)[0].strip() or "unknown"


def _day(value: Any) -> date:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ImportFailed("bad_response", "start_time: ожидались unix-секунды")
    try:
        return datetime.fromtimestamp(value, tz=UTC).date()
    except (OverflowError, OSError, ValueError):
        raise ImportFailed("bad_response", "start_time: вне диапазона") from None


def parse(pages: Iterable[dict[str, Any]], day_from: date, day_to: date) -> list[CostLine]:
    """Строки расхода: сумма в USD по (проект, модель, сутки UTC)."""
    totals: dict[tuple[str, str, date], Decimal] = {}
    for page in pages:
        for bucket in _http.as_list(page.get("data"), "data"):
            b = _http.as_dict(bucket, "data[]")
            day = _day(b.get("start_time"))
            results = _http.as_list(b.get("results"), "results")
            if not day_from <= day <= day_to:
                continue
            for item in results:
                row = _http.as_dict(item, "results[]")
                amount = row.get("amount")
                if amount is None:
                    continue
                amount = _http.as_dict(amount, "amount")
                if amount.get("value") is None:
                    continue
                currency = amount.get("currency")
                if not isinstance(currency, str) or currency.lower() != "usd":
                    raise ImportFailed("bad_response", "amount.currency: ожидалась usd")
                value = _http.as_amount(amount.get("value"), "amount.value")
                ref = _http.optional_str(row.get("project_id"), "project_id") or DEFAULT_PROJECT
                model = model_from_line_item(_http.optional_str(row.get("line_item"), "line_item"))
                key = (ref, model, day)
                totals[key] = totals.get(key, Decimal(0)) + value
    return [
        CostLine(
            provider="openai",
            source_kind="project",
            source_ref=ref,
            model=model,
            day=day,
            amount=amount,
            currency="USD",
        )
        for (ref, model, day), amount in sorted(totals.items())
    ]


class OpenAISource:
    provider: Provider = "openai"

    def __init__(self, settings: Settings, *, transport: Transport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def fetch(self, day_from: date, day_to: date) -> list[CostLine]:
        # Сначала клиент: при IMPORT_ENABLED=false — EgressDisabled и ни одного соединения.
        client = get_client("openai", self._settings, transport=self._transport)
        async with client:
            key = self._settings.openai_admin_key
            if key is None or not key.get_secret_value():
                raise ImportFailed("auth_rejected", "OPENAI_ADMIN_KEY не задан")
            start, end = _http.window_utc(day_from, day_to)
            params = {
                "start_time": int(start.timestamp()),
                "end_time": int(end.timestamp()),
                "bucket_width": "1d",
                "group_by[]": ["project_id", "line_item"],
            }
            headers = {"authorization": f"Bearer {key.get_secret_value()}"}
            pages = [p async for p in _http.pages(client, PATH, params, headers)]
        return parse(pages, day_from, day_to)
