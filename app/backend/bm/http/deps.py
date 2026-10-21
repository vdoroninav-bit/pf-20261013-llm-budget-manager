"""Зависимости FastAPI: соединение с БД, часы, настройки, сессия админки и проверка прав.
Аутентификация вызывающих систем — по `X-Api-Key` (`CallerDep`).

Права проверяются по `operationId` маршрута (`PERMISSIONS`), поэтому роутеру достаточно объявить
параметр `session: AdminSession` — таблица прав одна, и разойтись с контрактом негде.
"""

import hmac
from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import Depends, Request

from bm.access import audit
from bm.access.auth import Session, resolve_session
from bm.access.authorize import Forbidden, Scope, authorize
from bm.access.permissions import ALLOWED_BEFORE_PASSWORD_CHANGE
from bm.budget.errors import BudgetError
from bm.budget.service import Budget, CallerCtx
from bm.clock import Clock
from bm.http.errors import ProblemError, forbidden, unauthorized
from bm.settings import Settings

SESSION_COOKIE = "bm_session"
CSRF_HEADER = "X-CSRF-Token"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def get_conn(request: Request) -> AsyncIterator[asyncpg.Connection]:
    """Одно соединение из пула на запрос; FastAPI кэширует зависимость, поэтому оно общее для всех Depends."""
    pool: asyncpg.Pool = request.app.state.pool
    async with pool.acquire() as conn:
        yield conn


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


Conn = Annotated[asyncpg.Connection, Depends(get_conn)]
ClockDep = Annotated[Clock, Depends(get_clock)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def operation_id(request: Request) -> str:
    route = request.scope.get("route")
    op_id = getattr(route, "operation_id", None)
    if not op_id:  # ошибка программиста: маршрут админки без operationId
        raise RuntimeError(f"маршрут {request.url.path} без operation_id")
    return str(op_id)


async def audit_forbidden(
    conn: asyncpg.Connection, session: Session, request: Request, exc: Forbidden
) -> None:
    """Попытка запрещённого доступа (FR-031). Соединение вне транзакции — запись фиксируется сразу."""
    await audit.write(
        conn,
        actor=session.principal.login,
        action="forbidden_access",
        object_type="operation",
        object_id=exc.action,
        after={"method": request.method, "path": request.url.path, "role": session.principal.role.value},
        at=get_clock(request).now(),
    )


async def admin_session(request: Request, conn: Conn, clock: ClockDep, settings: SettingsDep) -> Session:
    """Сессия админки + CSRF + смена пароля + роль по `PERMISSIONS[operationId]`."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise unauthorized("Требуется вход в админку.")
    session = await resolve_session(
        conn, token, now=clock.now(), secret=settings.session_secret.get_secret_value()
    )
    if session is None:
        raise unauthorized("Сессия истекла или недействительна. Войдите снова.")
    op_id = operation_id(request)
    if request.method in _UNSAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent, session.csrf_token):
            raise ProblemError(
                403, "forbidden", "Нет или неверный CSRF-токен. Обновите страницу и повторите."
            )
    if session.principal.must_change_password and op_id not in ALLOWED_BEFORE_PASSWORD_CHANGE:
        raise ProblemError(403, "password_change_required", "Сначала смените пароль.")
    try:
        authorize(session.principal, op_id, Scope())
    except Forbidden as exc:
        await audit_forbidden(conn, session, request, exc)
        raise forbidden() from exc
    return session


AdminSession = Annotated[Session, Depends(admin_session)]


async def authorize_object(
    conn: asyncpg.Connection, session: Session, request: Request, scope: Scope
) -> None:
    """Проверка доступа к конкретному объекту (подразделение владельца бюджета); отказ — 403 + аудит."""
    try:
        authorize(session.principal, operation_id(request), scope)
    except Forbidden as exc:
        await audit_forbidden(conn, session, request, exc)
        raise forbidden("Объект другого подразделения недоступен.") from exc


# ---------------------------------------------------------------- вызывающие системы (T021)

API_KEY_HEADER = "X-Api-Key"


def get_budget(request: Request) -> Budget:
    budget: Budget = request.app.state.budget
    return budget


def budget_problem(exc: BudgetError) -> ProblemError:
    """Ошибка модуля `budget` → Problem (`code` контракта, `detail` на русском)."""
    return ProblemError(exc.status, exc.code, exc.detail)


async def caller_ctx(request: Request) -> CallerCtx:
    """Аутентификация по `X-Api-Key`: SHA-256 → снимок конфигурации; нет или отозван — 401."""
    try:
        return await get_budget(request).authenticate(request.headers.get(API_KEY_HEADER))
    except BudgetError as exc:
        raise budget_problem(exc) from exc


BudgetDep = Annotated[Budget, Depends(get_budget)]
CallerDep = Annotated[CallerCtx, Depends(caller_ctx)]
