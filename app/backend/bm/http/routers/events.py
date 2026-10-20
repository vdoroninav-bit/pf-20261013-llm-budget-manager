"""Журнал решений и расхода: `listEvents` (метаданные и псевдонимы, без содержимого запросов; T071).

Владелец бюджета видит события только своего подразделения; запрос чужого — 403 и аудит (FR-030, FR-031).
"""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request

from bm.access.authorize import Scope, department_filter
from bm.budget.schemas import EventFlag
from bm.domain.types import DenyCode
from bm.http.deps import AdminSession, Conn, authorize_object
from bm.http.errors import ProblemError
from bm.http.routers._common import Page, PageSize
from bm.http.schemas_reports import CallEvent, CallEventList
from bm.reports import service

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", operation_id="listEvents")
async def list_events(
    request: Request,
    session: AdminSession,
    conn: Conn,
    lo: Annotated[date, Query(alias="from")],
    hi: Annotated[date, Query(alias="to")],
    department_id: UUID | None = None,
    outcome: Literal["allow", "redirect", "deny"] | None = None,
    code: DenyCode | None = None,
    flag: EventFlag | None = None,
    page: Page = 1,
    page_size: PageSize = 50,
) -> CallEventList:
    if hi < lo:
        raise ProblemError(422, "validation_error", "Некорректный период: дата «по» раньше даты «с».")
    if department_id is None:
        department_id = department_filter(session.principal)
    else:
        await authorize_object(conn, session, request, Scope(department_id=department_id))
    query = service.EventQuery(lo, hi, department_id, outcome, code.value if code is not None else None, flag)
    items, total = await service.list_events(conn, query, page=page, page_size=page_size)
    return CallEventList(items=[CallEvent.model_validate(x) for x in items], total=total)
