"""Общее для коннекторов провайдеров (T062, T063): постраничная загрузка через `bm.egress` и классы ошибок.

Классы ошибок (`ImportErrorClass` контракта, FR-025):
- 401, 403, ключ не задан — `auth_rejected`;
- 408, 429, 5xx, обрыв соединения, таймаут, отказ прокси — `provider_unavailable`;
- прочие 4xx, не JSON, неожиданная структура, зацикленный курсор — `bad_response`.

В текст `ImportFailed` попадают только код статуса и класс исключения: ни тела ответа провайдера, ни ключа,
ни URL прокси (в них может быть логин и пароль) — конвейер всё равно пишет в `import_run` только класс.
"""

import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from bm.egress.client import Client, TransportError
from bm.provider_import.sources.base import ImportFailed

MAX_PAGES = 1000  # страховка от бесконечной пагинации: окно импорта — до нескольких десятков суток


def classify_status(status: int) -> str:
    if status in (401, 403):
        return "auth_rejected"
    if status in (408, 429) or status >= 500:
        return "provider_unavailable"
    return "bad_response"


def window_utc(day_from: date, day_to: date) -> tuple[datetime, datetime]:
    """[начало day_from, начало дня после day_to) в UTC: сутки провайдера = сутки UTC (ADR-004)."""
    start = datetime.combine(day_from, time(0), tzinfo=UTC)
    end = datetime.combine(day_to + timedelta(days=1), time(0), tzinfo=UTC)
    return start, end


async def pages(
    client: Client,
    path: str,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
) -> AsyncIterator[dict[str, Any]]:
    """Страницы ответа `{data, has_more, next_page}`; следующая — с параметром `page` = `next_page`."""
    seen: set[str] = set()
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        query = dict(params) | ({"page": cursor} if cursor is not None else {})
        body = await get_json(client, path, query, headers)
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise ImportFailed("bad_response", "в ответе нет списка data")
        yield body
        if not body.get("has_more"):
            return
        cursor = body.get("next_page")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise ImportFailed("bad_response", "has_more без нового next_page")
        seen.add(cursor)
    raise ImportFailed("bad_response", f"больше {MAX_PAGES} страниц")


async def get_json(client: Client, path: str, params: Mapping[str, Any], headers: Mapping[str, str]) -> Any:
    try:
        resp = await client.get(path, params=params, headers=dict(headers))
    except TransportError as exc:  # соединение, таймаут, прокси
        raise ImportFailed("provider_unavailable", type(exc).__name__) from None
    if resp.status_code != 200:
        raise ImportFailed(classify_status(resp.status_code), f"HTTP {resp.status_code}")
    try:
        # Дробные числа — сразу Decimal: без двоичных «хвостов» float (принцип VI).
        return json.loads(resp.content, parse_float=Decimal)
    except (ValueError, UnicodeDecodeError):
        raise ImportFailed("bad_response", "ответ не JSON") from None


def as_dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ImportFailed("bad_response", f"{what}: ожидался объект")
    return value


def as_list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise ImportFailed("bad_response", f"{what}: ожидался список")
    return value


def as_amount(value: Any, what: str) -> Decimal:
    """Сумма из строки или числа JSON → конечный `Decimal` (bool, NaN, бесконечность — ошибка ответа)."""
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ImportFailed("bad_response", f"{what}: сумма не число")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ImportFailed("bad_response", f"{what}: сумма не число") from None
    if not amount.is_finite():
        raise ImportFailed("bad_response", f"{what}: сумма не число")
    return amount


def optional_str(value: Any, what: str) -> str | None:
    if value is None or isinstance(value, str):
        return value or None
    raise ImportFailed("bad_response", f"{what}: ожидалась строка")
