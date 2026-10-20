"""Источник расхода провайдера (12_plan.md §6): протокол `CostSource`, строка `CostLine`, ошибка загрузки.

`day` — сутки UTC провайдера, они же дата расхода (ADR-004). `amount` — `Decimal` в валюте `currency`.
Коннекторы (T062, T063) и записанный источник для тестов (`recorded.py`) реализуют один протокол.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

type Provider = Literal["anthropic", "openai"]
type ImportErrorClass = Literal["auth_rejected", "provider_unavailable", "bad_response", "internal"]

PROVIDERS: tuple[Provider, ...] = ("anthropic", "openai")
ERROR_CLASSES = frozenset({"auth_rejected", "provider_unavailable", "bad_response", "internal"})


@dataclass(frozen=True, slots=True)
class CostLine:
    provider: str
    source_kind: str  # workspace | project | api_key
    source_ref: str
    model: str
    day: date
    amount: Decimal
    currency: str  # RUB | USD


class CostSource(Protocol):
    provider: Provider

    async def fetch(self, day_from: date, day_to: date) -> list[CostLine]:
        """Строки расхода за сутки [day_from, day_to] (UTC, включительно). Ошибка — `ImportFailed`."""
        ...


class ImportFailed(Exception):
    """Загрузка не удалась; `error_class` — значение `ImportErrorClass` контракта (FR-025)."""

    def __init__(self, error_class: str, detail: str = "") -> None:
        if error_class not in ERROR_CLASSES:
            raise ValueError(f"неизвестный класс ошибки импорта: {error_class!r}")
        super().__init__(f"{error_class}: {detail}" if detail else error_class)
        self.error_class = error_class
