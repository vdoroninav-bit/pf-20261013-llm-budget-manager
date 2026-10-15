"""Pydantic-модели Decision API 1:1 со схемами 12_openapi.yaml (тег caller).

Лежат в ядре (`bm.budget`), а не в `bm.http`, потому что их используют оба адаптера — HTTP и MCP,
а ядро не может импортировать адаптеры (import-linter, контракт adapters-thin).
Во входных моделях `extra="forbid"`: содержимое запросов моделей не принимается (принцип I).
Деньги наружу — строка `Money` ("30.00"), внутри — целые копейки.
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from bm.domain.types import DenyCode, Outcome, PeriodKind


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


type Tokens = Annotated[StrictInt, Field(ge=0)]
type ModelId = Annotated[str, Field(min_length=1, max_length=200)]
type Money = str


# ---------------------------------------------------------------- субъект


class EmployeeSubject(_In):
    type: Literal["employee"]
    login: Annotated[str, Field(min_length=1, max_length=256)]


class ServiceSubject(_In):
    type: Literal["service"]


type SubjectRef = Annotated[EmployeeSubject | ServiceSubject, Field(discriminator="type")]


# ---------------------------------------------------------------- решение


class Needs(_In):
    tools: bool = False
    json_mode: bool = False
    context_tokens: Tokens = 0


class DecisionRequest(_In):
    subject: SubjectRef
    model: ModelId
    kind: Literal["chat", "embedding"]
    input_tokens: Tokens | None = None
    max_output_tokens: Tokens | None = None
    needs: Needs | None = None


class AppliedLimit(BaseModel):
    owner_type: Literal["employee", "department"]
    owner_ref: str
    period: PeriodKind
    limit_rub: Money
    remaining_rub: Money
    period_start: datetime
    resets_at: datetime


class EstimateOut(BaseModel):
    input_tokens: int
    output_tokens: int
    source: Literal["request", "catalog_default"]


class Decision(BaseModel):
    decision_id: UUID
    outcome: Outcome
    code: DenyCode | None
    reason: Literal["budget_exceeded"] | None
    requested_model: str
    model: str | None
    reserve_rub: Money
    expires_at: datetime | None
    http_status_hint: Literal[200, 403, 422, 429]
    policy_mode: Literal["observe", "warn", "enforce"]
    fail_mode: Literal["open", "closed"]
    department_id: UUID | None
    observed_violation: Literal["budget_exceeded", "model_not_allowed"] | None
    applied_limit: AppliedLimit | None
    estimate: EstimateOut | None
    budget_blocked_by_subscriptions: bool
    message: str
    created_at: datetime


# ---------------------------------------------------------------- расход

type EventFlag = Literal[
    "duplicate",
    "no_report",
    "no_decision",
    "decision_not_followed",
    "over_limit_observed",
    "model_not_allowed_observed",
]


class TokenCounts(_In):
    input: Tokens
    output: Tokens
    cached: Tokens = 0


class UsageReport(_In):
    report_id: UUID
    decision_id: UUID | None = None
    subject: SubjectRef | None = None
    model: ModelId | None = None
    actual_model: ModelId | None = None
    occurred_at: datetime | None = None
    tokens: TokenCounts
    call_status: Literal["ok", "aborted", "error"]


class UsageResult(BaseModel):
    report_id: UUID
    decision_id: UUID | None
    status: Literal["charged", "duplicate"]
    amount_rub: Money
    flags: list[EventFlag]
    expense_date: date


class ProblemOut(BaseModel):
    """Схема `Problem` — ошибка строки пакета."""

    type: str = "about:blank"
    title: str
    status: int
    code: str
    detail: str


class BatchRow(BaseModel):
    index: int
    ok: bool
    result: UsageResult | None = None
    error: ProblemOut | None = None


class UsageBatchResult(BaseModel):
    results: list[BatchRow]


# ---------------------------------------------------------------- остатки


class SpentBySource(BaseModel):
    token_rub: Money
    import_rub: Money
    subscriptions_rub: Money


class LimitBalance(BaseModel):
    owner_type: Literal["employee", "department"]
    owner_ref: str
    period: PeriodKind
    period_start: datetime
    resets_at: datetime
    limit_rub: Money
    spent: SpentBySource
    reserved_rub: Money
    remaining_rub: Money
    used_pct: str
    over_limit: bool
    subscriptions_share_pct: str


class BalanceReport(BaseModel):
    as_of: datetime
    owner_type: Literal["employee", "department"]
    owner_ref: str
    department_id: UUID | None
    policy_mode: Literal["observe", "warn", "enforce"]
    limits: list[LimitBalance]


# ---------------------------------------------------------------- конфигурация интеграции


class DepartmentFailMode(BaseModel):
    department_id: UUID
    fail_mode: Literal["open", "closed"]


class IntegrationConfig(BaseModel):
    version: str
    timeout_ms: int
    refresh_s: int
    default_fail_mode: Literal["open", "closed"]
    departments: list[DepartmentFailMode]
