"""Отчёты: `getSpendReport`, `getSpendCsv`, `getReconciliation` (FR-027, FR-028, SC-005; T071).

Владелец бюджета видит только своё подразделение: без `department_id` отчёт ограничивается им, чужое
подразделение или «не распределено» — 403 и запись `forbidden_access` в аудит (FR-030, FR-031).
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from bm.access.auth import Session
from bm.access.authorize import Scope, department_filter
from bm.domain.money import kop_to_str
from bm.http.deps import AdminSession, ClockDep, Conn, authorize_object
from bm.http.errors import ProblemError
from bm.http.schemas_reports import ReconciliationReport, SpendReport, SpendRow
from bm.reports import service
from bm.reports.service import DeptFilter

router = APIRouter(prefix="/reports", tags=["reports"])

From = Annotated[date, Query(alias="from")]
To = Annotated[date, Query(alias="to")]
MONTH_PATTERN = r"^[0-9]{4}-(0[1-9]|1[0-2])$"


def _invalid(detail: str) -> ProblemError:
    return ProblemError(422, "validation_error", detail)


def _check_range(lo: date, hi: date) -> None:
    if hi < lo:
        raise _invalid("Некорректный период: дата «по» раньше даты «с».")


async def dept_scope(raw: str | None, request: Request, session: Session, conn: Conn) -> DeptFilter:
    """`department_id` запроса → фильтр отчёта с проверкой прав на объект."""
    if raw is None or raw == "":
        own = department_filter(session.principal)
        return DeptFilter("one", own) if own is not None else DeptFilter()
    if raw == "unallocated":
        await authorize_object(conn, session, request, Scope(department_id=None, scoped=True))
        return DeptFilter("unallocated")
    try:
        department_id = UUID(raw)
    except ValueError as exc:
        raise _invalid("department_id — ожидается UUID подразделения или «unallocated».") from exc
    await authorize_object(conn, session, request, Scope(department_id=department_id))
    return DeptFilter("one", department_id)


@router.get("/spend", operation_id="getSpendReport")
async def get_spend_report(
    request: Request,
    session: AdminSession,
    conn: Conn,
    clock: ClockDep,
    lo: From,
    hi: To,
    department_id: str | None = None,
    group_by: Annotated[list[str] | None, Query()] = None,
) -> SpendReport:
    _check_range(lo, hi)
    try:
        keys = service.parse_group_by(group_by)
    except ValueError as exc:
        raise _invalid(
            f"group_by: неизвестный ключ «{exc}» (допустимы {', '.join(service.GROUP_KEYS)})."
        ) from exc
    dept = await dept_scope(department_id, request, session, conn)
    rows, total = await service.spend_report(conn, lo, hi, dept, keys, clock.now())
    return SpendReport(items=[SpendRow.model_validate(r) for r in rows], total_rub=kop_to_str(total))


@router.get("/spend.csv", operation_id="getSpendCsv", response_class=Response)
async def get_spend_csv(
    request: Request,
    session: AdminSession,
    conn: Conn,
    clock: ClockDep,
    lo: From,
    hi: To,
    department_id: str | None = None,
) -> Response:
    _check_range(lo, hi)
    dept = await dept_scope(department_id, request, session, conn)
    body = await service.spend_csv(conn, lo, hi, dept, clock.now())
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="spend_{lo}_{hi}.csv"'},
    )


@router.get("/reconciliation", operation_id="getReconciliation")
async def get_reconciliation(
    session: AdminSession,
    conn: Conn,
    month: Annotated[str, Query(pattern=MONTH_PATTERN)],
) -> ReconciliationReport:
    year, mon = month.split("-")
    rows = await service.reconciliation(conn, date(int(year), int(mon), 1))
    return ReconciliationReport.model_validate({"items": rows})
