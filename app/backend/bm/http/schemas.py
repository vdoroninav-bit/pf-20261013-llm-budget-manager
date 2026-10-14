"""Pydantic-модели 1:1 со схемами 12_openapi.yaml. Во входных моделях `extra="forbid"` (принцип I).

Здесь — схемы админки (auth, notifications); схемы вызывающих систем добавляет T021.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bm.access.authorize import Principal


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- auth


class LoginRequest(_In):
    login: str = Field(min_length=1)
    password: str = Field(min_length=1)


class PasswordChange(_In):
    old_password: str
    new_password: str = Field(min_length=12)


class AdminUser(BaseModel):
    id: UUID
    login: str
    role: Literal["admin", "budget_owner", "finance_viewer"]
    department_id: UUID | None = None
    active: bool
    must_change_password: bool

    @classmethod
    def of(cls, p: Principal, *, active: bool = True) -> "AdminUser":
        return cls(
            id=p.user_id,
            login=p.login,
            role=p.role.value,
            department_id=p.department_id,
            active=active,
            must_change_password=p.must_change_password,
        )


class SessionOut(BaseModel):
    """Схема `Session` контракта."""

    user: AdminUser
    csrf_token: str


# ---------------------------------------------------------------- notifications

NotificationKind = Literal[
    "threshold_80",
    "threshold_100",
    "subs_share",
    "import_failure",
    "import_unallocated",
    "fx_missing",
    "reservation_no_report",
]


class NotificationOut(BaseModel):
    id: UUID
    kind: NotificationKind
    department_id: UUID | None
    period: Literal["day", "week", "month"] | None
    period_start: date | None
    message: str
    created_at: datetime
    read: bool
    webhook_status: Literal["disabled", "pending", "delivered", "failed"]


class NotificationList(BaseModel):
    items: list[NotificationOut]
    total: int
