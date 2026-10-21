"""Настройки из окружения (T015): значения по умолчанию из .env.example, секреты из *_FILE."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from bm.settings import Settings


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for k in list(os.environ):
        if k.startswith(("ANTHROPIC_", "OPENAI_", "SESSION_", "ADMIN_", "IMPORT_", "WEBHOOK_")):
            monkeypatch.delenv(k, raising=False)
    for k, v in values.items():
        monkeypatch.setenv(k, v)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, DATABASE_URL="postgresql://u@h/db_test", SESSION_SECRET="s" * 32)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.import_enabled is False
    assert s.reservation_ttl_min == 15
    assert s.decision_timeout_ms == 50
    assert s.default_fail_mode == "open"
    assert s.admin_initial_login == "admin"
    assert s.webhook_url is None
    assert s.anthropic_admin_key is None and s.openai_admin_key is None
    assert (s.import_hour_msk, s.import_retry_until_hour_msk, s.import_window_days) == (4, 12, 7)
    assert (s.retention_detail_months, s.retention_agg_months, s.subs_warn_pct_default) == (12, 36, 50)


def test_empty_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(
        monkeypatch,
        DATABASE_URL="postgresql://u@h/db_test",
        SESSION_SECRET="s" * 32,
        WEBHOOK_URL="",
        ANTHROPIC_ADMIN_KEY="",
        ANTHROPIC_ADMIN_KEY_FILE="",
    )
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.webhook_url is None and s.anthropic_admin_key is None


def test_secret_file_has_priority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = tmp_path / "anthropic"
    f.write_text("sk-ant-admin-from-file\n", encoding="utf-8")
    _env(
        monkeypatch,
        DATABASE_URL="postgresql://u@h/db_test",
        SESSION_SECRET="s" * 32,
        ANTHROPIC_ADMIN_KEY="sk-ant-admin-from-env",
        ANTHROPIC_ADMIN_KEY_FILE=str(f),
    )
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.anthropic_admin_key is not None
    assert s.anthropic_admin_key.get_secret_value() == "sk-ant-admin-from-file"
    assert "sk-ant-admin" not in repr(s)  # секреты не печатаются (принцип II)


def test_session_secret_required(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, DATABASE_URL="postgresql://u@h/db_test", SESSION_SECRET="")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_session_cookie_secure_default_false_and_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """14_tech_rulings.md Б2: по умолчанию false (HTTP на 127.0.0.1), true — при HTTPS на обратном прокси."""
    _env(monkeypatch, DATABASE_URL="postgresql://u@h/db_test", SESSION_SECRET="s" * 32)
    assert Settings(_env_file=None).session_cookie_secure is False  # type: ignore[call-arg]
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    assert Settings(_env_file=None).session_cookie_secure is True  # type: ignore[call-arg]


# ---------------------------------------------------------------- T062: IMPORT_HTTPS_PROXY (пакет 2, Б4)

PROXY_PASSWORD = "Pr0xy-Secret-77"


def test_import_https_proxy_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, DATABASE_URL="postgresql://u@h/db_test", SESSION_SECRET="s" * 32, IMPORT_HTTPS_PROXY="")
    assert Settings(_env_file=None).import_https_proxy is None  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "url", ["http://proxy.corp.local:3128", f"https://svc:{PROXY_PASSWORD}@10.0.0.1:8443", "HTTP://p:3128"]
)
def test_import_https_proxy_http_or_https(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    _env(
        monkeypatch, DATABASE_URL="postgresql://u@h/db_test", SESSION_SECRET="s" * 32, IMPORT_HTTPS_PROXY=url
    )
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.import_https_proxy is not None and s.import_https_proxy.get_secret_value() == url
    assert PROXY_PASSWORD not in repr(s) + str(s) + s.model_dump_json()  # пароль прокси — секрет


@pytest.mark.parametrize(
    "url",
    [
        f"socks5://svc:{PROXY_PASSWORD}@proxy:1080",
        f"svc:{PROXY_PASSWORD}@proxy:3128",
        "proxy.corp.local:3128",
        "http://",
        f"ftp://svc:{PROXY_PASSWORD}@proxy/",
    ],
)
def test_import_https_proxy_bad_scheme_fails_at_start(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    _env(
        monkeypatch, DATABASE_URL="postgresql://u@h/db_test", SESSION_SECRET="s" * 32, IMPORT_HTTPS_PROXY=url
    )
    with pytest.raises(ValidationError) as err:
        Settings(_env_file=None)  # type: ignore[call-arg]
    text = str(err.value)
    assert "IMPORT_HTTPS_PROXY" in text and "http://" in text  # понятный текст на русском
    assert PROXY_PASSWORD not in text  # ошибка старта не печатает пароль


# ---------------------------------------------------------------- T097: IMPORT_HTTPS_PROXY_FILE (пакет 4, 4Д4)


def test_import_https_proxy_file_has_priority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = tmp_path / "import_https_proxy"
    f.write_text(f"http://svc:{PROXY_PASSWORD}@10.0.0.1:3128\n", encoding="utf-8")
    _env(
        monkeypatch,
        DATABASE_URL="postgresql://u@h/db_test",
        SESSION_SECRET="s" * 32,
        IMPORT_HTTPS_PROXY="http://from-env:3128",
        IMPORT_HTTPS_PROXY_FILE=str(f),
    )
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.import_https_proxy is not None
    assert s.import_https_proxy.get_secret_value() == f"http://svc:{PROXY_PASSWORD}@10.0.0.1:3128"
    assert PROXY_PASSWORD not in repr(s) + str(s) + s.model_dump_json()


def test_import_https_proxy_empty_file_means_direct(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = tmp_path / "import_https_proxy"
    f.write_text("\n", encoding="utf-8")
    _env(
        monkeypatch,
        DATABASE_URL="postgresql://u@h/db_test",
        SESSION_SECRET="s" * 32,
        IMPORT_HTTPS_PROXY_FILE=str(f),
    )
    assert Settings(_env_file=None).import_https_proxy is None  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "url", [f"socks5://svc:{PROXY_PASSWORD}@proxy:1080", f"svc:{PROXY_PASSWORD}@proxy:3128", "http://"]
)
def test_import_https_proxy_file_scheme_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str
) -> None:
    """Проверка схемы http(s):// действует и для значения из файла; ошибка не печатает пароль."""
    f = tmp_path / "import_https_proxy"
    f.write_text(url + "\n", encoding="utf-8")
    _env(
        monkeypatch,
        DATABASE_URL="postgresql://u@h/db_test",
        SESSION_SECRET="s" * 32,
        IMPORT_HTTPS_PROXY="http://from-env:3128",  # корректное значение из окружения не спасает: _FILE главнее
        IMPORT_HTTPS_PROXY_FILE=str(f),
    )
    with pytest.raises(ValidationError) as err:
        Settings(_env_file=None)  # type: ignore[call-arg]
    text = str(err.value)
    assert "IMPORT_HTTPS_PROXY" in text and "http://" in text
    assert PROXY_PASSWORD not in text


def test_import_https_proxy_file_missing_fails_at_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Файл указан, но его нет — ошибка старта на русском с именем переменной, без значений секретов."""
    missing = tmp_path / "нет-такого"
    _env(
        monkeypatch,
        DATABASE_URL="postgresql://u@h/db_test",
        SESSION_SECRET="s" * 32,
        IMPORT_HTTPS_PROXY=f"http://svc:{PROXY_PASSWORD}@10.0.0.1:3128",
        IMPORT_HTTPS_PROXY_FILE=str(missing),
    )
    with pytest.raises(ValidationError) as err:
        Settings(_env_file=None)  # type: ignore[call-arg]
    text = str(err.value)
    assert "IMPORT_HTTPS_PROXY_FILE" in text and "не удалось прочитать файл" in text
    assert PROXY_PASSWORD not in text and "s" * 32 not in text


def test_session_secret_from_file_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Compose передаёт только SESSION_SECRET_FILE: обязательный секрет из файла — достаточно (найдено в T097)."""
    f = tmp_path / "session_secret"
    f.write_text("x" * 32 + "\n", encoding="utf-8")
    _env(monkeypatch, DATABASE_URL="postgresql://u@h/db_test", SESSION_SECRET_FILE=str(f))
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.session_secret.get_secret_value() == "x" * 32
