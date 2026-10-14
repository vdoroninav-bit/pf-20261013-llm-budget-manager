"""Ошибки HTTP в формате `application/problem+json` (схема `Problem` контракта).

`code` — машинный код на английском, `detail` — сообщение на русском (принцип IX).
Тело запроса в ответах и логах не повторяется (принцип I): в 422 перечисляются только имена полей.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"

log = logging.getLogger("bm.http")


class ProblemError(Exception):
    """Ошибка, которая отдаётся клиенту как Problem. `detail` — на русском."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        *,
        headers: dict[str, str] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(f"{status} {code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail
        self.headers = headers
        self.errors = errors


def unauthorized(detail: str = "Требуется вход или действующий ключ.") -> ProblemError:
    return ProblemError(401, "unauthorized", detail)


def forbidden(detail: str = "Недостаточно прав для этой операции.") -> ProblemError:
    return ProblemError(403, "forbidden", detail)


def not_found(detail: str = "Не найдено.") -> ProblemError:
    return ProblemError(404, "not_found", detail)


# Заголовки RFC 9110 (stdlib `http` запрещён вне bm.egress import-linter'ом, см. importlinter.ini).
_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Content Too Large",
    422: "Unprocessable Content",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


def problem_response(
    status: int,
    code: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    title = _TITLES.get(status, "Error")
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "code": code,
        "detail": detail,
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


_VALIDATION_RU = {
    "missing": "обязательное поле",
    "extra_forbidden": "лишнее поле",
    "string_too_short": "слишком короткое значение",
    "string_too_long": "слишком длинное значение",
    "string_pattern_mismatch": "неверный формат",
    "json_invalid": "некорректный JSON",
    "uuid_parsing": "ожидается UUID",
    "int_parsing": "ожидается целое число",
    "bool_parsing": "ожидается true или false",
    "date_from_datetime_parsing": "ожидается дата ГГГГ-ММ-ДД",
    "enum": "значение не из списка",
    "literal_error": "значение не из списка",
    "greater_than_equal": "значение меньше допустимого",
    "less_than_equal": "значение больше допустимого",
}


def _validation_detail(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors()[:20]:
        loc = [str(p) for p in err.get("loc", ()) if p != "body"]
        field = ".".join(loc) or "тело запроса"
        parts.append(f"{field} — {_VALIDATION_RU.get(err.get('type', ''), 'недопустимое значение')}")
    return "Некорректный запрос: " + "; ".join(parts) + "."


_HTTP_CODES = {
    400: ("bad_request", "Некорректный запрос."),
    401: ("unauthorized", "Требуется вход или действующий ключ."),
    403: ("forbidden", "Недостаточно прав для этой операции."),
    404: ("not_found", "Не найдено."),
    405: ("method_not_allowed", "Метод не поддерживается для этого адреса."),
    413: ("file_too_large", "Слишком большой запрос."),
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(_: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(exc.status, exc.code, exc.detail, headers=exc.headers, errors=exc.errors)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return problem_response(422, "validation_error", _validation_detail(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, detail = _HTTP_CODES.get(exc.status_code, ("http_error", "Ошибка запроса."))
        return problem_response(exc.status_code, code, detail, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _internal(request: Request, exc: Exception) -> JSONResponse:
        log.error(
            "unhandled_error",
            extra={"path": request.url.path, "method": request.method, "error_class": type(exc).__name__},
        )
        return problem_response(
            500, "internal", "Внутренняя ошибка сервиса. Подробности — в журнале сервиса."
        )
