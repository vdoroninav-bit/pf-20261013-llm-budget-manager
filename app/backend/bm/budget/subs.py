"""Подписки в остатке (FR-018, FR-020, ADR-003): чьи подписки, M месяца, амортизация окна, доля бюджета.

- **Кому списывается.** Подписка сотрудника — ему целиком и его подразделению по отрезкам назначения
  (`employee_assignment`); подписка подразделения — подразделению; «не распределено» — никому (4.6).
- **M месяца** берётся только из `subscription_month`. Нет строки — M считается по курсу (месяца, иначе
  последнему введённому с `last_known_rate_used`) и фиксируется при первом расчёте (`store=True`): курс,
  изменённый задним числом, уже амортизированное не меняет. Курса нет совсем — месяц не списывается.
- **Окно** — дни периода по сегодня включительно (остаток) или весь период (доля подписок, FR-020: иначе
  предупреждение «при наступлении нового периода» невозможно — в первый день доля ничтожна).

Расчёт дней — чистая функция `bm.domain.amortize.amortized`; здесь только выбор подписок и строки БД.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import asyncpg

from bm.budget.snapshot import Assignment, ConfigSnapshot, SubscriptionCfg
from bm.budget.snapshot import load as load_snapshot
from bm.domain.amortize import amortized, month_total
from bm.domain.money import kop_to_str
from bm.domain.periods import MSK, bounds, month_end, month_start
from bm.domain.types import Kop, PeriodKind
from bm.notify import service as notify

type MonthKey = tuple[UUID, date]  # (подписка, 1-е число месяца)

PERIODS = (PeriodKind.DAY, PeriodKind.WEEK, PeriodKind.MONTH)
PERIOD_RU = {PeriodKind.DAY: "день", PeriodKind.WEEK: "неделю", PeriodKind.MONTH: "месяц"}


@dataclass(frozen=True, slots=True)
class MonthTotal:
    """M месяца: сумма подписки за полный месяц, курс и предупреждение о курсе."""

    m_total_kop: Kop
    fx: Decimal | None  # None — подписка в рублях
    fx_warning: str | None  # None | last_known_rate_used


@dataclass(frozen=True, slots=True)
class Charged:
    """Подписка владельца и дни, когда она ему относится (отрезки включительно; конец None — открыт)."""

    sub: SubscriptionCfg
    segments: tuple[tuple[date, date | None], ...]


_ALWAYS: tuple[tuple[date, date | None], ...] = ((date.min, None),)


# ---------------------------------------------------------------- курс и M


def rate_for(fx: Mapping[date, Decimal], month: date) -> tuple[Decimal | None, str | None]:
    """Курс USD месяца; нет — последний введённый до него (`last_known_rate_used`); нет вовсе — `no_rate`."""
    if month in fx:
        return fx[month], None
    known = [m for m in fx if m <= month]
    if known:
        return fx[max(known)], "last_known_rate_used"
    return None, "no_rate"


def compute_month(sub: SubscriptionCfg, month: date, fx: Mapping[date, Decimal]) -> MonthTotal | None:
    """M месяца без записи в БД. None — подписка в USD, а курса нет ни одного."""
    if sub.currency == "RUB":
        return MonthTotal(month_total(sub.seats, sub.seat_price, Decimal(1)), None, None)
    rate, warning = rate_for(fx, month)
    if rate is None:
        return None
    return MonthTotal(month_total(sub.seats, sub.seat_price, rate), rate, warning)


async def month_totals(
    conn: asyncpg.Connection,
    keys: Iterable[tuple[SubscriptionCfg, date]],
    fx: Mapping[date, Decimal],
    *,
    store: bool,
) -> dict[MonthKey, MonthTotal]:
    """M по (подписка, месяц): из `subscription_month`, недостающие — по курсу.

    `store=True` фиксирует недостающие в `subscription_month` (первый расчёт месяца). При гонке побеждает
    первая запись (ON CONFLICT DO NOTHING), и все читают её. `store=False` — предпросмотр, без записи.
    """
    wanted = {(s.id, m): s for s, m in keys}
    if not wanted:
        return {}
    ids, months = zip(*wanted, strict=True)
    out: dict[MonthKey, MonthTotal] = {}
    for r in await conn.fetch(
        "SELECT subscription_id, month, m_total_kop, fx, fx_warning FROM subscription_month "
        "WHERE (subscription_id, month) IN (SELECT * FROM unnest($1::uuid[], $2::date[]))",
        list(ids),
        list(months),
    ):
        out[(r["subscription_id"], r["month"])] = MonthTotal(r["m_total_kop"], r["fx"], r["fx_warning"])
    for key, sub in wanted.items():
        if key in out:
            continue
        mt = compute_month(sub, key[1], fx)
        if mt is None:
            continue
        if store:
            row = await conn.fetchrow(
                "INSERT INTO subscription_month(subscription_id, month, m_total_kop, fx, fx_warning) "
                "VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING RETURNING m_total_kop, fx, fx_warning",
                key[0],
                key[1],
                mt.m_total_kop,
                mt.fx,
                mt.fx_warning,
            )
            if row is None:  # параллельная транзакция зафиксировала M раньше
                row = await conn.fetchrow(
                    "SELECT m_total_kop, fx, fx_warning FROM subscription_month "
                    "WHERE subscription_id = $1 AND month = $2",
                    key[0],
                    key[1],
                )
            if row is not None:
                mt = MonthTotal(row["m_total_kop"], row["fx"], row["fx_warning"])
        out[key] = mt
    return out


# ---------------------------------------------------------------- чьи подписки и за какие дни


def charged_to(
    subs: Iterable[SubscriptionCfg], assignments: Sequence[Assignment], owner_type: str, owner_id: UUID
) -> list[Charged]:
    """Подписки, которые списываются из бюджета владельца (FR-018)."""
    out: list[Charged] = []
    for s in subs:
        if s.assignee_type == "unallocated" or s.assignee_id is None:
            continue
        if s.assignee_type == owner_type and s.assignee_id == owner_id:
            out.append(Charged(s, _ALWAYS))
        elif owner_type == "department" and s.assignee_type == "employee":
            segments = tuple(
                (a.valid_from, a.valid_to)
                for a in assignments
                if a.employee_id == s.assignee_id and a.department_id == owner_id
            )
            if segments:
                out.append(Charged(s, segments))
    return out


def _months(lo: date, hi: date) -> list[date]:
    out, m = [], month_start(lo)
    while m <= hi:
        out.append(m)
        m = month_end(m) + timedelta(days=1)
    return out


def needed_months(charged: Iterable[Charged], lo: date, hi: date) -> set[tuple[SubscriptionCfg, date]]:
    """Месяцы окна [lo, hi], в которые подписка действует: для них нужна M."""
    out: set[tuple[SubscriptionCfg, date]] = set()
    for c in charged:
        s = c.sub
        a = max(lo, s.start_date)
        b = hi if s.end_date is None else min(hi, s.end_date)
        if a <= b:
            out.update((s, m) for m in _months(a, b))
    return out


def amount(c: Charged, totals: Mapping[MonthKey, MonthTotal], lo: date, hi: date) -> Kop:
    """Амортизация подписки владельцу за дни окна [lo, hi] (включительно), по месяцам и отрезкам."""
    s = c.sub
    total = 0
    for month in _months(lo, hi):
        mt = totals.get((s.id, month))
        if mt is None:
            continue
        w_lo, w_hi = max(lo, month), min(hi, month_end(month))
        for seg_from, seg_to in c.segments:
            a, b = max(w_lo, seg_from), w_hi if seg_to is None else min(w_hi, seg_to)
            if a <= b:
                total += amortized(mt.m_total_kop, month, s.start_date, s.end_date, (a, b))
    return total


async def window_amounts(
    conn: asyncpg.Connection,
    snap: ConfigSnapshot,
    windows: Mapping[tuple[str, UUID, PeriodKind], tuple[date, date]],
    *,
    cache: dict[MonthKey, MonthTotal] | None = None,
) -> dict[tuple[str, UUID, PeriodKind], Kop]:
    """Амортизация подписок по окнам (владелец, период) → копейки. M фиксируется при первом расчёте.

    `cache` — M, уже прочитанные процессом (они неизменяемы): горячий путь не ходит в БД повторно.
    Без подписок у владельцев — ни одного запроса.
    """
    charged = {(ot, oid): charged_to(snap.subscriptions, snap.assignments, ot, oid) for ot, oid, _ in windows}
    keys: set[tuple[SubscriptionCfg, date]] = set()
    for (ot, oid, _), (lo, hi) in windows.items():
        keys |= needed_months(charged[(ot, oid)], lo, hi)
    known = cache if cache is not None else {}
    missing = [(s, m) for s, m in keys if (s.id, m) not in known]
    totals = dict(known)
    if missing:
        fresh = await month_totals(conn, missing, snap.fx, store=True)
        totals.update(fresh)
        if cache is not None:
            cache.update(fresh)
    return {
        (ot, oid, k): sum(amount(c, totals, lo, hi) for c in charged[(ot, oid)])
        for (ot, oid, k), (lo, hi) in windows.items()
    }


# ---------------------------------------------------------------- доля подписок (FR-020)


@dataclass(frozen=True, slots=True)
class PeriodShare:
    period: PeriodKind
    start: date
    limit: Kop | None
    amort: Kop  # все подписки владельца за весь текущий период

    def pct(self) -> str | None:
        if self.limit is None:
            return None
        return pct_str(self.amort, self.limit)

    def reaches(self, threshold_pct: int) -> bool:
        """Доля ≥ порога (сравнение без округления); лимит 0 — любая ненулевая амортизация."""
        if self.limit is None or self.amort <= 0:
            return False
        return self.amort * 100 >= threshold_pct * self.limit


def pct_str(part: Kop, whole: Kop) -> str:
    """Процент с одним знаком, ROUND_HALF_UP. Лимит 0: 0.0 без суммы, иначе 100.0."""
    if whole == 0:
        return "0.0" if part == 0 else "100.0"
    value = (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{value:.1f}"


def noon(day: date) -> datetime:
    return datetime.combine(day, time(12), tzinfo=MSK)


async def department_shares(
    conn: asyncpg.Connection,
    snap: ConfigSnapshot,
    department_id: UUID,
    today: date,
    *,
    extra: SubscriptionCfg | None = None,
    store: bool,
) -> list[PeriodShare]:
    """Доля подписок подразделения в его лимитах за текущие периоды (весь период, FR-020).

    `extra` — подписка из формы, ещё не сохранённая (предпросмотр): M считается в памяти.
    """
    subs = [*snap.subscriptions, *([extra] if extra is not None else [])]
    charged = charged_to(subs, snap.assignments, "department", department_id)
    limits = snap.limits_for("department", department_id)
    out: list[PeriodShare] = []
    for kind in PERIODS:
        p = bounds(kind, noon(today))
        keys = needed_months(charged, p.start, p.end)
        saved = [(s, m) for s, m in keys if extra is None or s.id != extra.id]
        totals = await month_totals(conn, saved, snap.fx, store=store)
        if extra is not None:
            for s, m in keys - set(saved):
                mt = compute_month(s, m, snap.fx)
                if mt is not None:
                    totals[(s.id, m)] = mt
        amort = sum(amount(c, totals, p.start, p.end) for c in charged)
        out.append(PeriodShare(kind, p.start, limits.get(kind), amort))
    return out


def standalone_shares(sub: SubscriptionCfg, today: date, fx: Mapping[date, Decimal]) -> list[PeriodShare]:
    """Амортизация одной несохранённой подписки за текущие периоды, без лимита.

    Предпросмотр «не распределено» и сотрудника без подразделения.
    """
    alone = Charged(sub, _ALWAYS)
    out: list[PeriodShare] = []
    for kind in PERIODS:
        p = bounds(kind, noon(today))
        totals = {
            (s.id, m): mt
            for s, m in needed_months([alone], p.start, p.end)
            if (mt := compute_month(s, m, fx))
        }
        out.append(PeriodShare(kind, p.start, None, amount(alone, totals, p.start, p.end)))
    return out


def share_message(department: str, share: PeriodShare, threshold_pct: int) -> str:
    return (
        f"Подписки занимают {share.pct()}% бюджета подразделения «{department}» за "
        f"{PERIOD_RU[share.period]} с {share.start:%d.%m.%Y}: {kop_to_str(share.amort)} ₽ из "
        f"{kop_to_str(share.limit or 0)} ₽ (порог {threshold_pct}%)."
    )


async def notify_shares(
    conn: asyncpg.Connection, snap: ConfigSnapshot, department_id: UUID, today: date
) -> int:
    """Уведомление `subs_share` по каждому текущему периоду, где доля ≥ порога подразделения.

    Одно на (подразделение, период, начало периода) — `notify.create_once`. Возвращает число созданных.
    """
    dept = snap.departments.get(department_id)
    if dept is None:
        return 0
    created = 0
    for share in await department_shares(conn, snap, department_id, today, store=True):
        if share.reaches(dept.subs_warn_pct) and await notify.create_once(
            conn,
            "subs_share",
            department_id,
            share.period.value,  # type: ignore[arg-type]
            share.start,
            share_message(dept.name, share, dept.subs_warn_pct),
        ):
            created += 1
    return created


NEW_PERIOD_AT = time(0, 5)  # задача worker: с 00:05 МСК (12_tasks.md T051)


async def new_period_check(pool: asyncpg.Pool, now: datetime) -> int:
    """Задача worker: M текущего месяца фиксируется для всех действующих подписок, доля подписок
    проверяется заново по всем подразделениям (новый период — новое уведомление). Повтор безопасен.
    """
    msk_now = now.astimezone(MSK)
    if msk_now.time() < NEW_PERIOD_AT:
        return 0
    today = msk_now.date()
    month = month_start(today)
    async with pool.acquire() as conn:
        snap = await load_snapshot(conn, now)
        active = [
            (s, month)
            for s in snap.subscriptions
            if s.start_date <= month_end(month) and (s.end_date is None or s.end_date >= month)
        ]
        await month_totals(conn, active, snap.fx, store=True)
        created = 0
        for department_id in snap.departments:
            created += await notify_shares(conn, snap, department_id, today)
    return created
