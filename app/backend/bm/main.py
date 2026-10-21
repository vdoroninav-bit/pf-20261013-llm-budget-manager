"""Сборка приложения FastAPI: `create_app(settings)`.

Запуск: `uvicorn bm.main:create_app --factory` (deploy/Dockerfile) — настройки читаются из окружения.
Swagger UI и /openapi.json не публикуются: UI тянет скрипты с CDN (принцип IV), а источник истины
контракта — 12_openapi.yaml.

Админка (SPA, T096 / 4Д6): образ кладёт сборку в `ADMIN_STATIC_DIR`, процесс api раздаёт её по /admin/
(`StaticFiles` из Starlette). Каталога нет (разработка, локальный стек) — монтирования нет, API работает.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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
from bm.notify import service as notify
from bm.settings import Settings

log = logging.getLogger("bm")

# Куда образ кладёт сборку админки (deploy/Dockerfile, стадия python-runtime).
# Константа, а не переменная окружения (4Д6).
ADMIN_STATIC_DIR = Path("/app/static/admin")


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
        notify.configure(webhook_enabled=bool(settings.webhook_url))  # статус новых уведомлений (T073)
        yield
    finally:
        notify.configure(webhook_enabled=False)
        await pool.close()


def create_app(
    settings: Settings | None = None, *, clock: Clock | None = None, admin_dir: Path = ADMIN_STATIC_DIR
) -> FastAPI:
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
    _mount_admin(app, admin_dir)
    return app


def _mount_admin(app: FastAPI, admin_dir: Path) -> None:
    """SPA админки на /admin/: index.html на `/admin/`, ассеты по `base: '/admin/'`, `/admin` → `/admin/`.

    Маршруты SPA хешевые (`/admin/#/…`), поэтому отсутствующий файл — 404, а не index.html. Пути вне каталога
    StaticFiles не отдаёт. В схему `app.openapi()` монтирование не попадает (контракт его не описывает).
    """
    if not admin_dir.is_dir():
        log.info("admin_static_disabled", extra={"admin_dir": str(admin_dir)})
        return
    app.mount("/admin", StaticFiles(directory=admin_dir, html=True), name="admin")
