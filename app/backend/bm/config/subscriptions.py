"""Подписки с фиксированной платой (FR-017–FR-020, сценарий 4, T051): список, карточка, ввод, предпросмотр
доли, закрытие датой и удаление.

Правила (ADR-003, 12_arch_decision.md §6 п. 1 и п. 10):
- денежные поля и назначение неизменяемы (триггер `subscription_immutable`): изменить подписку = закрыть
  датой и создать новую. PATCH принимает только `end_date`;
- закрыть можно не раньше сегодняшнего дня (МСК): амортизация прошедших дней сохраняется (4.5);
- удаление = закрытие сегодняшней датой; подписку, которая ещё не началась, удаление стирает целиком;
- USD без единого введённого курса — `422 fx_rate_missing`;
- M месяца фиксируется в `subscription_month` при сохранении (по месяц сегодняшнего дня) — курс, изменённый
  задним числом, её не меняет; дальше — при первом расчёте месяца (`bm.budget.subs`);
- после сохранения доля подписок проверяется сразу: при доле ≥ порога — уведомление `subs_share` (4.4).

Все изменения — через `config.apply` (аудит «было/стало» и `config_version`).
"""

import uuid
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from bm.budget import subs
from bm.budget.snapshot import SubscriptionCfg
from bm.budget.snapshot import load as load_snapshot
from bm.config.catalog import price_str
from bm.config.errors import Invalid, NotFound
from bm.config.service import Applied, Change, apply
from bm.domain.money import kop_to_str
from bm.domain.periods import MSK, month_end, month_start, msk_date

_SELECT = (
    "SELECT s.id, s.provider, s.plan, s.seat_price, s.currency, s.seats, s.start_date, s.end_date, "
    "s.assignee_type, s.assignee_id, s.created_at, e.pseudonym, "
    "CASE s.assignee_type WHEN 'department' THEN s.assignee_id "
    "     WHEN 'employee' THEN a.department_id END AS department_id "
    "FROM subscription s "
    "LEFT JOIN employee e ON s.assignee_type = 'employee' AND e.id = s.assignee_id "
    "LEFT JOIN employee_assignment a ON a.employee_id = e.id AND a.valid_to IS NULL"
)


def _cfg(r: Mapping[str, Any]) -> SubscriptionCfg:
    return SubscriptionCfg(
        id=r["id"],
        provider=r["provider"],
        plan=r["plan"],
        seat_price=r["seat_price"],
        currency=r["currency"],
        seats=r["seats"],
        start_date=r["start_date"],
        end_date=r["end_date"],
        assignee_type=r["assignee_type"],
        assignee_id=r["assignee_id"],
    )


def reference_month(sub: SubscriptionCfg, today: date) -> date:
    """Месяц, M которого показывается в карточке: текущий; до начала — первый; после конца — последний."""
    day = max(today, sub.start_date)
    if sub.end_date is not None:
        day = min(day, sub.end_date)
    return month_start(day)


async def _fx(conn: asyncpg.Connection) -> dict[date, Decimal]:
    return {r["month"]: r["usd_rub"] for r in await conn.fetch("SELECT month, usd_rub FROM fx_rate")}


async def _out(conn: asyncpg.Connection, r: asyncpg.Record, today: date) -> dict[str, Any]:
    sub = _cfg(r)
    month = reference_month(sub, today)
    totals = await subs.month_totals(conn, [(sub, month)], await _fx(conn), store=False)
    mt = totals.get((sub.id, month))
    ref: str | None = None
    if sub.assignee_type == "employee":
        ref = r["pseudonym"]
    elif sub.assignee_type == "department":
        ref = str(sub.assignee_id)
    return {
        "id": str(sub.id),
        "provider": sub.provider,
        "plan": sub.plan,
        "seat_price": price_str(sub.seat_price),
        "currency": sub.currency,
        "seats": sub.seats,
        "start_date": sub.start_date.isoformat(),
        "end_date": None if sub.end_date is None else sub.end_date.isoformat(),
        "assignee": {"type": sub.assignee_type, "ref": ref},
        "monthly_total_rub": None if mt is None else kop_to_str(mt.m_total_kop),
        "fx_warning": "no_rate" if mt is None else mt.fx_warning,
        "created_at": r["created_at"].astimezone(MSK).isoformat(),
    }


async def _row(conn: asyncpg.Connection, subscription_id: UUID, *, lock: bool = False) -> asyncpg.Record:
    row = await conn.fetchrow(
        f"{_SELECT} WHERE s.id = $1 AND s.deleted_at IS NULL" + (" FOR UPDATE OF s" if lock else ""),
        subscription_id,
    )
    if row is None:
        raise NotFound("Подписка не найдена.")
    return row


# ---------------------------------------------------------------- чтение


async def list_subscriptions(
    conn: asyncpg.Connection,
    *,
    department_id: UUID | None,
    active_on: date | None,
    page: int,
    page_size: int,
    now: datetime,
) -> tuple[list[dict[str, Any]], int]:
    """`department_id` — подписки подразделения и его сотрудников (по текущему назначению)."""
    where, args = ["s.deleted_at IS NULL"], []  # deleted_at — строка вне учёта (как в снимке, T018)
    if department_id is not None:
        args.append(department_id)
        where.append(
            f"((s.assignee_type = 'department' AND s.assignee_id = ${len(args)}) "
            f"OR (s.assignee_type = 'employee' AND a.department_id = ${len(args)}))"
        )
    if active_on is not None:
        args.append(active_on)
        n = len(args)
        where.append(f"s.start_date <= ${n} AND (s.end_date IS NULL OR s.end_date >= ${n})")
    cond = " AND ".join(where)
    total: int = await conn.fetchval(f"SELECT count(*) FROM ({_SELECT} WHERE {cond}) t", *args)  # noqa: S608
    args += [page_size, (page - 1) * page_size]
    rows = await conn.fetch(
        f"{_SELECT} WHERE {cond} ORDER BY s.start_date DESC, s.created_at DESC, s.id "
        f"LIMIT ${len(args) - 1} OFFSET ${len(args)}",
        *args,
    )
    today = msk_date(now)
    return [await _out(conn, r, today) for r in rows], total


async def get_subscription(
    conn: asyncpg.Connection, subscription_id: UUID, now: datetime
) -> tuple[dict[str, Any], UUID | None]:
    """(подписка, подразделение для проверки прав: назначения или текущее подразделение сотрудника)."""
    row = await _row(conn, subscription_id)
    return await _out(conn, row, msk_date(now)), row["department_id"]


# ---------------------------------------------------------------- ввод и предпросмотр


async def _assignee(conn: asyncpg.Connection, assignee: Mapping[str, Any]) -> tuple[str, UUID | None]:
    kind, ref = assignee["type"], assignee.get("ref")
    if kind == "unallocated":
        if ref is not None:
            raise Invalid("Для подписки «не распределено» поле assignee.ref должно быть пустым.")
        return kind, None
    if not ref:
        raise Invalid("Укажите, на кого назначена подписка: assignee.ref.")
    if kind == "employee":
        emp_id: UUID | None = await conn.fetchval("SELECT id FROM employee WHERE pseudonym = $1", ref)
        if emp_id is None:
            raise Invalid("Сотрудник с таким псевдонимом не найден.")
        return kind, emp_id
    try:
        dep = UUID(str(ref))
    except ValueError as exc:
        raise Invalid("assignee.ref подразделения — UUID подразделения.") from exc
    if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM department WHERE id = $1)", dep):
        raise Invalid("Подразделение не найдено.")
    return kind, dep


def _draft(body: Mapping[str, Any], assignee_type: str, assignee_id: UUID | None) -> SubscriptionCfg:
    start: date = body["start_date"]
    end: date | None = body.get("end_date")
    if end is not None and end < start:
        raise Invalid("Дата окончания раньше даты начала.")
    return SubscriptionCfg(
        id=uuid.uuid4(),
        provider=body["provider"],
        plan=body["plan"],
        seat_price=Decimal(body["seat_price"]),
        currency=body["currency"],
        seats=body["seats"],
        start_date=start,
        end_date=end,
        assignee_type=assignee_type,
        assignee_id=assignee_id,
    )


async def _department_of(conn: asyncpg.Connection, sub: SubscriptionCfg) -> UUID | None:
    if sub.assignee_type == "department":
        return sub.assignee_id
    if sub.assignee_type == "employee":
        dep: UUID | None = await conn.fetchval(
            "SELECT department_id FROM employee_assignment WHERE employee_id = $1 AND valid_to IS NULL",
            sub.assignee_id,
        )
        return dep
    return None


async def preview(
    conn: asyncpg.Connection, body: Mapping[str, Any], now: datetime, *, default_warn_pct: int
) -> dict[str, Any]:
    """Доля бюджета подразделения, которую займут его подписки вместе с новой (FR-020). Ничего не пишет."""
    kind, ref = await _assignee(conn, body["assignee"])
    draft = _draft(body, kind, ref)
    today = msk_date(now)
    snap = await load_snapshot(conn, now)
    month = reference_month(draft, today)
    fx_warning = "no_rate" if (mt := subs.compute_month(draft, month, snap.fx)) is None else mt.fx_warning
    dep = await _department_of(conn, draft)
    dept = snap.departments.get(dep) if dep is not None else None
    threshold = dept.subs_warn_pct if dept is not None else default_warn_pct
    if dept is not None:
        shares = await subs.department_shares(conn, snap, dept.id, today, extra=draft, store=False)
    else:  # «не распределено» (или сотрудник без подразделения): только сама подписка, лимита нет
        shares = subs.standalone_shares(draft, today, snap.fx)
    return {
        "department_id": None if dept is None else str(dept.id),
        "periods": [
            {
                "period": s.period.value,
                "limit_rub": None if s.limit is None else kop_to_str(s.limit),
                "amortization_rub": kop_to_str(s.amort),
                "share_pct": s.pct(),
            }
            for s in shares
        ],
        "warn_threshold_pct": threshold,
        "warn": any(s.reaches(threshold) for s in shares),
        "fx_warning": fx_warning,
    }


async def create(
    conn: asyncpg.Connection, actor: str, body: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    today = msk_date(now)

    async def run(c: asyncpg.Connection) -> Applied:
        kind, ref = await _assignee(c, body["assignee"])
        draft = _draft(body, kind, ref)
        if draft.currency == "USD" and not await c.fetchval("SELECT EXISTS (SELECT 1 FROM fx_rate)"):
            raise Invalid(
                "Нет курса USD/₽: подписку в долларах нельзя списать, пока не введён курс месяца "
                "в разделе «Курсы».",
                code="fx_rate_missing",
            )
        sub_id: UUID = await c.fetchval(
            "INSERT INTO subscription(provider, plan, seat_price, currency, seats, start_date, end_date, "
            "assignee_type, assignee_id, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
            "RETURNING id",
            draft.provider,
            draft.plan,
            draft.seat_price,
            draft.currency,
            draft.seats,
            draft.start_date,
            draft.end_date,
            kind,
            ref,
            now,
        )
        row = await _row(c, sub_id)
        sub = _cfg(row)
        # M фиксируется по месяц сегодняшнего дня (и за прошедшие месяцы, если подписка началась раньше)
        last = min(month_end(today), sub.end_date or month_end(today))
        if sub.start_date <= last:
            months = subs.needed_months([subs.Charged(sub, ((date.min, None),))], sub.start_date, last)
            await subs.month_totals(c, months, await _fx(c), store=True)
        after = await _out(c, row, today)
        dep = row["department_id"]
        if dep is not None:
            await subs.notify_shares(c, await load_snapshot(c, now), dep, today)
        return Applied(object_id=str(sub_id), after=after, result=after)

    applied = await apply(conn, Change("create", "subscription", run), actor, at=now)
    result: dict[str, Any] = applied.result
    return result


# ---------------------------------------------------------------- закрытие и удаление


async def end(
    conn: asyncpg.Connection, actor: str, subscription_id: UUID, end_date: date, now: datetime
) -> dict[str, Any]:
    today = msk_date(now)

    async def run(c: asyncpg.Connection) -> Applied:
        row = await _row(c, subscription_id, lock=True)
        before = await _out(c, row, today)
        if row["end_date"] is not None and row["end_date"] < today:
            raise Invalid("Подписка уже закончилась: изменить можно только действующую подписку.")
        if end_date < row["start_date"]:
            raise Invalid("Дата окончания раньше даты начала подписки.")
        if end_date < today:
            raise Invalid(
                "Закрыть подписку можно не раньше сегодняшнего дня: амортизация прошедших дней сохраняется."
            )
        await c.execute("UPDATE subscription SET end_date = $2 WHERE id = $1", subscription_id, end_date)
        after = await _out(c, await _row(c, subscription_id), today)
        return Applied(object_id=str(subscription_id), before=before, after=after, result=after)

    applied = await apply(conn, Change("end", "subscription", run), actor, at=now)
    result: dict[str, Any] = applied.result
    return result


async def delete(conn: asyncpg.Connection, actor: str, subscription_id: UUID, now: datetime) -> None:
    """Удалить = закрыть сегодняшней датой (4.5). Ещё не начавшаяся подписка удаляется целиком."""
    today = msk_date(now)

    async def run(c: asyncpg.Connection) -> Applied:
        row = await _row(c, subscription_id, lock=True)
        before = await _out(c, row, today)
        if row["start_date"] > today:
            await c.execute("DELETE FROM subscription_month WHERE subscription_id = $1", subscription_id)
            await c.execute("DELETE FROM subscription WHERE id = $1", subscription_id)
            return Applied(object_id=str(subscription_id), before=before, after=None)
        end_date = today if row["end_date"] is None else min(row["end_date"], today)
        # deleted_at не ставится: снимок не видит подписки с deleted_at (T018), а прошедшие дни должны
        # остаться в остатке. Удаление видно в аудите (action=delete).
        await c.execute("UPDATE subscription SET end_date = $2 WHERE id = $1", subscription_id, end_date)
        after = await _out(c, await _row(c, subscription_id), today)
        return Applied(object_id=str(subscription_id), before=before, after=after)

    await apply(conn, Change("delete", "subscription", run), actor, at=now)
