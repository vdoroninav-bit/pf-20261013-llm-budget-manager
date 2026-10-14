"""Календарные периоды по МСК (FR-014): сутки с 00:00, неделя с понедельника, месяц с 1-го числа.

Результат не зависит от часового пояса процесса: всё считается через ZoneInfo("Europe/Moscow").
"""

import calendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bm.domain.types import Period, PeriodKind

MSK = ZoneInfo("Europe/Moscow")


def _require_aware(at: datetime) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("нужно время с часовым поясом (naive datetime запрещён)")


def msk_date(at: datetime) -> date:
    """Календарная дата момента `at` по МСК."""
    _require_aware(at)
    return at.astimezone(MSK).date()


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def bounds(kind: PeriodKind, at: datetime) -> Period:
    """Период вида `kind`, в который попадает момент `at` (по МСК)."""
    d = msk_date(at)
    match kind:
        case PeriodKind.DAY:
            start, end = d, d
        case PeriodKind.WEEK:
            start = d - timedelta(days=d.weekday())
            end = start + timedelta(days=6)
        case PeriodKind.MONTH:
            start, end = month_start(d), month_end(d)
        case _:  # pragma: no cover - перечисление закрыто
            raise ValueError(f"неизвестный период: {kind!r}")
    resets_at = datetime.combine(end + timedelta(days=1), time(0), tzinfo=MSK)
    return Period(kind=kind, start=start, end=end, resets_at=resets_at)
