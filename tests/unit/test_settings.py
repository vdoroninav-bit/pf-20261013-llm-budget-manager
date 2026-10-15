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
