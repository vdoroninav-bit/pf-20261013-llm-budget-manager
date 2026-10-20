"""Импорт расхода: `getImportStatus`, `listImportRuns`, `runImport` (FR-025, T061).

Ручной запуск ставится в очередь (202) — загрузку выполняет worker. Импорт выключен — 409 `import_disabled`,
ключ провайдера не задан — 409 `provider_key_missing`. Владелец бюджета видит сокращённый статус.
"""

from fastapi import APIRouter

from bm.access.permissions import Role
from bm.http.deps import AdminSession, ClockDep, Conn, SettingsDep
from bm.http.errors import ProblemError
from bm.http.routers._common import Page, PageSize
from bm.http.schemas_subs import ImportRun, ImportRunList, ImportRunRequest, ImportStatus, Provider
from bm.provider_import import runs as service

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("/status", operation_id="getImportStatus")
async def get_import_status(
    session: AdminSession, conn: Conn, clock: ClockDep, settings: SettingsDep
) -> ImportStatus:
    full = session.principal.role is not Role.BUDGET_OWNER
    return ImportStatus.model_validate(await service.status(conn, settings, clock.now(), full=full))


@router.get("/runs", operation_id="listImportRuns")
async def list_import_runs(
    session: AdminSession,
    conn: Conn,
    provider: Provider | None = None,
    page: Page = 1,
    page_size: PageSize = 50,
) -> ImportRunList:
    items, total = await service.list_runs(conn, provider=provider, page=page, page_size=page_size)
    return ImportRunList.model_validate({"items": items, "total": total})


@router.post("/run", operation_id="runImport", status_code=202)
async def run_import(
    body: ImportRunRequest, session: AdminSession, conn: Conn, clock: ClockDep, settings: SettingsDep
) -> ImportRun:
    try:
        queued = await service.queue(conn, settings, body.provider, body.day_from, body.day_to, clock.now())
    except service.ImportUnavailable as exc:
        raise ProblemError(409, exc.code, exc.detail) from exc
    except ValueError as exc:
        raise ProblemError(422, "validation_error", "Начало окна загрузки позже его конца.") from exc
    return ImportRun.model_validate(queued)
