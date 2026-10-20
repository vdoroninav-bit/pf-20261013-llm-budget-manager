"""Pydantic-модели подписок, справочника источников и импорта (T051, T061) 1:1 со схемами 12_openapi.yaml.

Во входных моделях `extra="forbid"` (`additionalProperties: false`): PATCH подписки с денежным полем — 422.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from bm.http.schemas_admin import Currency, Money, PriceDecimal, _In

PeriodKind = Literal["day", "week", "month"]
FxWarning = Literal["last_known_rate_used", "no_rate"]
Provider = Literal["anthropic", "openai"]
ImportErrorClass = Literal["auth_rejected", "provider_unavailable", "bad_response", "internal"]


# ---------------------------------------------------------------- подписки


class SubscriptionAssignee(_In):
    type: Literal["employee", "department", "unallocated"]
    ref: str | None = None


class SubscriptionAssigneeOut(BaseModel):
    type: Literal["employee", "department", "unallocated"]
    ref: str | None


class SubscriptionCreate(_In):
    provider: str = Field(min_length=1, max_length=64)
    plan: str = Field(min_length=1, max_length=128)
    seat_price: PriceDecimal
    currency: Currency
    seats: int = Field(ge=1)
    start_date: date
    end_date: date | None = None
    assignee: SubscriptionAssignee


class SubscriptionPatch(_In):
    end_date: date


class Subscription(BaseModel):
    id: str
    provider: str
    plan: str
    seat_price: str
    currency: Currency
    seats: int
    start_date: date
    end_date: date | None
    assignee: SubscriptionAssigneeOut
    monthly_total_rub: Money | None
    fx_warning: FxWarning | None
    created_at: datetime


class SubscriptionList(BaseModel):
    items: list[Subscription]
    total: int


class PreviewPeriod(BaseModel):
    period: PeriodKind
    limit_rub: Money | None
    amortization_rub: Money
    share_pct: str | None


class SubscriptionPreview(BaseModel):
    department_id: str | None
    periods: list[PreviewPeriod]
    warn_threshold_pct: int
    warn: bool
    fx_warning: FxWarning | None


# ---------------------------------------------------------------- справочник источников


class ProviderSource(BaseModel):
    id: str
    provider: Provider
    kind: Literal["workspace", "project", "api_key"]
    ref: str
    display_name: str | None
    department_id: str | None
    mode: Literal["import", "decision_api"]


class ProviderSourceList(BaseModel):
    items: list[ProviderSource]
    total: int


class ProviderSourceCreate(_In):
    provider: Provider
    kind: Literal["workspace", "project", "api_key"]
    ref: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    department_id: str
    mode: Literal["import", "decision_api"]


class ProviderSourcePatch(_In):
    display_name: str | None = Field(default=None, max_length=200)
    department_id: str = Field(default=None)  # type: ignore[assignment]
    mode: Literal["import", "decision_api"] = Field(default=None)  # type: ignore[assignment]


# ---------------------------------------------------------------- импорт


class ImportRun(BaseModel):
    id: str
    provider: Provider
    status: Literal["queued", "running", "ok", "failed"]
    started_at: datetime | None
    finished_at: datetime | None
    day_from: date
    day_to: date
    lines_charged: int
    lines_reconcile: int
    lines_unallocated: int
    lines_changed: int
    error_class: ImportErrorClass | None


class ImportRunList(BaseModel):
    items: list[ImportRun]
    total: int


class ImportRunRequest(_In):
    provider: Provider
    day_from: date | None = None
    day_to: date | None = None


class ProviderImportStatus(BaseModel):
    provider: Provider
    configured: bool
    last_success_day: date | None
    last_run_at: datetime | None
    lag_hours: int | None
    consecutive_failures: int
    last_error_class: ImportErrorClass | None
    unallocated_sources: list[str]


class ImportStatus(BaseModel):
    enabled: bool
    providers: list[ProviderImportStatus]
