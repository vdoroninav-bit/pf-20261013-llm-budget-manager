"""Сотрудники под псевдонимами: `listEmployees`, `getEmployee`, `putEmployeeLimits`, `revealPseudonym`
(FR-013, FR-035, T042). Владелец бюджета видит только сотрудников своего подразделения.
"""

from uuid import UUID

from fastapi import APIRouter, Path, Request

from bm.access.authorize import Scope, department_filter
from bm.config import employees as service
from bm.http.deps import AdminSession, ClockDep, Conn, authorize_object
from bm.http.routers._common import Page, PageSize
from bm.http.routers.departments import limits_kop
from bm.http.schemas_admin import Employee, EmployeeIdentity, EmployeeList, LimitSet, LimitSetOut

router = APIRouter(tags=["employees"])

PseudonymPath = Path(pattern=r"^emp-[0-9a-f]{10}$")


@router.get("/employees", operation_id="listEmployees")
async def list_employees(
    request: Request,
    session: AdminSession,
    conn: Conn,
    department_id: UUID | None = None,
    page: Page = 1,
    page_size: PageSize = 50,
) -> EmployeeList:
    own = department_filter(session.principal)
    if own is not None:  # владелец бюджета: только своё подразделение
        if department_id is not None:
            await authorize_object(conn, session, request, Scope(department_id=department_id))
        department_id = own
    items, total = await service.list_employees(
        conn, department_id=department_id, page=page, page_size=page_size
    )
    return EmployeeList.model_validate({"items": items, "total": total})


@router.get("/employees/{pseudonym}", operation_id="getEmployee")
async def get_employee(
    request: Request, session: AdminSession, conn: Conn, pseudonym: str = PseudonymPath
) -> Employee:
    emp = await service.get_employee(conn, pseudonym)
    dep = emp["department_id"]
    await authorize_object(
        conn, session, request, Scope(department_id=None if dep is None else UUID(dep), scoped=True)
    )
    return Employee.model_validate(emp)


@router.put("/employees/{pseudonym}/limits", operation_id="putEmployeeLimits")
async def put_employee_limits(
    body: LimitSet, session: AdminSession, conn: Conn, clock: ClockDep, pseudonym: str = PseudonymPath
) -> LimitSetOut:
    saved = await service.put_employee_limits(
        conn, session.principal.login, pseudonym, limits_kop(body), clock.now()
    )
    return LimitSetOut.model_validate(saved)


@router.get("/pseudonyms/{pseudonym}", operation_id="revealPseudonym")
async def reveal_pseudonym(
    session: AdminSession, conn: Conn, clock: ClockDep, pseudonym: str = PseudonymPath
) -> EmployeeIdentity:
    return EmployeeIdentity.model_validate(
        await service.reveal(conn, session.principal.login, pseudonym, clock.now())
    )
