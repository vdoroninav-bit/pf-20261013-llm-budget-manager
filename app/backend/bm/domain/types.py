"""Значения, общие для всех модулей (12_plan.md §6). Только данные, без ввода-вывода.

Деньги — `Kop` (целые копейки); цены и курсы — `Decimal`. `float` не используется (принцип VI).
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

type Kop = int
"""Сумма в копейках."""


class PeriodKind(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class Outcome(StrEnum):
    ALLOW = "allow"
    REDIRECT = "redirect"
    DENY = "deny"


class DenyCode(StrEnum):
    MODEL_NOT_ALLOWED = "model_not_allowed"
    BUDGET_EXCEEDED = "budget_exceeded"
    MODEL_UNKNOWN = "model_unknown"
    SUBJECT_UNKNOWN = "subject_unknown"


class PolicyMode(StrEnum):
    """Режим политики подразделения (FR-005): наблюдать / предупреждать / ограничивать."""

    OBSERVE = "observe"
    WARN = "warn"
    ENFORCE = "enforce"


type Currency = Literal["RUB", "USD"]
type OwnerType = Literal["employee", "department"]
type Violation = Literal["budget_exceeded", "model_not_allowed"]
type EstimateSource = Literal["request", "catalog_default"]


@dataclass(frozen=True, slots=True)
class Period:
    """Календарный период по МСК. `end` включительно; `resets_at` = (end + 1) 00:00 МСК."""

    kind: PeriodKind
    start: date
    end: date
    resets_at: datetime

    @property
    def starts_at(self) -> datetime:
        """Начало периода: `start` 00:00 в часовом поясе `resets_at` (МСК)."""
        return datetime.combine(self.start, time(0), tzinfo=self.resets_at.tzinfo)


@dataclass(frozen=True, slots=True)
class Price:
    """Цена модели за 1 000 токенов в валюте модели. `cached_per_1k=None` — кэш по цене входа."""

    id: UUID
    in_per_1k: Decimal
    out_per_1k: Decimal
    cached_per_1k: Decimal | None
    currency: Currency


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    kind: str  # "chat" | "embedding"
    supports_tools: bool
    supports_json: bool
    max_context: int
    default_in: int
    default_out: int
    tie_order: int
    active: bool
    price: Price


@dataclass(frozen=True, slots=True)
class LimitState:
    """Состояние одного лимита владельца за период.

    `owner_ref` — псевдоним сотрудника или id подразделения.
    """

    owner_type: OwnerType
    owner_ref: str
    period: Period
    limit: Kop
    spent_token: Kop
    spent_import: Kop
    amort: Kop
    reserved: Kop

    @property
    def remaining(self) -> Kop:
        """FR-026: лимит − факт токенов − импорт − амортизация подписок − открытые резервы."""
        return self.limit - self.spent_token - self.spent_import - self.amort - self.reserved


@dataclass(frozen=True, slots=True)
class DecisionInput:
    """Параметры запроса решения, нужные select() (DecisionRequest без субъекта)."""

    kind: str  # "chat" | "embedding"
    input_tokens: int | None = None
    max_output_tokens: int | None = None
    tools: bool = False
    json_mode: bool = False
    context_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Estimate:
    input_tokens: int
    output_tokens: int
    source: EstimateSource


@dataclass(frozen=True, slots=True)
class Verdict:
    """Результат select(). `reserve` > 0 только при allow/redirect; `code` только при deny."""

    outcome: Outcome
    model: str | None
    reserve: Kop
    code: DenyCode | None
    reason: Literal["budget_exceeded"] | None
    observed_violation: Violation | None
    applied: LimitState | None
    blocked_by_subs: bool
    estimate: Estimate | None
