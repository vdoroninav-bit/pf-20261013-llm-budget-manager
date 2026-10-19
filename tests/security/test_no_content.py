"""T070, SC-011 / FR-034 / принцип I: ни одного поля с текстом запросов или ответов моделей — в схеме БД, в данных,
в ответах API и в журналах прогона.

- В схеме нет текстовых колонок вне белого списка; любая новая текстовая колонка требует осознанного решения
  (добавить сюда с обоснованием в ревью).
- Поля `prompt`, `messages`, `content` в запросах Decision API отклоняются (422), их значение не возвращается
  в ответе, не пишется в журнал и не попадает в БД.
"""

import logging
import re
from typing import Any

import asyncpg
import httpx
import pytest

from tests.fixtures.qa import catalog, decide, decision_body, key_headers, report, usage_body
from tests.fixtures.seed import Seed

pytestmark = pytest.mark.db

# Текстовые колонки (text, varchar, json/jsonb, массивы text): только идентификаторы, коды и служебные сообщения.
TEXT_COLUMNS: dict[str, set[str]] = {
    "admin_user": {"argon2_hash", "login", "role"},
    "agg_daily": {"model_id", "source"},
    "audit_log": {"action", "actor", "after", "before", "object_id", "object_type"},
    "budget_lock": {"owner_type"},
    "call_record": {
        "call_status",
        "code",
        "est_source",
        "final_model",
        "flags",
        "outcome",
        "requested_model",
        "status",
    },
    "caller_system": {"key_prefix", "name", "scope_type", "status"},
    "department": {"ad_group", "fail_mode", "name", "policy_mode"},
    "dept_model_allow": {"model_id"},
    "employee": {"ad_login", "display_name", "pseudonym"},
    "fx_rate": {"entered_by"},
    "import_line": {"currency", "model", "provider", "source_ref", "status"},
    "import_run": {"error_class", "provider", "status"},
    "job_run": {"error_class", "job"},
    "ledger_daily": {"owner_type", "source"},
    "limit_def": {"owner_type", "period"},
    "model": {"id", "kind", "provider"},
    "model_price": {"currency", "model_id"},
    "notification": {"kind", "message", "period", "webhook_status"},  # message — текст утилиты, не модели
    "provider_source": {"display_name", "kind", "mode", "provider", "ref"},
    "schema_migrations": {"version"},
    "session": {"csrf_token"},
    "subscription": {"assignee_type", "currency", "plan", "provider"},
    "subscription_month": {"fx_warning"},
}
CONTENT_NAME = re.compile(
    r"prompt|messages?|content|completion|response|answer|question|input_text|output_text"
)
CONTENT_NAME_ALLOWED = {("notification", "message")}
SECRET_TEXT = "QA-СОДЕРЖИМОЕ-ЗАПРОСА-7f3a: расскажи про бюджет"

TEXT_COLUMNS_SQL = """
SELECT c.table_name, c.column_name
FROM information_schema.columns c
JOIN pg_class k ON k.relname = c.table_name
JOIN pg_namespace n ON n.oid = k.relnamespace AND n.nspname = 'public'
WHERE c.table_schema = 'public' AND NOT k.relispartition AND k.relkind IN ('r', 'p')
  AND (c.data_type IN ('text', 'character varying', 'character', 'json', 'jsonb')
       OR c.udt_name IN ('_text', '_varchar', '_json', '_jsonb'))
"""


async def db_dump(conn: asyncpg.Connection) -> str:
    """Текстовое представление всех строк всех таблиц схемы public (как грубый «дамп БД»)."""
    tables = await conn.fetch(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition"
    )
    parts = []
    for t in tables:
        name = t["relname"]
        rows = await conn.fetch(f'SELECT t::text AS r FROM "{name}" t')  # noqa: S608 — имена из pg_class
        parts.extend(r["r"] for r in rows)
    return "\n".join(parts)


def log_text(records: list[logging.LogRecord]) -> str:
    return "\n".join(f"{r.getMessage()} {r.__dict__!r}" for r in records)


async def test_schema_has_no_text_columns_outside_whitelist(db: asyncpg.Connection) -> None:
    actual: dict[str, set[str]] = {}
    for r in await db.fetch(TEXT_COLUMNS_SQL):
        actual.setdefault(r["table_name"], set()).add(r["column_name"])
    extra = {
        t: cols - TEXT_COLUMNS.get(t, set())
        for t, cols in actual.items()
        if cols - TEXT_COLUMNS.get(t, set())
    }
    assert extra == {}, f"новые текстовые колонки вне белого списка (принцип I): {extra}"


async def test_no_column_is_named_like_model_content(db: asyncpg.Connection) -> None:
    cols = await db.fetch(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"
    )
    bad = sorted(
        (r["table_name"], r["column_name"])
        for r in cols
        if CONTENT_NAME.search(r["column_name"])
        and (r["table_name"], r["column_name"]) not in CONTENT_NAME_ALLOWED
        and not r["table_name"].startswith("call_record_")
    )
    assert bad == []


@pytest.mark.parametrize(
    ("path", "field", "value"),
    [
        ("/api/v1/decisions", "prompt", SECRET_TEXT),
        ("/api/v1/decisions", "messages", [{"role": "user", "content": SECRET_TEXT}]),
        ("/api/v1/usage", "content", SECRET_TEXT),
        ("/api/v1/usage", "messages", [{"role": "assistant", "content": SECRET_TEXT}]),
    ],
)
async def test_content_fields_rejected_not_echoed_not_logged_not_stored(
    client: httpx.AsyncClient,
    seed: Seed,
    db: asyncpg.Connection,
    caplog: pytest.LogCaptureFixture,
    path: str,
    field: str,
    value: Any,
) -> None:
    await catalog(seed, only="A")
    dep = await seed.department("Маркетинг", allowed=("A",))
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key()
    body = (
        decision_body(model="A")
        if path.endswith("decisions")
        else usage_body(None, login="ivanov", model="A")
    )
    body[field] = value
    with caplog.at_level(logging.DEBUG):
        resp = await client.post(path, json=body, headers=key_headers(key))
    assert resp.status_code == 422, resp.text
    assert "QA-СОДЕРЖИМОЕ" not in resp.text, "значение запрещённого поля возвращено в ответе"
    assert "QA-СОДЕРЖИМОЕ" not in log_text(caplog.records), "значение запрещённого поля попало в журнал"
    assert "QA-СОДЕРЖИМОЕ" not in await db_dump(db)


async def test_full_flow_logs_have_no_content_fields(
    client: httpx.AsyncClient, seed: Seed, caplog: pytest.LogCaptureFixture
) -> None:
    await catalog(seed, only="A")
    dep = await seed.department("Маркетинг", allowed=("A",))
    await seed.employee("ivanov", department=dep)
    _, key = await seed.caller_key()
    with caplog.at_level(logging.DEBUG):
        d = await decide(client, key, decision_body(model="A"))
        await report(client, key, usage_body(d["decision_id"]))
        await client.post("/api/v1/decisions", json=decision_body(model="nope"), headers=key_headers(key))
    for r in caplog.records:
        assert not {"prompt", "messages", "content"} & set(r.__dict__), (
            f"поле содержимого в журнале: {r.__dict__}"
        )
