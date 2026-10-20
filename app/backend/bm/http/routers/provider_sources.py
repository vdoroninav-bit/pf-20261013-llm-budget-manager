"""Справочник источников провайдеров: `listProviderSources`, `createProviderSource`,
`updateProviderSource`, `deleteProviderSource` (FR-022, FR-023, T061). Только администратор.
"""

from uuid import UUID

from fastapi import APIRouter, Response

from bm.http.deps import AdminSession, Conn
from bm.http.schemas_admin import patch_of
from bm.http.schemas_subs import ProviderSource, ProviderSourceCreate, ProviderSourceList, ProviderSourcePatch
from bm.provider_import import catalog as service

router = APIRouter(prefix="/provider-sources", tags=["provider-sources"])


@router.get("", operation_id="listProviderSources")
async def list_provider_sources(session: AdminSession, conn: Conn) -> ProviderSourceList:
    items = await service.list_sources(conn)
    return ProviderSourceList.model_validate({"items": items, "total": len(items)})


@router.post("", operation_id="createProviderSource", status_code=201)
async def create_provider_source(
    body: ProviderSourceCreate, session: AdminSession, conn: Conn
) -> ProviderSource:
    created = await service.create(conn, session.principal.login, body.model_dump())
    return ProviderSource.model_validate(created)


@router.patch("/{source_id}", operation_id="updateProviderSource")
async def update_provider_source(
    source_id: UUID, body: ProviderSourcePatch, session: AdminSession, conn: Conn
) -> ProviderSource:
    updated = await service.update(conn, session.principal.login, source_id, patch_of(body))
    return ProviderSource.model_validate(updated)


@router.delete("/{source_id}", operation_id="deleteProviderSource", status_code=204)
async def delete_provider_source(source_id: UUID, session: AdminSession, conn: Conn) -> Response:
    await service.delete(conn, session.principal.login, source_id)
    return Response(status_code=204)
