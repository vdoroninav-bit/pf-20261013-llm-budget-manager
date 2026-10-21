"""Настройки из переменных окружения (12_plan.md §8, deploy/.env.example).

Секреты можно передать файлом: `<ИМЯ>_FILE=/run/secrets/<файл>` имеет приоритет над `<ИМЯ>` (Docker secrets).
Пустая строка в окружении означает «не задано». Секреты хранятся как `SecretStr` и не печатаются (принцип II).
"""

from pathlib import Path
from typing import Any, Literal

from pydantic import AnyUrl, Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Поля-секреты, для которых поддерживается вариант `<ИМЯ>_FILE`.
# Файлы читаются до проверки полей, поэтому валидаторы (схема прокси, длина SESSION_SECRET) действуют и для
# значения из файла, а обязательный SESSION_SECRET можно передать только через SESSION_SECRET_FILE.
_FILE_SECRETS = (
    "session_secret",
    "admin_initial_password",
    "anthropic_admin_key",
    "openai_admin_key",
    "import_https_proxy",  # T097, 14_tech_rulings.md, пакет 4, 4Д4
)


class Settings(BaseSettings):
    # hide_input_in_errors: ошибка старта не печатает значения (ключи, пароль в URL прокси), только текст.
    model_config = SettingsConfigDict(
        env_file=None, extra="ignore", case_sensitive=False, hide_input_in_errors=True
    )

    # База данных
    database_url: str = Field(min_length=1)

    # HTTP
    bind_addr: str = "127.0.0.1"
    api_port: int = 8080
    uvicorn_workers: int = Field(default=2, ge=1)

    # Админка и вход
    session_secret: SecretStr
    session_secret_file: Path | None = None
    admin_initial_login: str = "admin"
    admin_initial_password: SecretStr | None = None
    admin_initial_password_file: Path | None = None
    # Флаг Secure у cookie bm_session (14_tech_rulings.md Б2): true — при HTTPS на обратном прокси заказчика.
    session_cookie_secure: bool = False

    # Решения
    reservation_ttl_min: int = Field(default=15, ge=1)
    decision_timeout_ms: int = Field(default=50, ge=1)
    default_fail_mode: Literal["open", "closed"] = "open"

    # Импорт провайдеров (единственный исходящий трафик, принцип IV)
    import_enabled: bool = False
    anthropic_admin_key: SecretStr | None = None
    anthropic_admin_key_file: Path | None = None
    openai_admin_key: SecretStr | None = None
    openai_admin_key_file: Path | None = None
    import_hour_msk: int = Field(default=4, ge=0, le=23)
    import_retry_until_hour_msk: int = Field(default=12, ge=0, le=23)
    import_window_days: int = Field(default=7, ge=1)
    # Прокси только для импорта (14_tech_rulings.md, пакет 2, Б4): пусто — прямой выход. Стандартные
    # HTTP(S)_PROXY не действуют (egress: trust_env=False). Логин и пароль в URL — секрет.
    import_https_proxy: SecretStr | None = None
    import_https_proxy_file: Path | None = None

    # Уведомления
    webhook_url: str | None = None
    subs_warn_pct_default: int = Field(default=50, ge=1, le=100)

    # Хранение
    retention_detail_months: int = Field(default=12, ge=1)
    retention_agg_months: int = Field(default=36, ge=1)
    backup_keep: int = Field(default=14, ge=1)

    # Журналы
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="before")
    @classmethod
    def _empty_is_unset_and_read_files(cls, data: Any) -> Any:
        """`WEBHOOK_URL=` и пустые секреты в .env — то же, что «не задано». Затем `<ИМЯ>_FILE` подменяет
        `<ИМЯ>` (приоритет файла); пустой файл — «не задано». Значение секрета в текст ошибки не попадает."""
        if not isinstance(data, dict):
            return data
        data = {k: v for k, v in data.items() if not (isinstance(v, str) and v.strip() == "")}
        for name in _FILE_SECRETS:
            path = data.get(f"{name}_file")
            if path is None:
                continue
            try:
                value = Path(path).read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as exc:
                raise ValueError(
                    f"{name.upper()}_FILE: не удалось прочитать файл {path} ({type(exc).__name__}); "
                    "проверьте секрет в compose или уберите переменную"
                ) from None
            if value:
                data[name] = value
            else:
                data.pop(name, None)
        return data

    @field_validator("session_secret")
    @classmethod
    def _secret_not_blank(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().strip():
            raise ValueError("SESSION_SECRET не задан")
        return v

    @field_validator("import_https_proxy")
    @classmethod
    def _proxy_url(cls, v: SecretStr | None) -> SecretStr | None:
        if v is None:
            return v
        try:
            url = AnyUrl(v.get_secret_value().strip())  # urllib в bm.settings запрещён import-linter'ом
            ok = url.scheme in ("http", "https") and bool(url.host)
        except ValidationError:
            ok = False
        if not ok:
            raise ValueError(
                "IMPORT_HTTPS_PROXY: нужен адрес прокси вида http://хост:порт или https://хост:порт "
                "(логин и пароль — http://логин:пароль@хост:порт); пусто — прямой выход"
            )
        return v

    @model_validator(mode="after")
    def _session_secret_length(self) -> "Settings":
        if len(self.session_secret.get_secret_value()) < 16:
            raise ValueError("SESSION_SECRET должен быть не короче 16 символов (openssl rand -hex 32)")
        return self
