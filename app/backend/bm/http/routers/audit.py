"""Журнал аудита: `listAudit` (FR-031; T071). Только администратор (`PERMISSIONS`)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from bm.http.deps import AdminSession, Conn
from bm.http.errors import ProblemError
from bm.http.routers._common import Page, PageSize
from bm.http.schemas_reports import AuditEntry, AuditList
from bm.reports import service

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", operation_id="listAudit")
async def list_audit(
    session: AdminSession,
    conn: Conn,
    lo: Annotated[date, Query(alias="from")],
    hi: Annotated[date, Query(alias="to")],
    object_type: str | None = None,
    page: Page = 1,
    page_size: PageSize = 50,
) -> AuditList:
    if hi < lo:
        raise ProblemError(422, "validation_error", "Некорректный период: дата «по» раньше даты «с».")
    items, total = await service.list_audit(
        conn, lo, hi, object_type=object_type, page=page, page_size=page_size
    )
    return AuditList(items=[AuditEntry.model_validate(x) for x in items], total=total)
