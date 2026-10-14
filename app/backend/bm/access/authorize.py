"""Проверка прав в сервисном слое: `authorize(principal, action, scope)` (12_plan.md §6).

Отказ — исключение `Forbidden`; запись в аудит (`forbidden_access`) делает вызывающий слой,
потому что ему известны путь и актор, а транзакция запроса откатывается.
"""

from dataclasses import dataclass, field
from uuid import UUID

from bm.access.permissions import PERMISSIONS, Role


class Forbidden(Exception):
    """Роль не позволяет операцию или объект чужого подразделения."""

    def __init__(self, action: str, reason: str) -> None:
        super().__init__(f"{action}: {reason}")
        self.action = action
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Principal:
    """Пользователь админки, от имени которого выполняется операция."""

    user_id: UUID
    login: str
    role: Role
    department_id: UUID | None = None
    must_change_password: bool = False


@dataclass(frozen=True, slots=True)
class Scope:
    """Объект операции.

    - `Scope()` — операция без конкретного объекта (список): подразделение фильтрует сам сервис,
      см. `department_filter`;
    - `Scope(department_id=X)` — объект подразделения X;
    - `Scope(department_id=None, scoped=True)` — объект, не принадлежащий ни одному подразделению.
    """

    department_id: UUID | None = None
    scoped: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.department_id is not None:
            object.__setattr__(self, "scoped", True)


def authorize(principal: Principal, action: str, scope: Scope) -> None:
    roles = PERMISSIONS.get(action)
    if roles is None:
        raise Forbidden(action, "операция не описана в таблице прав")
    if principal.role not in roles:
        raise Forbidden(action, f"роль {principal.role} не допускается")
    if (
        principal.role is Role.BUDGET_OWNER
        and scope.scoped
        and scope.department_id != principal.department_id
    ):
        raise Forbidden(action, "объект чужого подразделения")


def department_filter(principal: Principal) -> UUID | None:
    """Ограничение списков: владелец бюджета видит только своё подразделение; остальные — всё (None)."""
    if principal.role is Role.BUDGET_OWNER:
        return principal.department_id
    return None
