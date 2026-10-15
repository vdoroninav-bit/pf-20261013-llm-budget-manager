"""Pydantic-модели админ-CRUD (T041, T042) 1:1 со схемами 12_openapi.yaml.

Во входных моделях `extra="forbid"` (принцип I, `additionalProperties: false`). PATCH-модели различают
«поле не передано» и «передано null» через `model_fields_set` (`patch_of`).
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bm.domain.money import str_to_kop
from bm.http.schemas import AdminUser

Money = Annotated[str, Field(pattern=r"^-?[0-9]+\.[0-9]{2}$")]
PriceDecimal = Annotated[str, Field(pattern=r"^[0-9]+(\.[0-9]{1,6})?$")]
RateDecimal = Annotated[str, Field(pattern=r"^[0-9]+(\.[0-9]{1,4})?$")]
Pseudonym = Annotated[str, Field(pattern=r"^emp-[0-9a-f]{10}$")]
PolicyMode = Literal["observe", "warn", "enforce"]
FailMode = Literal["open", "closed"]
Role = Literal["admin", "budget_owner", "finance_viewer"]
Currency = Literal["RUB", "USD"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


def patch_of(model: BaseModel) -> dict[str, Any]:
    """Только переданные поля (включая явный null)."""
    return model.model_dump(exclude_unset=True)


# ---------------------------------------------------------------- общие


class LimitSet(_In):
    day: Money | None
    week: Money | None
    month: Money | None

    def kop(self) -> dict[str, int | None]:
        """Копейки по периодам; отрицательная сумма — ValueError (вызывающий отдаёт 422)."""
        out: dict[str, int | None] = {}
        for period in ("day", "week", "month"):
            v = getattr(self, period)
            out[period] = None if v is None else str_to_kop(v)
            if (out[period] or 0) < 0:
                raise ValueError(period)
        return out


class LimitSetOut(BaseModel):
    day: str | None
    week: str | None
    month: str | None


# ---------------------------------------------------------------- org


class RowMessage(BaseModel):
    line: int
    message: str


class OrgImportSummary(BaseModel):
    dry_run: bool
    departments_added: int
    employees_added: int
    employees_updated: int
    employees_moved: int
    employees_removed: int
    rows_total: int
    rows_applied: int
    errors: list[RowMessage]
    warnings: list[RowMessage]


# ---------------------------------------------------------------- departments


class Department(BaseModel):
    id: UUID
    name: str
    ad_group: str | None
    policy_mode: PolicyMode
    fail_mode: FailMode
    allowed_models: list[str]
    subs_warn_pct: int
    limits: LimitSetOut
    employees_count: int


class DepartmentList(BaseModel):
    items: list[Department]
    total: int


class DepartmentCreate(_In):
    name: str = Field(min_length=1, max_length=200)
    ad_group: str | None = None


class DepartmentPatch(_In):
    name: str = Field(default=None, min_length=1, max_length=200)  # type: ignore[assignment]
    policy_mode: PolicyMode = None  # type: ignore[assignment]
    fail_mode: FailMode = None  # type: ignore[assignment]
    allowed_models: list[str] = None  # type: ignore[assignment]
    subs_warn_pct: int = Field(default=None, ge=1, le=100)  # type: ignore[assignment]


# ---------------------------------------------------------------- employees


class Employee(BaseModel):
    pseudonym: str
    department_id: UUID | None
    active: bool
    limits: LimitSetOut


class EmployeeList(BaseModel):
    items: list[Employee]
    total: int


class EmployeeIdentity(BaseModel):
    pseudonym: str
    login: str
    display_name: str | None


# ---------------------------------------------------------------- models


class ModelPrice(BaseModel):
    id: UUID
    model_id: str
    valid_from: datetime
    input_per_1k: str
    output_per_1k: str
    cached_input_per_1k: str | None
    currency: Currency


class ModelPriceList(BaseModel):
    items: list[ModelPrice]
    total: int


class ModelPriceCreate(_In):
    valid_from: datetime | None = None
    input_per_1k: PriceDecimal
    output_per_1k: PriceDecimal
    cached_input_per_1k: PriceDecimal | None = None
    currency: Currency


class Model(BaseModel):
    id: str
    provider: str
    kind: Literal["chat", "embedding"]
    supports_tools: bool
    supports_json: bool
    max_context: int
    default_input_tokens: int
    default_output_tokens: int
    tie_order: int
    active: bool
    current_price: ModelPrice


class ModelList(BaseModel):
    items: list[Model]
    total: int


class ModelCreate(_In):
    id: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=64)
    kind: Literal["chat", "embedding"]
    supports_tools: bool
    supports_json: bool
    max_context: int = Field(ge=1)
    default_input_tokens: int = Field(ge=0)
    default_output_tokens: int = Field(ge=0)
    tie_order: int = 100
    price: ModelPriceCreate


class ModelPatch(_In):
    supports_tools: bool = None  # type: ignore[assignment]
    supports_json: bool = None  # type: ignore[assignment]
    max_context: int = Field(default=None, ge=1)  # type: ignore[assignment]
    default_input_tokens: int = Field(default=None, ge=0)  # type: ignore[assignment]
    default_output_tokens: int = Field(default=None, ge=0)  # type: ignore[assignment]
    tie_order: int = None  # type: ignore[assignment]
    active: bool = None  # type: ignore[assignment]


# ---------------------------------------------------------------- fx


class FxRate(BaseModel):
    month: str
    usd_rub: str
    entered_by: str
    entered_at: datetime


class FxRateList(BaseModel):
    items: list[FxRate]
    total: int


class FxRatePut(_In):
    usd_rub: RateDecimal


# ---------------------------------------------------------------- callers


class SubjectScope(_In):
    type: Literal["service", "department_employees", "any_employee"]
    department_id: UUID | None = None


class SubjectScopeOut(BaseModel):
    type: Literal["service", "department_employees", "any_employee"]
    department_id: UUID | None


class Caller(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    subject_scope: SubjectScopeOut
    provider_source_id: UUID | None
    status: Literal["active", "revoked"]
    created_at: datetime
    revoked_at: datetime | None


class CallerCreated(Caller):
    api_key: str


class CallerList(BaseModel):
    items: list[Caller]
    total: int


class CallerCreate(_In):
    name: str = Field(min_length=1, max_length=200)
    subject_scope: SubjectScope
    provider_source_id: UUID | None = None


class CallerPatch(_In):
    name: str = Field(default=None, min_length=1, max_length=200)  # type: ignore[assignment]
    provider_source_id: UUID | None = None


# ---------------------------------------------------------------- admin users


class AdminUserList(BaseModel):
    items: list[AdminUser]
    total: int


class AdminUserCreate(_In):
    login: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12)
    role: Role
    department_id: UUID | None = None


class AdminUserPatch(_In):
    role: Role = None  # type: ignore[assignment]
    department_id: UUID | None = None
    active: bool = None  # type: ignore[assignment]
    reset_password: str = Field(default=None, min_length=12)  # type: ignore[assignment]
