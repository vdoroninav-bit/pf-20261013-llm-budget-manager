"""Сборка приложения FastAPI: `create_app(settings)`.

Запуск: `uvicorn bm.main:create_app --factory` (deploy/Dockerfile) — настройки читаются из окружения.
Swagger UI и /openapi.json не публикуются: UI тянет скрипты с CDN (принцип IV), а источник истины
контракта — 12_openapi.yaml.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from bm import __version__
from bm.access.auth import ensure_initial_admin
from bm.clock import Clock, SystemClock
from bm.db.migrate import migrate
from bm.db.pool import create_pool
from bm.http.errors import install_error_handlers
from bm.http.routers import auth, notifications, system
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
        yield
    finally:
        await pool.close()


def create_app(settings: Settings | None = None, *, clock: Clock | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]  # значения — из окружения
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
    app.include_router(notifications.router, prefix="/api/v1")
    return app
