"""T011: накопительная амортизация подписок (ADR-003, APPROVAL вопрос 3А).

cum(d) = round_half_up(M × active_days(1..d) / days_in_month); доля окна = cum(конец) − cum(начало − 1).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from bm.domain.amortize import amortized, month_total

OCT = date(2026, 10, 1)
SEP = date(2026, 9, 1)
M_41 = 750_000  # пример 4.1: 3 места × 2 500 ₽ = 7 500 ₽


def test_month_total_rub() -> None:
    assert month_total(3, Decimal("2500"), Decimal(1)) == M_41


def test_month_total_usd_round_half_up() -> None:
    # 1 место × 19.99 USD × 92.5 = 1849.075 ₽ = 184907.5 коп. → 184908
    assert month_total(1, Decimal("19.99"), Decimal("92.5")) == 184_908


def test_example_4_1_window_first_to_tenth() -> None:
    # 7 500 / 31 × 10 = 2 419,354… → 241 935 коп.
    assert amortized(M_41, OCT, date(2026, 10, 1), None, (date(2026, 10, 1), date(2026, 10, 10))) == 241_935


def test_example_4_1_single_day_tenth_cumulative_rule() -> None:
    # cum(10) − cum(9) = 241 935 − 217 742 = 24 193 коп. (241,93 ₽; ответ 3А). При 3Б было бы 24 194.
    assert amortized(M_41, OCT, date(2026, 10, 1), None, (date(2026, 10, 10), date(2026, 10, 10))) == 24_193


def test_sum_of_all_days_equals_month_total_exactly() -> None:
    days = [OCT + timedelta(days=i) for i in range(31)]
    total = sum(amortized(M_41, OCT, OCT, None, (d, d)) for d in days)
    assert total == M_41


@pytest.mark.parametrize("m_total", [1, 7, 99_999, 750_000, 1_234_567])
def test_sum_of_days_equals_m_for_any_m(m_total: int) -> None:
    days = [SEP + timedelta(days=i) for i in range(30)]
    assert sum(amortized(m_total, SEP, SEP, None, (d, d)) for d in days) == m_total


def test_whole_month_window_equals_m() -> None:
    assert amortized(M_41, OCT, OCT, None, (date(2026, 10, 1), date(2026, 10, 31))) == M_41


def test_start_15th_end_20th() -> None:
    # активна 15–20 октября: 6 дней из 31. За месяц: round(750000 × 6 / 31) = round(145161.29) = 145161
    whole = amortized(M_41, OCT, date(2026, 10, 15), date(2026, 10, 20), (date(2026, 10, 1), date(2026, 10, 31)))
    assert whole == 145_161
    # До старта списаний нет
    assert amortized(M_41, OCT, date(2026, 10, 15), date(2026, 10, 20), (date(2026, 10, 1), date(2026, 10, 14))) == 0
    # После окончания списаний нет
    after = (date(2026, 10, 21), date(2026, 10, 31))
    assert amortized(M_41, OCT, date(2026, 10, 15), date(2026, 10, 20), after) == 0
    # Сумма по дням 15–20 равна сумме за месяц
    by_day = sum(
        amortized(M_41, OCT, date(2026, 10, 15), date(2026, 10, 20), (d, d))
        for d in (date(2026, 10, 15) + timedelta(days=i) for i in range(6))
    )
    assert by_day == whole


def test_start_15th_partial_window() -> None:
    # активна с 15-го без окончания; окно 1–16: 2 активных дня → round(750000 × 2 / 31) = 48387
    assert amortized(M_41, OCT, date(2026, 10, 15), None, (date(2026, 10, 1), date(2026, 10, 16))) == 48_387


def test_window_outside_month_is_clipped() -> None:
    # окно 28.09–04.10 для месяца «октябрь» → считаются только 1–4 октября
    assert amortized(M_41, OCT, OCT, None, (date(2026, 9, 28), date(2026, 10, 4))) == round(750_000 * 4 / 31)
    # окно целиком в другом месяце → 0
    assert amortized(M_41, OCT, OCT, None, (date(2026, 9, 1), date(2026, 9, 30))) == 0


def test_active_from_previous_month_counts_from_month_start() -> None:
    assert amortized(M_41, OCT, date(2026, 8, 15), None, (date(2026, 10, 1), date(2026, 10, 10))) == 241_935


def test_week_across_months_two_fx() -> None:
    """Неделя 28.09–04.10.2026 (пн–вс): подписка в USD, курс сентября 90, октября 100 (Review Focus 1)."""
    seats, seat_price = 2, Decimal("10")
    m_sep = month_total(seats, seat_price, Decimal("90"))  # 1 800 ₽
    m_oct = month_total(seats, seat_price, Decimal("100"))  # 2 000 ₽
    assert (m_sep, m_oct) == (180_000, 200_000)
    week = (date(2026, 9, 28), date(2026, 10, 4))
    start = date(2026, 9, 1)
    sep_part = amortized(m_sep, SEP, start, None, week)
    oct_part = amortized(m_oct, OCT, start, None, week)
    # Сентябрь (30 дн.): cum(30) − cum(27) = 180000 − 162000 = 18000
    assert sep_part == 18_000
    # Октябрь (31 дн.): cum(4) = round(200000 × 4 / 31) = round(25806.45) = 25806
    assert oct_part == 25_806
    assert sep_part + oct_part == 43_806


def test_zero_total() -> None:
    assert amortized(0, OCT, OCT, None, (OCT, date(2026, 10, 31))) == 0


def test_month_must_be_first_day() -> None:
    with pytest.raises(ValueError):
        amortized(M_41, date(2026, 10, 2), OCT, None, (OCT, OCT))


def test_inverted_window_is_zero() -> None:
    assert amortized(M_41, OCT, OCT, None, (date(2026, 10, 10), date(2026, 10, 9))) == 0
