"""Ошибки ядра настроек (`bm.config.errors`) → Problem с HTTP-статусом (T041, T042)."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from bm.config.errors import ConfigError, Conflict, FileTooLarge, LastAdmin, NotFound
from bm.http.errors import problem_response

_STATUS: tuple[tuple[type[ConfigError], int], ...] = (
    (NotFound, 404),
    (Conflict, 409),
    (LastAdmin, 409),
    (FileTooLarge, 413),
)


def status_of(exc: ConfigError) -> int:
    for cls, status in _STATUS:
        if isinstance(exc, cls):
            return status
    return 422


def install(app: FastAPI) -> None:
    @app.exception_handler(ConfigError)
    async def _config_error(_: Request, exc: ConfigError) -> JSONResponse:
        return problem_response(status_of(exc), exc.code, exc.detail)
