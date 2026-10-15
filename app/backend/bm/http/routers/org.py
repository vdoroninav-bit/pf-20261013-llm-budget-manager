"""Загрузка выгрузки групп AD: `importOrg` (FR-012, T041)."""

from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from bm.http.deps import AdminSession, ClockDep, Conn
from bm.http.schemas_admin import OrgImportSummary
from bm.org_import import service

router = APIRouter(prefix="/org", tags=["org"])


@router.post("/import", operation_id="importOrg")
async def import_org(
    session: AdminSession,
    conn: Conn,
    clock: ClockDep,
    file: Annotated[UploadFile, File()],
    dry_run: Annotated[bool, Form()] = False,
) -> OrgImportSummary:
    # Читаем не больше лимита + 1 байт: больший файл целиком в память не попадает (13_design_rulings.md В6).
    data = await file.read(service.MAX_FILE_BYTES + 1)
    summary = await service.import_ad_csv(conn, data, dry_run, session.principal.login, now=clock.now())
    return OrgImportSummary.model_validate(summary, from_attributes=True)
