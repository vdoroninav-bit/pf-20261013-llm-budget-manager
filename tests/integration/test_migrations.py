"""T014: схема БД (12_plan.md §7), раннер миграций, запрет UPDATE/DELETE на audit_log."""

from datetime import date

import asyncpg
import pytest

from bm.db.migrate import migrate

pytestmark = pytest.mark.db

TABLES = {
    "department",
    "employee",
    "employee_assignment",
    "caller_system",
    "limit_def",
    "dept_model_allow",
    "model",
    "model_price",
    "fx_rate",
    "budget_lock",
    "call_record",
    "report_key",
    "ledger_daily",
    "subscription",
    "subscription_month",
    "provider_source",
    "import_line",
    "import_run",
    "notification",
    "agg_daily",
    "admin_user",
    "session",
    "audit_log",
    "config_version",
    "job_run",
    "schema_migrations",
}


async def test_all_tables_exist(db: asyncpg.Connection) -> None:
    rows = await db.fetch(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition"
    )
    assert TABLES <= {r["relname"] for r in rows}


async def test_migrations_recorded_and_idempotent(db: asyncpg.Connection) -> None:
    applied = await db.fetchval("SELECT array_agg(version ORDER BY version) FROM schema_migrations")
    assert applied[0] == "0001_init"
    assert await migrate(db) == []  # повторный запуск ничего не применяет


async def test_audit_log_insert_allowed_update_delete_forbidden(db: asyncpg.Connection) -> None:
    await db.execute(
        "INSERT INTO audit_log(actor, action, object_type, object_id, after) VALUES ('admin', 'create', 'x', '1', '{}')"
    )
    with pytest.raises(asyncpg.PostgresError, match="audit_log"):
        await db.execute("UPDATE audit_log SET actor = 'evil'")
    with pytest.raises(asyncpg.PostgresError, match="audit_log"):
        await db.execute("DELETE FROM audit_log")
    with pytest.raises(asyncpg.PostgresError, match="audit_log"):
        await db.execute("TRUNCATE audit_log")
    assert await db.fetchval("SELECT actor FROM audit_log") == "admin"


async def test_audit_log_privileges_revoked_for_owner(db: asyncpg.Connection) -> None:
    # REVOKE UPDATE, DELETE, TRUNCATE: у владельца таблицы (роль приложения) этих прав нет.
    privs = await db.fetchval(
        "SELECT array_agg(DISTINCT a.privilege_type) FROM pg_class c, aclexplode(c.relacl) a "
        "WHERE c.relname = 'audit_log' AND a.grantee = c.relowner"
    )
    assert "INSERT" in privs and "SELECT" in privs
    assert not {"UPDATE", "DELETE", "TRUNCATE"} & set(privs)


async def test_call_record_partitions_current_and_next_month(db: asyncpg.Connection) -> None:
    today = await db.fetchval("SELECT (now() AT TIME ZONE 'Europe/Moscow')::date")
    cur = today.replace(day=1)
    nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    names = {
        r["relname"]
        for r in await db.fetch(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE i.inhparent = 'call_record'::regclass"
        )
    }
    assert {f"call_record_{cur:%Y_%m}", f"call_record_{nxt:%Y_%m}"} <= names


async def test_ensure_partitions_function_is_idempotent(db: asyncpg.Connection) -> None:
    await db.execute("SELECT bm_ensure_call_record_partitions('2027-01-15'::date)")
    await db.execute("SELECT bm_ensure_call_record_partitions('2027-01-15'::date)")
    n = await db.fetchval(
        "SELECT count(*) FROM pg_class WHERE relname IN ('call_record_2027_01', 'call_record_2027_02')"
    )
    assert n == 2


async def test_budget_lock_row_created_with_owner(db: asyncpg.Connection) -> None:
    dep = await db.fetchval("INSERT INTO department(name) VALUES ('Маркетинг') RETURNING id")
    emp = await db.fetchval(
        "INSERT INTO employee(pseudonym, ad_login) VALUES ('emp-0000000001', 'ivanov') RETURNING id"
    )
    rows = await db.fetch("SELECT owner_type, owner_id, reserved_kop FROM budget_lock ORDER BY owner_type")
    assert [(r["owner_type"], r["owner_id"], r["reserved_kop"]) for r in rows] == [
        ("department", dep, 0),
        ("employee", emp, 0),
    ]


async def test_new_department_defaults_observe_and_fail_open(db: asyncpg.Connection) -> None:
    row = await db.fetchrow("INSERT INTO department(name) VALUES ('Новое') RETURNING policy_mode, fail_mode, subs_warn_pct")
    assert (row["policy_mode"], row["fail_mode"], row["subs_warn_pct"]) == ("observe", "open", 50)


async def test_config_version_single_row(db: asyncpg.Connection) -> None:
    assert await db.fetchval("SELECT version FROM config_version") == 1
    with pytest.raises(asyncpg.PostgresError):
        await db.execute("INSERT INTO config_version(id, version) VALUES (false, 2)")


async def test_one_open_assignment_per_employee(db: asyncpg.Connection) -> None:
    d1 = await db.fetchval("INSERT INTO department(name) VALUES ('Д1') RETURNING id")
    d2 = await db.fetchval("INSERT INTO department(name) VALUES ('Д2') RETURNING id")
    e = await db.fetchval("INSERT INTO employee(pseudonym, ad_login) VALUES ('emp-0000000002', 'p') RETURNING id")
    await db.execute(
        "INSERT INTO employee_assignment(employee_id, department_id, valid_from) VALUES ($1, $2, '2026-10-01')", e, d1
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO employee_assignment(employee_id, department_id, valid_from) VALUES ($1, $2, '2026-10-02')",
            e,
            d2,
        )


async def test_money_columns_are_integer_kopecks(db: asyncpg.Connection) -> None:
    rows = await db.fetch(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name LIKE '%\\_kop'"
    )
    assert rows, "должны быть денежные колонки *_kop"
    assert {r["data_type"] for r in rows} == {"bigint"}


async def test_no_float_columns(db: asyncpg.Connection) -> None:
    n = await db.fetchval(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
        "AND data_type IN ('real', 'double precision')"
    )
    assert n == 0


async def test_subscription_money_fields_immutable(db: asyncpg.Connection) -> None:
    sid = await db.fetchval(
        "INSERT INTO subscription(provider, plan, seat_price, currency, seats, start_date, assignee_type) "
        "VALUES ('anthropic', 'Team', 2500, 'RUB', 3, '2026-10-01', 'unallocated') RETURNING id"
    )
    with pytest.raises(asyncpg.PostgresError, match="subscription"):
        await db.execute("UPDATE subscription SET seats = 4 WHERE id = $1", sid)
    await db.execute("UPDATE subscription SET end_date = '2026-10-31' WHERE id = $1", sid)


async def test_notification_unique_key_treats_nulls_as_equal(db: asyncpg.Connection) -> None:
    sql = (
        "INSERT INTO notification(kind, department_id, period, period_start, message) "
        "VALUES ('import_failure', NULL, NULL, '2026-10-14', 'x')"
    )
    await db.execute(sql)
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(sql)
