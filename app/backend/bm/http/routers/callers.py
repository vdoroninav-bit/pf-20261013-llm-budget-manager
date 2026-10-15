"""Вызывающие системы: `listCallers`, `createCaller`, `updateCaller`, `revokeCaller` (FR-016, T042)."""

from uuid import UUID

from fastapi import APIRouter

from bm.config import callers as service
from bm.http.deps import AdminSession, ClockDep, Conn
from bm.http.schemas_admin import Caller, CallerCreate, CallerCreated, CallerList, CallerPatch, patch_of

router = APIRouter(prefix="/callers", tags=["callers"])


@router.get("", operation_id="listCallers")
async def list_callers(session: AdminSession, conn: Conn) -> CallerList:
    items = await service.list_callers(conn)
    return CallerList.model_validate({"items": items, "total": len(items)})


@router.post("", operation_id="createCaller", status_code=201)
async def create_caller(
    body: CallerCreate, session: AdminSession, conn: Conn, clock: ClockDep
) -> CallerCreated:
    created = await service.create_caller(
        conn,
        session.principal.login,
        name=body.name,
        scope_type=body.subject_scope.type,
        scope_department_id=body.subject_scope.department_id,
        provider_source_id=body.provider_source_id,
        now=clock.now(),
    )
    return CallerCreated.model_validate(created)


@router.patch("/{caller_id}", operation_id="updateCaller")
async def update_caller(
    caller_id: UUID, body: CallerPatch, session: AdminSession, conn: Conn, clock: ClockDep
) -> Caller:
    updated = await service.update_caller(
        conn, session.principal.login, caller_id, patch_of(body), clock.now()
    )
    return Caller.model_validate(updated)


@router.post("/{caller_id}/revoke", operation_id="revokeCaller")
async def revoke_caller(caller_id: UUID, session: AdminSession, conn: Conn, clock: ClockDep) -> Caller:
    return Caller.model_validate(
        await service.revoke_caller(conn, session.principal.login, caller_id, clock.now())
    )
