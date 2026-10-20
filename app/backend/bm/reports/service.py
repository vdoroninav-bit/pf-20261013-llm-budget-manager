"""Отчёты и журналы админки (FR-026–FR-028, FR-031, SC-005): расход по подразделениям, моделям и источникам,
CSV, сверка Decision API с импортом, прослеживаемость остатка, журнал событий и журнал аудита.

Источники сумм — те же, что у остатка (FR-026), поэтому отчёт и остаток сходятся:
- `token` — факт событий `call_record` (статусы `settled`, `expired`) по дате расхода и подразделению записи;
  модель — `final_model`, т. е. фактическая модель, по которой списаны деньги (пакет 2, В1-3). Дни старше
  самой старой партиции `call_record` (ретенция детальных данных) берутся из `agg_daily`;
- `import` — строки `import_line` со статусом `charged` (подразделение) и `unallocated` («не распределено»);
  строки `reconcile` — это сверка, а не расход (FR-023);
- `subscription` — амортизация подписок по дням по сегодня включительно (`bm.budget.subs`, T051);
  подписки «не распределено» — отдельной строкой без подразделения (4.6).

Идентификаторов сотрудников в отчётах нет (FR-028); псевдонимы — только в журнале событий и прослеживаемости,
и только тем ролям, которым они разрешены.
"""

import csv
import io
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from uuid import UUID

import asyncpg

from bm.budget import subs
from bm.budget.schemas import BalanceReport, LimitBalance
from bm.budget.service import Budget, OwnerRef
from bm.budget.snapshot import ConfigSnapshot
from bm.budget.snapshot import load as load_snapshot
from bm.domain.money import kop_to_str, str_to_kop
from bm.domain.periods import MSK, bounds, month_end, month_start, msk_date
from bm.domain.types import Kop, PeriodKind

GROUP_KEYS = ("department", "model", "source", "day", "month")
CSV_COLUMNS = (
    "period_start",
    "department",
    "model",
    "source",
    "amount_rub",
    "original_currency",
    "original_amount",
)
UNALLOCATED_RU = "Не распределено"
_CSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")
_CENT = Decimal("0.01")


# ---------------------------------------------------------------- фильтр подразделения


@dataclass(frozen=True, slots=True)
class DeptFilter:
    """Какие подразделения попадают в отчёт: все, одно или только «не распределено»."""

    kind: Literal["all", "one", "unallocated"] = "all"
    department_id: UUID | None = None

    def sql(self, column: str, first_arg: int) -> tuple[str, list[Any]]:
        """Условие SQL по колонке подразделения; параметр — `$first_arg`."""
        if self.kind == "one":
            return f"{column} = ${first_arg}", [self.department_id]
        if self.kind == "unallocated":
            return f"{column} IS NULL", []
        return "TRUE", []

    def accepts(self, department_id: UUID | None) -> bool:
        if self.kind == "one":
            return department_id == self.department_id
        if self.kind == "unallocated":
            return department_id is None
        return True


# ---------------------------------------------------------------- факты расхода


@dataclass(slots=True)
class Fact:
    """Сумма одного источника за день (или кусок месяца) по подразделению и модели."""

    day: date
    department_id: UUID | None
    model_id: str | None
    source: str
    amount_kop: Kop
    currency: str | None = None
    original: Decimal | None = None  # сумма в исходной валюте
    late: bool = False


def _msk_midnight(d: date) -> datetime:
    return datetime.combine(d, time(0), tzinfo=MSK)


async def oldest_detail_day(conn: asyncpg.Connection) -> date | None:
    """Первый день самой старой партиции `call_record` (до него детальных событий нет — ретенция)."""
    names = await conn.fetch(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'call_record'"
    )
    months = [partition_month(r["relname"]) for r in names]
    known = [m for m in months if m is not None]
    return min(known) if known else None


def partition_month(name: str) -> date | None:
    """`call_record_2026_10` → 2026-10-01; чужое имя — None."""
    prefix = "call_record_"
    if not name.startswith(prefix):
        return None
    try:
        y, m = name[len(prefix) :].split("_")
        return date(int(y), int(m), 1)
    except ValueError:
        return None


async def _token_facts(conn: asyncpg.Connection, lo: date, hi: date, dept: DeptFilter) -> list[Fact]:
    out: list[Fact] = []
    detail_from = await oldest_detail_day(conn)
    live_lo = lo if detail_from is None else max(lo, detail_from)
    if live_lo <= hi:
        cond, args = dept.sql("department_id", 5)
        rows = await conn.fetch(
            "SELECT expense_date, department_id, coalesce(final_model, requested_model) AS model, "  # noqa: S608
            "sum(fact_kop) AS amount FROM call_record "
            "WHERE fact_kop IS NOT NULL AND status IN ('settled', 'expired') "
            "AND expense_date BETWEEN $1 AND $2 AND created_at >= $3 AND created_at < $4 "
            f"AND {cond} GROUP BY 1, 2, 3",
            live_lo,
            hi,
            _msk_midnight(live_lo),
            _msk_midnight(hi + timedelta(days=8)),  # отчёт без решения — не позже 7 суток после расхода
            *args,
        )
        out += [
            Fact(r["expense_date"], r["department_id"], r["model"], "token", int(r["amount"]), "RUB")
            for r in rows
        ]
    if detail_from is not None and lo < detail_from:
        agg_hi = min(hi, detail_from - timedelta(days=1))
        cond, args = dept.sql("department_id", 3)
        rows = await conn.fetch(
            "SELECT day, department_id, model_id, sum(amount_kop) AS amount FROM agg_daily "  # noqa: S608
            f"WHERE source = 'token' AND day BETWEEN $1 AND $2 AND {cond} GROUP BY 1, 2, 3",
            lo,
            agg_hi,
            *args,
        )
        out += [
            Fact(r["day"], r["department_id"], r["model_id"], "token", int(r["amount"]), "RUB") for r in rows
        ]
    for f in out:
        f.original = Decimal(f.amount_kop) / 100
    return out


async def _import_facts(conn: asyncpg.Connection, lo: date, hi: date, dept: DeptFilter) -> list[Fact]:
    cond, args = dept.sql("department_id", 3)
    rows = await conn.fetch(
        "SELECT day, department_id, model, currency, sum(coalesce(amount_kop, 0)) AS amount, "  # noqa: S608
        "sum(amount_orig) AS original, "
        "bool_or(date_trunc('month', day) < date_trunc('month', fetched_at AT TIME ZONE 'Europe/Moscow')) "
        "AS late FROM import_line WHERE status IN ('charged', 'unallocated') AND day BETWEEN $1 AND $2 "
        f"AND (status = 'charged') = (department_id IS NOT NULL) AND {cond} GROUP BY 1, 2, 3, 4",
        lo,
        hi,
        *args,
    )
    return [
        Fact(
            r["day"],
            r["department_id"],
            r["model"],
            "import",
            int(r["amount"]),
            r["currency"],
            r["original"],
            bool(r["late"]),
        )
        for r in rows
    ]


def _months(lo: date, hi: date) -> list[date]:
    out, m = [], month_start(lo)
    while m <= hi:
        out.append(m)
        m = month_end(m) + timedelta(days=1)
    return out


def _pieces(lo: date, hi: date, by_day: bool) -> list[tuple[date, date]]:
    """Окно [lo, hi] по месяцам (или по дням): амортизация считается по кускам внутри месяца."""
    if by_day:
        return [(lo + timedelta(days=i), lo + timedelta(days=i)) for i in range((hi - lo).days + 1)]
    return [(max(lo, m), min(hi, month_end(m))) for m in _months(lo, hi)]


@dataclass(frozen=True, slots=True)
class SubShare:
    """Амортизация одной подписки за кусок окна, отнесённая подразделению (None — «не распределено»)."""

    subscription_id: UUID
    employee_id: UUID | None
    department_id: UUID | None
    day: date
    amount_kop: Kop
    currency: str
    original: Decimal | None


async def subscription_shares(
    conn: asyncpg.Connection,
    snap: ConfigSnapshot,
    lo: date,
    hi: date,
    dept: DeptFilter,
    *,
    by_day: bool = False,
) -> list[SubShare]:
    """Амортизация подписок за [lo, hi] по подразделениям и «не распределено» (правила — `bm.budget.subs`)."""
    if hi < lo or not snap.subscriptions:
        return []
    charged: list[tuple[UUID | None, subs.Charged]] = []
    for department_id in snap.departments:
        if dept.accepts(department_id):
            charged += [
                (department_id, c)
                for c in subs.charged_to(snap.subscriptions, snap.assignments, "department", department_id)
            ]
    if dept.accepts(None):
        charged += [
            (None, subs.Charged(s, ((date.min, None),)))
            for s in snap.subscriptions
            if s.assignee_type == "unallocated" or s.assignee_id is None
        ]
    keys = subs.needed_months([c for _, c in charged], lo, hi)
    totals = await subs.month_totals(conn, keys, snap.fx, store=True)
    out: list[SubShare] = []
    for department_id, c in charged:
        for a, b in _pieces(lo, hi, by_day):
            kop = subs.amount(c, totals, a, b)
            if kop == 0:
                continue
            mt = totals.get((c.sub.id, month_start(a)))
            original: Decimal | None = Decimal(kop) / 100
            if c.sub.currency != "RUB":
                original = None if mt is None or not mt.fx else (Decimal(kop) / 100 / mt.fx).quantize(_CENT)
            out.append(
                SubShare(
                    c.sub.id,
                    c.sub.assignee_id if c.sub.assignee_type == "employee" else None,
                    department_id,
                    a,
                    kop,
                    c.sub.currency,
                    original,
                )
            )
    return out


async def spend_facts(
    conn: asyncpg.Connection, lo: date, hi: date, dept: DeptFilter, now: datetime, *, by_day: bool
) -> list[Fact]:
    """Все суммы расхода за [lo, hi] по трём источникам. Подписки — по сегодня включительно."""
    facts = await _token_facts(conn, lo, hi, dept)
    facts += await _import_facts(conn, lo, hi, dept)
    snap = await load_snapshot(conn, now)
    sub_hi = min(hi, msk_date(now))
    facts += [
        Fact(s.day, s.department_id, None, "subscription", s.amount_kop, s.currency, s.original)
        for s in await subscription_shares(conn, snap, lo, sub_hi, dept, by_day=by_day)
    ]
    return facts


# ---------------------------------------------------------------- отчёт getSpendReport


@dataclass(slots=True)
class _Group:
    amount: Kop = 0
    currencies: set[str] = field(default_factory=set)
    original: Decimal = Decimal(0)
    original_known: bool = True
    late: bool = False

    def add(self, f: Fact) -> None:
        self.amount += f.amount_kop
        if f.currency:
            self.currencies.add(f.currency)
        if f.original is None:
            self.original_known = False
        else:
            self.original += f.original
        self.late = self.late or f.late


def parse_group_by(raw: Iterable[str] | None) -> list[str]:
    """`group_by=department,model` (style=form, explode=false) → список ключей; неизвестный — ValueError."""
    keys: list[str] = []
    for part in raw or ():
        for key in part.split(","):
            key = key.strip()
            if not key:
                continue
            if key not in GROUP_KEYS:
                raise ValueError(key)
            if key not in keys:
                keys.append(key)
    return keys


def _decimal_str(d: Decimal) -> str:
    """Исходная сумма без лишних нулей, но не меньше двух знаков: 2.000000 → 2.00, 10.505 → 10.505."""
    q = d.quantize(_CENT, rounding=ROUND_HALF_UP)
    if q == d:
        return f"{q:f}"
    return f"{d.normalize():f}"


async def department_names(conn: asyncpg.Connection) -> dict[UUID, str]:
    return {r["id"]: r["name"] for r in await conn.fetch("SELECT id, name FROM department")}


def _row_key(f: Fact, keys: Sequence[str]) -> tuple[Any, ...]:
    period: date | None = None
    if "day" in keys:
        period = f.day
    elif "month" in keys:
        period = month_start(f.day)
    return (
        period,
        f.department_id if "department" in keys else None,
        f.model_id if "model" in keys else None,
        f.source if "source" in keys else None,
    )


async def spend_report(
    conn: asyncpg.Connection,
    lo: date,
    hi: date,
    dept: DeptFilter,
    group_by: Sequence[str],
    now: datetime,
) -> tuple[list[dict[str, Any]], Kop]:
    """Строки `SpendRow` и итог. Колонки, по которым нет группировки, — null."""
    facts = await spend_facts(conn, lo, hi, dept, now, by_day="day" in group_by)
    groups: dict[tuple[Any, ...], _Group] = {}
    for f in facts:
        groups.setdefault(_row_key(f, group_by), _Group()).add(f)
    names = await department_names(conn)
    rows: list[dict[str, Any]] = []
    for (period, department_id, model_id, source), g in groups.items():
        single = len(g.currencies) == 1 and g.original_known
        rows.append(
            {
                "period_start": period,
                "department_id": str(department_id) if department_id else None,
                "department_name": names.get(department_id) if department_id else None,
                "model_id": model_id,
                "source": source,
                "amount_rub": kop_to_str(g.amount),
                "original_currency": next(iter(g.currencies)) if single else None,
                "original_amount": _decimal_str(g.original) if single else None,
                "late": g.late,
            }
        )
    rows.sort(
        key=lambda r: (
            r["period_start"] or date.min,
            r["department_name"] or "",
            r["model_id"] or "",
            r["source"] or "",
        )
    )
    return rows, sum(f.amount_kop for f in facts)


# ---------------------------------------------------------------- CSV getSpendCsv


def csv_cell(value: str) -> str:
    """Защита от CSV-инъекции (OWASP): значение, которое табличный редактор принял бы за формулу, — с `'`."""
    if value.startswith(_CSV_DANGEROUS):
        return "'" + value
    return value


async def spend_csv(conn: asyncpg.Connection, lo: date, hi: date, dept: DeptFilter, now: datetime) -> bytes:
    """CSV FR-028: UTF-8 с BOM, `;`, строка на (месяц, подразделение, модель, источник, валюта)."""
    facts = await spend_facts(conn, lo, hi, dept, now, by_day=False)
    groups: dict[tuple[Any, ...], _Group] = {}
    for f in facts:
        key = (month_start(f.day), f.department_id, f.model_id or "", f.source, f.currency or "")
        groups.setdefault(key, _Group()).add(f)
    names = await department_names(conn)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    ordered = sorted(
        groups.items(), key=lambda kv: (kv[0][0], names.get(kv[0][1], UNALLOCATED_RU), kv[0][2], kv[0][3])
    )
    for (month, department_id, model_id, source, currency), g in ordered:
        department = names.get(department_id, "") if department_id else UNALLOCATED_RU
        writer.writerow(
            (
                max(month, lo).isoformat(),
                csv_cell(department),
                csv_cell(model_id),
                csv_cell(source),
                kop_to_str(g.amount),
                currency,
                _decimal_str(g.original) if g.original_known else "",
            )
        )
    return "﻿".encode() + buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------- сверка getReconciliation


def _pct(part: Kop, whole: Kop) -> str | None:
    if whole == 0:
        return None
    value = (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{value:.1f}"


async def reconciliation(conn: asyncpg.Connection, month: date) -> list[dict[str, Any]]:
    """SC-005: по источникам `mode=decision_api` — импорт провайдера против событий ключей, связанных с
    источником.

    Сутки импорта — UTC (ADR-004), поэтому и события берутся по UTC-суткам месяца (`created_at`).
    """
    lo, hi = month, month_end(month)
    rows = await conn.fetch(
        "WITH src AS (SELECT id, provider, ref, department_id FROM provider_source WHERE mode = "
        "'decision_api'), "
        "imp AS (SELECT l.provider, l.source_ref, sum(coalesce(l.amount_kop, 0)) AS kop FROM import_line l "
        "  WHERE l.day BETWEEN $1 AND $2 GROUP BY 1, 2), "
        "ev AS (SELECT k.provider_source_id AS sid, sum(r.fact_kop) AS kop FROM call_record r "
        "  JOIN caller_system k ON k.id = r.caller_id "
        "  WHERE k.provider_source_id IS NOT NULL AND r.fact_kop IS NOT NULL "
        "  AND r.status IN ('settled', 'expired') "
        "  AND r.created_at >= ($1::date)::timestamp AT TIME ZONE 'UTC' "
        "  AND r.created_at < (($2::date + 1))::timestamp AT TIME ZONE 'UTC' GROUP BY 1) "
        "SELECT s.provider, s.ref, s.department_id, coalesce(imp.kop, 0) AS imported, "
        "coalesce(ev.kop, 0) AS events FROM src s "
        "LEFT JOIN imp ON imp.provider = s.provider AND imp.source_ref = s.ref "
        "LEFT JOIN ev ON ev.sid = s.id ORDER BY s.provider, s.ref",
        lo,
        hi,
    )
    out = []
    for r in rows:
        imported, events = int(r["imported"]), int(r["events"])
        diff = imported - events
        out.append(
            {
                "month": f"{month:%Y-%m}",
                "provider": r["provider"],
                "source_ref": r["ref"],
                "department_id": r["department_id"],
                "imported_rub": kop_to_str(imported),
                "decision_api_rub": kop_to_str(events),
                "diff_rub": kop_to_str(diff),
                "diff_pct": _pct(diff, imported),
                "coverage_pct": _pct(min(events, imported), imported),
            }
        )
    return out


# ---------------------------------------------------------------- журнал событий listEvents


@dataclass(frozen=True, slots=True)
class EventQuery:
    lo: date
    hi: date
    department_id: UUID | None = None
    outcome: str | None = None
    code: str | None = None
    flag: str | None = None


async def list_events(
    conn: asyncpg.Connection, q: EventQuery, *, page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    """Журнал решений и расхода по моменту записи (`created_at`, сутки МСК), новые сверху. Только "
    "метаданные."""
    where = ["r.created_at >= $1", "r.created_at < $2"]
    args: list[Any] = [_msk_midnight(q.lo), _msk_midnight(q.hi + timedelta(days=1))]
    for column, value in (("r.department_id", q.department_id), ("r.outcome", q.outcome), ("r.code", q.code)):
        if value is not None:
            args.append(value)
            where.append(f"{column} = ${len(args)}")
    if q.flag is not None:
        args.append(q.flag)
        where.append(f"${len(args)} = ANY(r.flags)")
    cond = " AND ".join(where)
    total: int = await conn.fetchval(f"SELECT count(*) FROM call_record r WHERE {cond}", *args)  # noqa: S608
    args += [page_size, (page - 1) * page_size]
    rows = await conn.fetch(
        "SELECT r.*, k.name AS caller_name, e.pseudonym FROM call_record r "  # noqa: S608
        "LEFT JOIN caller_system k ON k.id = r.caller_id LEFT JOIN employee e ON e.id = r.employee_id "
        f"WHERE {cond} ORDER BY r.created_at DESC, r.id DESC LIMIT ${len(args) - 1} OFFSET ${len(args)}",
        *args,
    )
    return [_event(r) for r in rows], total


def _event(r: asyncpg.Record) -> dict[str, Any]:
    tokens = None
    if r["tokens_in"] is not None:
        tokens = {"input": r["tokens_in"], "output": r["tokens_out"] or 0, "cached": r["tokens_cached"] or 0}
    return {
        "id": r["id"],
        "created_at": r["created_at"].astimezone(MSK),
        "caller_name": r["caller_name"] or "",
        "subject_pseudonym": r["pseudonym"],
        "department_id": r["department_id"],
        "requested_model": r["requested_model"],
        "final_model": r["final_model"],
        "outcome": r["outcome"],
        "code": r["code"],
        "reserve_rub": kop_to_str(r["reserve_kop"]),
        "fact_rub": None if r["fact_kop"] is None else kop_to_str(r["fact_kop"]),
        "tokens": tokens,
        "status": r["status"],
        "flags": list(r["flags"]),
        "expense_date": r["expense_date"],
    }


# ---------------------------------------------------------------- журнал аудита listAudit


def _json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


async def list_audit(
    conn: asyncpg.Connection,
    lo: date,
    hi: date,
    *,
    object_type: str | None,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    """Журнал аудита за сутки МСК [lo, hi], новые сверху."""
    where = ["at >= $1", "at < $2"]
    args: list[Any] = [_msk_midnight(lo), _msk_midnight(hi + timedelta(days=1))]
    if object_type is not None:
        args.append(object_type)
        where.append(f"object_type = ${len(args)}")
    cond = " AND ".join(where)
    total: int = await conn.fetchval(f"SELECT count(*) FROM audit_log WHERE {cond}", *args)  # noqa: S608
    args += [page_size, (page - 1) * page_size]
    rows = await conn.fetch(
        "SELECT id, at, actor, action, object_type, object_id, before, after FROM audit_log "  # noqa: S608
        f"WHERE {cond} ORDER BY at DESC, id DESC LIMIT ${len(args) - 1} OFFSET ${len(args)}",
        *args,
    )
    return [
        {
            "id": r["id"],
            "at": r["at"].astimezone(MSK),
            "actor": r["actor"],
            "action": r["action"],
            "object_type": r["object_type"],
            "object_id": r["object_id"],
            "before": _json(r["before"]),
            "after": _json(r["after"]),
        }
        for r in rows
    ], total


# ---------------------------------------------------------------- прослеживаемость listDepartmentBalanceItems


def period_is_current(kind: PeriodKind, at: datetime, now: datetime) -> bool:
    """Период момента `at` ещё идёт (резервы не привязаны к периоду и уменьшают только текущие)."""
    return bounds(kind, at).end >= msk_date(now)


async def balance_items(
    conn: asyncpg.Connection,
    department_id: UUID,
    kind: PeriodKind,
    at: datetime,
    now: datetime,
    *,
    source: str | None,
    show_subjects: bool,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    """Строки, из которых складывается расход и резервы подразделения за период (FR-026).

    Сумма строк `token` = токенный расход остатка, `import` = импорт, `subscription` = амортизация,
    `reservation` = открытые резервы (только для текущего периода).
    """
    p = bounds(kind, at)
    lo, hi = p.start, msk_date(at)
    wanted = {source} if source else {"token", "reservation", "import", "subscription"}
    parts: list[str] = []
    args: list[Any] = [department_id, lo, hi, _msk_midnight(lo)]
    if "token" in wanted:
        parts.append(
            "SELECT 'token' AS source, r.id::text AS ref_id, r.expense_date AS day, "
            "coalesce(r.final_model, r.requested_model) AS model_id, r.employee_id, r.fact_kop AS kop, "
            "r.flags "
            "FROM call_record r, p WHERE r.department_id = p.dep AND r.fact_kop IS NOT NULL "
            "AND r.status IN ('settled', 'expired') AND r.expense_date BETWEEN p.lo AND p.hi AND "
            "r.created_at >= p.ts"
        )
    if "reservation" in wanted and period_is_current(kind, at, now):
        parts.append(
            "SELECT 'reservation' AS source, r.id::text AS ref_id, r.expense_date AS day, r.final_model AS "
            "model_id, "
            "r.employee_id, r.reserve_kop AS kop, r.flags FROM call_record r, p WHERE r.department_id = "
            "p.dep AND r.status = 'open'"
        )
    if "import" in wanted:
        parts.append(
            "SELECT 'import' AS source, l.provider || ':' || l.source_ref || ':' || l.model || ':' || "
            "l.day::text "
            "AS ref_id, l.day AS day, l.model AS model_id, NULL::uuid AS employee_id, "
            "coalesce(l.amount_kop, 0) AS kop, "
            "'{}'::text[] AS flags FROM import_line l, p "
            "WHERE l.department_id = p.dep AND l.status = 'charged' AND l.day BETWEEN p.lo AND p.hi"
        )
    # Все параметры объявлены в CTE: часть UNION может не использовать некоторые из них.
    params_cte = "WITH p AS (SELECT $1::uuid AS dep, $2::date AS lo, $3::date AS hi, $4::timestamptz AS ts) "
    sql_total = 0
    rows: list[asyncpg.Record] = []
    offset = (page - 1) * page_size
    if parts:
        union = " UNION ALL ".join(parts)
        sql_total = await conn.fetchval(f"{params_cte}SELECT count(*) FROM ({union}) x", *args)  # noqa: S608
        rows = await conn.fetch(
            f"{params_cte}SELECT x.*, e.pseudonym FROM ({union}) x "  # noqa: S608
            "LEFT JOIN employee e ON e.id = x.employee_id "
            "ORDER BY x.day, x.source, x.ref_id LIMIT $5 OFFSET $6",
            *args,
            page_size,
            offset,
        )
    sub_items: list[dict[str, Any]] = []
    if "subscription" in wanted:
        snap = await load_snapshot(conn, now)
        per_sub: dict[UUID, tuple[date, UUID | None, Kop]] = {}
        for s in await subscription_shares(conn, snap, lo, hi, DeptFilter("one", department_id)):
            first, emp, kop = per_sub.get(s.subscription_id, (s.day, s.employee_id, 0))
            per_sub[s.subscription_id] = (min(first, s.day), emp, kop + s.amount_kop)
        pseudonyms: dict[UUID, str] = {}
        emp_ids = [e for _, e, _ in per_sub.values() if e is not None]
        if emp_ids and show_subjects:
            pseudonyms = {
                r["id"]: r["pseudonym"]
                for r in await conn.fetch("SELECT id, pseudonym FROM employee WHERE id = ANY($1)", emp_ids)
            }
        for sid, (first, emp, kop) in sorted(per_sub.items(), key=lambda kv: (kv[1][0], str(kv[0]))):
            sub_items.append(
                {
                    "source": "subscription",
                    "ref_id": str(sid),
                    "date": first,
                    "model_id": None,
                    "subject_pseudonym": pseudonyms.get(emp) if emp else None,
                    "amount_rub": kop_to_str(kop),
                    "flags": [],
                }
            )
    items = [
        {
            "source": r["source"],
            "ref_id": r["ref_id"],
            "date": r["day"],
            "model_id": r["model_id"],
            "subject_pseudonym": r["pseudonym"] if show_subjects else None,
            "amount_rub": kop_to_str(r["kop"]),
            "flags": list(r["flags"]),
        }
        for r in rows
    ]
    # Подписки — после строк из БД: страница может захватить конец одних и начало других.
    sub_from = max(0, offset - sql_total)
    room = page_size - len(items)
    if room > 0:
        items += sub_items[sub_from : sub_from + room]
    return items, sql_total + len(sub_items)


# ---------------------------------------------------------------- остаток подразделения getDepartmentBalance


async def department_balance(budget: Budget, department_id: UUID, at: datetime | None) -> BalanceReport:
    """Остаток подразделения по правилам `Budget.balance` (те же, что у getCallerBalance и порогов).

    Для момента `at` в прошлом периоды, которые уже закончились, показываются без открытых резервов:
    резерв не привязан к периоду и уменьшает только текущие (12_plan.md §7, «Остаток»).
    """
    report = await budget.balance(OwnerRef("department", department_id), at)
    if at is None:
        return report
    today = msk_date(budget.now())
    limits: list[LimitBalance] = []
    for lb in report.limits:
        if bounds(lb.period, at).end < today and lb.reserved_rub != kop_to_str(0):
            remaining = str_to_kop(lb.remaining_rub) + str_to_kop(lb.reserved_rub)
            lb = lb.model_copy(update={"reserved_rub": kop_to_str(0), "remaining_rub": kop_to_str(remaining)})
        limits.append(lb)
    return report.model_copy(update={"limits": limits})
