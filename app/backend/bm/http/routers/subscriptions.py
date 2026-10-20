"""Подписки: `listSubscriptions`, `createSubscription`, `previewSubscription`, `getSubscription`,
`endSubscription`, `deleteSubscription` (FR-017–FR-020, T051). Владелец бюджета видит подписки своего
подразделения и его сотрудников; чужое подразделение — 403 и запись в аудит.
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Request, Response

from bm.access.authorize import Scope, department_filter
from bm.config import subscriptions as service
from bm.http.deps import AdminSession, ClockDep, Conn, SettingsDep, authorize_object
from bm.http.routers._common import Page, PageSize
from bm.http.schemas_subs import (
    Subscription,
    SubscriptionCreate,
    SubscriptionList,
    SubscriptionPatch,
    SubscriptionPreview,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get("", operation_id="listSubscriptions")
async def list_subscriptions(
    request: Request,
    session: AdminSession,
    conn: Conn,
    clock: ClockDep,
    department_id: UUID | None = None,
    active_on: date | None = None,
    page: Page = 1,
    page_size: PageSize = 50,
) -> SubscriptionList:
    own = department_filter(session.principal)
    if own is not None:  # владелец бюджета: только своё подразделение
        if department_id is not None:
            await authorize_object(conn, session, request, Scope(department_id=department_id))
        department_id = own
    items, total = await service.list_subscriptions(
        conn,
        department_id=department_id,
        active_on=active_on,
        page=page,
        page_size=page_size,
        now=clock.now(),
    )
    return SubscriptionList.model_validate({"items": items, "total": total})


@router.post("", operation_id="createSubscription", status_code=201)
async def create_subscription(
    body: SubscriptionCreate, session: AdminSession, conn: Conn, clock: ClockDep
) -> Subscription:
    created = await service.create(conn, session.principal.login, body.model_dump(), clock.now())
    return Subscription.model_validate(created)


@router.post("/preview", operation_id="previewSubscription")
async def preview_subscription(
    body: SubscriptionCreate, session: AdminSession, conn: Conn, clock: ClockDep, settings: SettingsDep
) -> SubscriptionPreview:
    result = await service.preview(
        conn, body.model_dump(), clock.now(), default_warn_pct=settings.subs_warn_pct_default
    )
    return SubscriptionPreview.model_validate(result)


@router.get("/{subscription_id}", operation_id="getSubscription")
async def get_subscription(
    subscription_id: UUID, request: Request, session: AdminSession, conn: Conn, clock: ClockDep
) -> Subscription:
    sub, department_id = await service.get_subscription(conn, subscription_id, clock.now())
    await authorize_object(conn, session, request, Scope(department_id=department_id, scoped=True))
    return Subscription.model_validate(sub)


@router.patch("/{subscription_id}", operation_id="endSubscription")
async def end_subscription(
    subscription_id: UUID, body: SubscriptionPatch, session: AdminSession, conn: Conn, clock: ClockDep
) -> Subscription:
    ended = await service.end(conn, session.principal.login, subscription_id, body.end_date, clock.now())
    return Subscription.model_validate(ended)


@router.delete("/{subscription_id}", operation_id="deleteSubscription", status_code=204)
async def delete_subscription(
    subscription_id: UUID, session: AdminSession, conn: Conn, clock: ClockDep
) -> Response:
    await service.delete(conn, session.principal.login, subscription_id, clock.now())
    return Response(status_code=204)
