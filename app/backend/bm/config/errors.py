"""Ошибки изменения настроек (T041, T042). Ядро не знает про HTTP: статусы назначает bm.http.admin_errors.

`code` — машинный код контракта (`Problem.code`), `detail` — сообщение на русском (принцип IX).
Исключение внутри `config.apply` откатывает изменение, аудит и `config_version` целиком.
"""


class ConfigError(Exception):
    code = "validation_error"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code


class NotFound(ConfigError):
    """Объект не найден (404 not_found)."""

    code = "not_found"


class Conflict(ConfigError):
    """Нарушение уникальности (409 conflict)."""

    code = "conflict"


class Invalid(ConfigError):
    """Недопустимое значение (422; `code` по умолчанию validation_error, например fx_rate_missing)."""


class LastAdmin(ConfigError):
    """Не осталось бы активного администратора (409 last_admin, 13_design_rulings.md В6)."""

    code = "last_admin"

    def __init__(self) -> None:
        super().__init__("Нельзя оставить систему без активного администратора.")


class FileTooLarge(ConfigError):
    """Файл AD больше 10 МиБ (413 file_too_large, 13_design_rulings.md В6)."""

    code = "file_too_large"
