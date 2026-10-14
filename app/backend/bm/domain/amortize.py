"""Накопительная амортизация подписок (ADR-003, APPROVAL вопрос 3А).

cum(d) = round_half_up(M × active_days(1..d) / days_in_month); доля окна = cum(конец) − cum(начало − 1).
Сумма долей всех дней месяца ровно равна M.
"""

from datetime import date, timedelta
from decimal import Decimal

from bm.domain.money import round_half_up
from bm.domain.periods import month_end
from bm.domain.types import Kop


def month_total(seats: int, seat_price: Decimal, fx: Decimal) -> Kop:
    """M — сумма подписки за полный месяц в копейках: seats × seat_price × fx, одно округление."""
    if seats < 0:
        raise ValueError("число мест не может быть отрицательным")
    return round_half_up(seats * seat_price * fx * 100)


def _active_days(month: date, active_from: date, active_to: date | None, upto: date) -> int:
    """Число активных дней подписки с 1-го числа месяца по `upto` включительно."""
    lo = max(month, active_from)
    hi = min(upto, month_end(month))
    if active_to is not None:
        hi = min(hi, active_to)
    return max(0, (hi - lo).days + 1)


def _cum(m_total: Kop, month: date, active_from: date, active_to: date | None, upto: date) -> Kop:
    if upto < month:
        return 0
    days_in_month = month_end(month).day
    return round_half_up(Decimal(m_total) * _active_days(month, active_from, active_to, upto) / days_in_month)


def amortized(
    m_total: Kop,
    month: date,
    active_from: date,
    active_to: date | None,
    window: tuple[date, date],
) -> Kop:
    """Амортизация подписки за дни окна `window` (включительно), попадающие в месяц `month`.

    `month` — первое число месяца; `m_total` — M этого месяца. Окно за пределами месяца обрезается,
    поэтому неделя на стыке месяцев считается суммой двух вызовов с M каждого месяца.
    """
    if month.day != 1:
        raise ValueError("month должен быть первым числом месяца")
    w_start, w_end = window
    if w_end < w_start:
        return 0
    return _cum(m_total, month, active_from, active_to, w_end) - _cum(
        m_total, month, active_from, active_to, w_start - timedelta(days=1)
    )
