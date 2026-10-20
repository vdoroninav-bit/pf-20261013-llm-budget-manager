"""JSON-журналы на stdlib `logging` (12_arch_decision.md «Наблюдаемость»; 14_tech_rulings.md, пакет 2, В1-7).

Одна строка — один JSON-объект: `ts` (UTC, ISO 8601), `level`, `logger`, `msg` и дополнительные поля только из
белого списка `EXTRA_FIELDS`. Всё остальное, что передано в `extra=`, отбрасывается: так в журнал не попадёт
случайно переданное содержимое запросов моделей (принцип I). Исключение пишется только классом (`exc_type`),
без текста и трассировки — в них могут оказаться ответы провайдеров.

Значения секретов из настроек (ключи провайдеров, URL прокси импорта с логином и паролем, секрет сессий,
начальный пароль) маскируются в готовой строке (принцип II). Подключается в `api` (`create_app`) и `worker`.

Модуль называется `bm.logging`; внутри пакета `import logging` по-прежнему означает stdlib (абсолютный
импорт).
"""

import json
import logging
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import IO, Any

from pydantic import SecretStr

from bm.settings import Settings

MASK = "***"

# Дополнительные поля, которые разрешено писать в журнал: идентификаторы, коды, счётчики — без содержимого.
EXTRA_FIELDS = frozenset(
    {
        "job",
        "error_class",
        "count",
        "provider",
        "operation",
        "status",
        "kind",
        "department_id",
        "run_id",
        "lines",
        "duration_ms",
        "method",
        "path",
    }
)

_SECRET_FIELDS = (
    "anthropic_admin_key",
    "openai_admin_key",
    "import_https_proxy",
    "session_secret",
    "admin_initial_password",
)
_USERINFO = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://([^@/]+)@")
_HANDLER_MARK = "_bm_json_handler"


def _variants(value: str) -> list[str]:
    """Значение секрета и его части, которые тоже нельзя показывать (логин:пароль и пароль из URL)."""
    out = [value]
    m = _USERINFO.match(value)
    if m:
        userinfo = m.group(1)
        out.append(userinfo)
        if ":" in userinfo:
            out.append(userinfo.split(":", 1)[1])
    return [v for v in out if len(v) >= 4]


def secrets_of(settings: Settings) -> list[str]:
    """Значения секретов из настроек (пустые и отсутствующие поля пропускаются)."""
    found: list[str] = []
    for name in _SECRET_FIELDS:
        value: Any = getattr(settings, name, None)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str) and value:
            found.extend(_variants(value))
    return found


class JsonFormatter(logging.Formatter):
    """Форматтер «одна строка — один JSON» с белым списком полей и маскированием секретов."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        # Длинные значения маскируются первыми: пароль — часть URL прокси.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in EXTRA_FIELDS:
            if key in record.__dict__:
                data[key] = record.__dict__[key]
        if record.exc_info and record.exc_info[0] is not None:
            data["exc_type"] = record.exc_info[0].__name__
        line = json.dumps(data, ensure_ascii=False, default=str)
        for secret in self._secrets:
            line = line.replace(secret, MASK)
            escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
            if escaped != secret:
                line = line.replace(escaped, MASK)
        return line


def setup(settings: Settings, *, stream: IO[str] | None = None) -> None:
    """Подключает JSON-журнал к корневому логгеру; уровень — `LOG_LEVEL`. Повторный вызов заменяет обработчик.

    Чужие обработчики (например, pytest caplog) не трогаются. Журнал доступа uvicorn приглушается: строка
    запроса может содержать `login` сотрудника (ПДн); длительность запросов есть в `/metrics`.
    """
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(h)
    handler = logging.StreamHandler(stream or sys.stderr)
    setattr(handler, _HANDLER_MARK, True)
    handler.setFormatter(JsonFormatter(secrets_of(settings)))
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = True
    access.setLevel(logging.WARNING)
