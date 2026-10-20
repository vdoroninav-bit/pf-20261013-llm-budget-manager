"""T071 (14_tech_rulings.md, пакет 2, В1-7): JSON-журналы на stdlib `logging`.

Строка журнала — один JSON-объект с полями `ts`, `level`, `logger`, `msg` и только дополнительными полями из белого
списка; значения секретов (`*_ADMIN_KEY`, `IMPORT_HTTPS_PROXY`) в строку не попадают.
"""

import io
import json
import logging
from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from bm import logging as bm_logging
from bm.settings import Settings

ANTHROPIC = "sk-ant-admin01-QA-NOT-REAL-91c2e7"
OPENAI = "sk-admin-QA-NOT-REAL-5b44d0"
PROXY = "http://svc-user:Pr0xy-Secret-77@proxy.corp.local:3128"


@pytest.fixture
def restore_root() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _record(msg: str, *args: object, **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("bm.test", logging.INFO, __file__, 1, msg, args, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_line_is_json_with_base_fields() -> None:
    line = bm_logging.JsonFormatter().format(_record("job_failed %s", "x", job="import", error_class="Boom"))
    assert "\n" not in line
    data = json.loads(line)
    assert data["level"] == "INFO" and data["logger"] == "bm.test" and data["msg"] == "job_failed x"
    assert data["ts"].endswith("Z") or "+00:00" in data["ts"]
    assert (data["job"], data["error_class"]) == ("import", "Boom")


def test_extra_fields_outside_whitelist_are_dropped() -> None:
    data = json.loads(
        bm_logging.JsonFormatter().format(
            _record("x", prompt="расскажи", messages=["a"], content="b", count=3)
        )
    )
    assert "prompt" not in data and "messages" not in data and "content" not in data
    assert data["count"] == 3


def test_secret_values_are_masked_everywhere() -> None:
    fmt = bm_logging.JsonFormatter(secrets=[ANTHROPIC, OPENAI, PROXY])
    line = fmt.format(_record("ключ %s прокси %s", ANTHROPIC, PROXY, error_class=OPENAI))
    for secret in (ANTHROPIC, OPENAI, PROXY, "Pr0xy-Secret-77"):
        assert secret not in line
    json.loads(line)


def test_exception_is_reported_by_class_without_traceback_text() -> None:
    try:
        raise ValueError(f"плохой ответ с ключом {ANTHROPIC}")
    except ValueError:
        rec = logging.LogRecord(
            "bm.test", logging.ERROR, __file__, 1, "boom", (), __import__("sys").exc_info()
        )
    data = json.loads(bm_logging.JsonFormatter(secrets=[ANTHROPIC]).format(rec))
    assert data["exc_type"] == "ValueError"
    assert ANTHROPIC not in json.dumps(data)


def test_setup_from_settings_writes_json_without_secrets(restore_root: None) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql://u@h/db_test",
        session_secret="s" * 32,
        anthropic_admin_key=SecretStr(ANTHROPIC),
        openai_admin_key=SecretStr(OPENAI),
        log_level="DEBUG",
    )
    object.__setattr__(settings, "import_https_proxy", PROXY)  # поле появляется в T062
    stream = io.StringIO()
    bm_logging.setup(settings, stream=stream)
    bm_logging.setup(settings, stream=stream)  # повторный вызов не дублирует обработчик
    logging.getLogger("bm.provider_import").warning(
        "ответ провайдера: %s / %s / %s", ANTHROPIC, OPENAI, PROXY
    )
    logging.getLogger("bm.x").debug("отладка")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(x) for x in lines]
    assert [p["level"] for p in parsed] == ["WARNING", "DEBUG"]
    for secret in (ANTHROPIC, OPENAI, PROXY, "Pr0xy-Secret-77"):
        assert secret not in stream.getvalue()


def test_level_comes_from_settings(restore_root: None) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql://u@h/db_test",
        session_secret="s" * 32,
        log_level="WARNING",
    )
    stream = io.StringIO()
    bm_logging.setup(settings, stream=stream)
    logging.getLogger("bm.x").info("не видно")
    logging.getLogger("bm.x").warning("видно")
    assert [json.loads(x)["msg"] for x in stream.getvalue().splitlines()] == ["видно"]
