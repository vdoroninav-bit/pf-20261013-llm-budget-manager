"""Лента уведомлений админки: `listNotifications`, `markNotificationRead` (FR-020, FR-025, FR-029)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from bm.access.authorize import Scope
from bm.access.permissions import Role
from bm.http.deps import AdminSession, Conn, authorize_object
from bm.http.errors import not_found
from bm.http.schemas import NotificationList, NotificationOut
from bm.notify import service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", operation_id="listNotifications")
async def list_notifications(
    session: AdminSession,
    conn: Conn,
    unread: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> NotificationList:
    p = session.principal
    items, total = await service.list_for_user(
        conn,
        user_id=p.user_id,
        only_department=p.department_id,
        restrict_to_department=p.role is Role.BUDGET_OWNER,
        unread=unread,
        page=page,
        page_size=page_size,
    )
    return NotificationList(
        items=[NotificationOut.model_validate(n, from_attributes=True) for n in items], total=total
    )


@router.post("/{notification_id}/read", operation_id="markNotificationRead", status_code=204)
async def mark_notification_read(
    notification_id: UUID, request: Request, session: AdminSession, conn: Conn
) -> Response:
    exists, department_id = await service.get_department(conn, notification_id)
    if not exists:
        raise not_found("Уведомление не найдено.")
    await authorize_object(conn, session, request, Scope(department_id=department_id, scoped=True))
    await service.mark_read(conn, notification_id, session.principal.user_id)
    return Response(status_code=204)
