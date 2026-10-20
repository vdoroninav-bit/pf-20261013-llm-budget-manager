"""T071 (14_tech_rulings.md, пакет 2, В1-5): правила `LimitBalance` — одни для getCallerBalance, getDepartmentBalance
и проверки порогов.

- `spent` — весь расход периода (токены, импорт, амортизация подписок), без резервов;
- `remaining_rub = limit − spent − reserved`;
- `used_pct = spent / limit × 100`, один знак, ROUND_HALF_UP, может быть > 100; limit = 0: 0.0 без расхода, иначе 100.0;
- `over_limit = spent > limit` — резервы перерасхода не создают.
"""

from datetime import datetime

import pytest

from bm.budget.service import Budget
from bm.domain.periods import MSK, bounds
from bm.domain.types import LimitState, PeriodKind

AT = datetime(2026, 10, 14, 12, tzinfo=MSK)


def state(limit: int, token: int = 0, import_: int = 0, amort: int = 0, reserved: int = 0) -> LimitState:
    return LimitState(
        owner_type="department",
        owner_ref="d",
        period=bounds(PeriodKind.MONTH, AT),
        limit=limit,
        spent_token=token,
        spent_import=import_,
        amort=amort,
        reserved=reserved,
    )


def test_reserve_does_not_make_over_limit_nor_change_used_pct() -> None:
    b = Budget._limit_balance(state(10_000, token=7_000, reserved=5_000))
    assert b.remaining_rub == "-20.00"  # остаток учитывает резерв
    assert b.used_pct == "70.0"  # процент — по расходу, без резерва
    assert b.over_limit is False  # резерв не перерасход


def test_spent_over_limit_is_over_limit() -> None:
    b = Budget._limit_balance(state(10_000, token=6_000, import_=3_000, amort=1_500))
    assert (b.used_pct, b.over_limit, b.remaining_rub) == ("105.0", True, "-5.00")


def test_spent_equal_to_limit_is_not_over() -> None:
    b = Budget._limit_balance(state(10_000, token=10_000))
    assert (b.used_pct, b.over_limit) == ("100.0", False)


@pytest.mark.parametrize(
    ("token", "pct", "over"),
    [(0, "0.0", False), (1, "100.0", True)],
)
def test_zero_limit(token: int, pct: str, over: bool) -> None:
    b = Budget._limit_balance(state(0, token=token, reserved=500))
    assert (b.used_pct, b.over_limit) == (pct, over)


def test_used_pct_rounds_half_up() -> None:
    assert Budget._limit_balance(state(1_000, token=7_995 // 10)).used_pct == "79.9"
    assert Budget._limit_balance(state(2_000, token=1_599)).used_pct == "80.0"  # 79.95 → 80.0
