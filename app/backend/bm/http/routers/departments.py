"""Подразделения: `listDepartments`, `createDepartment`, `getDepartment`, `updateDepartment`,
`putDepartmentLimits` (FR-013, T042); остаток подразделения `getDepartmentBalance` и его прослеживаемость
`listDepartmentBalanceItems` (FR-026, FR-027, T071).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request

from bm.access.authorize import Scope, department_filter
from bm.access.permissions import Role
from bm.budget.errors import BudgetError
from bm.budget.schemas import BalanceReport
from bm.config import departments as service
from bm.config.errors import Invalid
from bm.domain.periods import MSK
from bm.domain.types import PeriodKind
from bm.http.deps import AdminSession, BudgetDep, ClockDep, Conn, authorize_object
from bm.http.errors import not_found
from bm.http.routers._common import Order, Page, PageSize, Sort
from bm.http.schemas_admin import (
    Department,
    DepartmentCreate,
    DepartmentList,
    DepartmentPatch,
    LimitSet,
    LimitSetOut,
    patch_of,
)
from bm.http.schemas_reports import BalanceItem, BalanceItemList, SpendSource
from bm.reports import service as reports

router = APIRouter(prefix="/departments", tags=["departments"])


def limits_kop(body: LimitSet) -> dict[str, int | None]:
    try:
        return body.kop()
    except ValueError as exc:
        raise Invalid("Лимит не может быть отрицательным.") from exc


@router.get("", operation_id="listDepartments")
async def list_departments(
    session: AdminSession,
    conn: Conn,
    page: Page = 1,
    page_size: PageSize = 50,
    sort: Sort = None,
    order: Order = "asc",
    q: str | None = None,
) -> DepartmentList:
    items, total = await service.list_departments(
        conn,
        only=department_filter(session.principal),
        q=q,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
    )
    return DepartmentList.model_validate({"items": items, "total": total})


@router.post("", operation_id="createDepartment", status_code=201)
async def create_department(body: DepartmentCreate, session: AdminSession, conn: Conn) -> Department:
    dep = await service.create_department(conn, session.principal.login, body.name, body.ad_group)
    return Department.model_validate(dep)


@router.get("/{department_id}", operation_id="getDepartment")
async def get_department(
    department_id: UUID, request: Request, session: AdminSession, conn: Conn
) -> Department:
    dep: dict[str, Any] = await service.get_department(conn, department_id)
    await authorize_object(conn, session, request, Scope(department_id=department_id))
    return Department.model_validate(dep)


@router.patch("/{department_id}", operation_id="updateDepartment")
async def update_department(
    department_id: UUID, body: DepartmentPatch, session: AdminSession, conn: Conn
) -> Department:
    dep = await service.update_department(conn, session.principal.login, department_id, patch_of(body))
    return Department.model_validate(dep)


@router.put("/{department_id}/limits", operation_id="putDepartmentLimits")
async def put_department_limits(
    department_id: UUID, body: LimitSet, session: AdminSession, conn: Conn
) -> LimitSetOut:
    saved = await service.put_department_limits(
        conn, session.principal.login, department_id, limits_kop(body)
    )
    return LimitSetOut.model_validate(saved)


def _aware(at: datetime | None) -> datetime | None:
    """Момент без часового пояса понимается как время МСК (принцип IX)."""
    if at is not None and (at.tzinfo is None or at.utcoffset() is None):
        return at.replace(tzinfo=MSK)
    return at


@router.get("/{department_id}/balance", operation_id="getDepartmentBalance")
async def get_department_balance(
    department_id: UUID,
    request: Request,
    session: AdminSession,
    conn: Conn,
    budget: BudgetDep,
    at: datetime | None = None,
) -> BalanceReport:
    await service.get_department(conn, department_id)  # 404, если подразделения нет
    await authorize_object(conn, session, request, Scope(department_id=department_id))
    try:
        return await reports.department_balance(budget, department_id, _aware(at))
    except BudgetError as exc:
        raise not_found("Подразделение не найдено.") from exc


@router.get("/{department_id}/balance/items", operation_id="listDepartmentBalanceItems")
async def list_department_balance_items(
    department_id: UUID,
    request: Request,
    session: AdminSession,
    conn: Conn,
    clock: ClockDep,
    period: PeriodKind,
    at: datetime | None = None,
    source: SpendSource | None = None,
    page: Page = 1,
    page_size: PageSize = 50,
) -> BalanceItemList:
    await authorize_object(conn, session, request, Scope(department_id=department_id))
    now = clock.now()
    items, total = await reports.balance_items(
        conn,
        department_id,
        period,
        _aware(at) or now,
        now,
        source=source,
        show_subjects=session.principal.role is not Role.FINANCE_VIEWER,  # финансист сотрудников не видит
        page=page,
        page_size=page_size,
    )
    return BalanceItemList(items=[BalanceItem.model_validate(x) for x in items], total=total)
