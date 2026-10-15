"""Учётные записи админки: `listAdminUsers`, `createAdminUser`, `updateAdminUser` (FR-030, T042).

`409 last_admin` — нельзя оставить систему без активного администратора (13_design_rulings.md В6).
"""

from uuid import UUID

from fastapi import APIRouter

from bm.access import admin_users as service
from bm.http.deps import AdminSession, Conn
from bm.http.schemas import AdminUser
from bm.http.schemas_admin import AdminUserCreate, AdminUserList, AdminUserPatch, patch_of

router = APIRouter(prefix="/admin-users", tags=["admin-users"])


@router.get("", operation_id="listAdminUsers")
async def list_admin_users(session: AdminSession, conn: Conn) -> AdminUserList:
    items = await service.list_users(conn)
    return AdminUserList.model_validate({"items": items, "total": len(items)})


@router.post("", operation_id="createAdminUser", status_code=201)
async def create_admin_user(body: AdminUserCreate, session: AdminSession, conn: Conn) -> AdminUser:
    created = await service.create_user(
        conn,
        session.principal.login,
        login=body.login,
        password=body.password,
        role=body.role,
        department_id=body.department_id,
    )
    return AdminUser.model_validate(created)


@router.patch("/{user_id}", operation_id="updateAdminUser")
async def update_admin_user(
    user_id: UUID, body: AdminUserPatch, session: AdminSession, conn: Conn
) -> AdminUser:
    return AdminUser.model_validate(
        await service.update_user(conn, session.principal.login, user_id, patch_of(body))
    )
