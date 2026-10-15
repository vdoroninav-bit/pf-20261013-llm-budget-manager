"""Каталог моделей: `listModels`, `createModel`, `getModel`, `updateModel`, `listModelPrices`,
`createModelPrice` (FR-015, T042). Цены меняются только новой строкой истории (`/prices`).
"""

from fastapi import APIRouter

from bm.config import catalog as service
from bm.http.deps import AdminSession, ClockDep, Conn
from bm.http.routers._common import Order, Page, PageSize, Sort
from bm.http.schemas_admin import (
    Model,
    ModelCreate,
    ModelList,
    ModelPatch,
    ModelPrice,
    ModelPriceCreate,
    ModelPriceList,
    patch_of,
)

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", operation_id="listModels")
async def list_models(
    session: AdminSession,
    conn: Conn,
    clock: ClockDep,
    page: Page = 1,
    page_size: PageSize = 50,
    sort: Sort = None,
    order: Order = "asc",
) -> ModelList:
    items, total = await service.list_models(
        conn, now=clock.now(), page=page, page_size=page_size, sort=sort, order=order
    )
    return ModelList.model_validate({"items": items, "total": total})


@router.post("", operation_id="createModel", status_code=201)
async def create_model(body: ModelCreate, session: AdminSession, conn: Conn, clock: ClockDep) -> Model:
    created = await service.create_model(conn, session.principal.login, body.model_dump(), clock.now())
    return Model.model_validate(created)


@router.get("/{model_id}", operation_id="getModel")
async def get_model(model_id: str, session: AdminSession, conn: Conn, clock: ClockDep) -> Model:
    return Model.model_validate(await service.get_model(conn, model_id, clock.now()))


@router.patch("/{model_id}", operation_id="updateModel")
async def update_model(
    model_id: str, body: ModelPatch, session: AdminSession, conn: Conn, clock: ClockDep
) -> Model:
    updated = await service.update_model(conn, session.principal.login, model_id, patch_of(body), clock.now())
    return Model.model_validate(updated)


@router.get("/{model_id}/prices", operation_id="listModelPrices")
async def list_model_prices(model_id: str, session: AdminSession, conn: Conn) -> ModelPriceList:
    items = await service.list_prices(conn, model_id)
    return ModelPriceList.model_validate({"items": items, "total": len(items)})


@router.post("/{model_id}/prices", operation_id="createModelPrice", status_code=201)
async def create_model_price(
    model_id: str, body: ModelPriceCreate, session: AdminSession, conn: Conn, clock: ClockDep
) -> ModelPrice:
    created = await service.create_price(
        conn, session.principal.login, model_id, body.model_dump(), clock.now()
    )
    return ModelPrice.model_validate(created)
