"""Сборка приложения FastAPI: `create_app(settings)`.

Запуск: `uvicorn bm.main:create_app --factory` (deploy/Dockerfile) — настройки читаются из окружения.
Swagger UI и /openapi.json не публикуются: UI тянет скрипты с CDN (принцип IV), а источник истины
контракта — 12_openapi.yaml.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI

from bm import __version__
from bm import logging as bm_logging
from bm.access.auth import ensure_initial_admin
from bm.budget.service import Budget
from bm.clock import Clock, SystemClock
from bm.db.migrate import migrate
from bm.db.pool import create_pool
from bm.http import admin_errors
from bm.http.errors import install_error_handlers
from bm.http.routers import (
    admin_users,
    audit,
    auth,
    caller,
    callers,
    departments,
    employees,
    events,
    fx,
    imports,
    models,
    notifications,
    org,
    provider_sources,
    reports,
    subscriptions,
    system,
)
from bm.settings import Settings

log = logging.getLogger("bm")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    pool = await create_pool(settings.database_url)
    try:
        async with pool.acquire() as conn:
            await migrate(conn)
            password = settings.admin_initial_password
            await ensure_initial_admin(
                conn,
                settings.admin_initial_login,
                password.get_secret_value() if password else None,
                now=app.state.clock.now(),
            )
        app.state.pool = pool
        app.state.budget = Budget(
            pool,
            app.state.clock,
            reservation_ttl=timedelta(minutes=settings.reservation_ttl_min),
            timeout_ms=settings.decision_timeout_ms,
            default_fail_mode=settings.default_fail_mode,
        )
        await app.state.budget.ensure_partitions()
        yield
    finally:
        await pool.close()


def create_app(settings: Settings | None = None, *, clock: Clock | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]  # значения — из окружения
    bm_logging.setup(settings)  # JSON-журнал, уровень LOG_LEVEL (T071)
    app = FastAPI(
        title="LLM Budget Manager API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.clock = clock or SystemClock()
    install_error_handlers(app)
    app.include_router(system.router)
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(caller.router, prefix="/api/v1")
    app.include_router(notifications.router, prefix="/api/v1")
    # Админ-CRUD и загрузка AD (T041, T042)
    admin_errors.install(app)
    for admin_router in (org, departments, employees, models, fx, callers, admin_users):
        app.include_router(admin_router.router, prefix="/api/v1")
    # Подписки (T051), справочник источников и импорт (T061)
    for import_router in (subscriptions, provider_sources, imports):
        app.include_router(import_router.router, prefix="/api/v1")
    # Отчёты, журналы событий и аудита (T071)
    for report_router in (reports, events, audit):
        app.include_router(report_router.router, prefix="/api/v1")
    return app
