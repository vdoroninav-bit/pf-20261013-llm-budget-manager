"""Pydantic-модели отчётов и журналов (T071) 1:1 со схемами 12_openapi.yaml: SpendRow, ReconciliationRow,
BalanceItem, CallEvent, AuditEntry."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from bm.budget.schemas import EventFlag

SpendSource = Literal["token", "import", "subscription", "reservation"]


class SpendRow(BaseModel):
    period_start: date | None
    department_id: str | None
    department_name: str | None
    model_id: str | None
    source: SpendSource | None
    amount_rub: str
    original_currency: Literal["RUB", "USD"] | None
    original_amount: str | None
    late: bool


class SpendReport(BaseModel):
    items: list[SpendRow]
    total_rub: str


class ReconciliationRow(BaseModel):
    month: str
    provider: Literal["anthropic", "openai"]
    source_ref: str
    department_id: UUID | None
    imported_rub: str
    decision_api_rub: str
    diff_rub: str
    diff_pct: str | None
    coverage_pct: str | None


class ReconciliationReport(BaseModel):
    items: list[ReconciliationRow]


class BalanceItem(BaseModel):
    source: SpendSource
    ref_id: str
    date: date
    model_id: str | None
    subject_pseudonym: str | None
    amount_rub: str
    flags: list[EventFlag]


class BalanceItemList(BaseModel):
    items: list[BalanceItem]
    total: int


class EventTokens(BaseModel):
    input: int
    output: int
    cached: int


class CallEvent(BaseModel):
    id: UUID
    created_at: datetime
    caller_name: str
    subject_pseudonym: str | None
    department_id: UUID | None
    requested_model: str
    final_model: str | None
    outcome: Literal["allow", "redirect", "deny", "none"]
    code: Literal["model_not_allowed", "budget_exceeded", "model_unknown", "subject_unknown"] | None
    reserve_rub: str
    fact_rub: str | None
    tokens: EventTokens | None
    status: Literal["open", "settled", "expired", "closed"]
    flags: list[EventFlag]
    expense_date: date


class CallEventList(BaseModel):
    items: list[CallEvent]
    total: int


class AuditEntry(BaseModel):
    id: int
    at: datetime
    actor: str
    action: str
    object_type: str
    object_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class AuditList(BaseModel):
    items: list[AuditEntry]
    total: int
