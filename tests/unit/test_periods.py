"""T010: календарные периоды по МСК (FR-014): сутки с 00:00, неделя с понедельника, месяц с 1-го."""

import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from bm.domain.periods import MSK, bounds, msk_date
from bm.domain.types import Period, PeriodKind

MSK_OFFSET = timezone(timedelta(hours=3))


def msk(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=MSK)


def test_day_bounds() -> None:
    p = bounds(PeriodKind.DAY, msk(2026, 10, 14, 15, 30))
    assert p == Period(
        kind=PeriodKind.DAY,
        start=date(2026, 10, 14),
        end=date(2026, 10, 14),
        resets_at=msk(2026, 10, 15),
    )


def test_week_starting_monday_midnight_msk_starts_that_monday() -> None:
    # 2026-10-12 — понедельник
    p = bounds(PeriodKind.WEEK, msk(2026, 10, 12, 0, 0, 0))
    assert p.start == date(2026, 10, 12)
    assert p.end == date(2026, 10, 18)
    assert p.resets_at == msk(2026, 10, 19)


def test_week_sunday_last_second_belongs_to_previous_monday() -> None:
    p = bounds(PeriodKind.WEEK, msk(2026, 10, 11, 23, 59, 59))
    assert p.start == date(2026, 10, 5)
    assert p.end == date(2026, 10, 11)


def test_week_across_months() -> None:
    p = bounds(PeriodKind.WEEK, msk(2026, 10, 2, 12))
    assert (p.start, p.end) == (date(2026, 9, 28), date(2026, 10, 4))


def test_month_last_second_resets_at_first_of_next_month_msk() -> None:
    at = datetime(2026, 10, 31, 23, 59, 59, tzinfo=MSK_OFFSET)
    p = bounds(PeriodKind.MONTH, at)
    assert p.start == date(2026, 10, 1)
    assert p.end == date(2026, 10, 31)
    assert p.resets_at == datetime(2026, 11, 1, 0, 0, tzinfo=MSK_OFFSET)
    assert p.resets_at.isoformat() == "2026-11-01T00:00:00+03:00"


def test_month_december_rolls_year() -> None:
    p = bounds(PeriodKind.MONTH, msk(2026, 12, 31, 10))
    assert p.end == date(2026, 12, 31)
    assert p.resets_at == msk(2027, 1, 1)


def test_february_leap_year() -> None:
    p = bounds(PeriodKind.MONTH, msk(2028, 2, 10))
    assert p.end == date(2028, 2, 29)


def test_utc_input_is_converted_to_msk() -> None:
    # 2026-10-31 21:30 UTC = 2026-11-01 00:30 МСК — уже ноябрь
    at = datetime(2026, 10, 31, 21, 30, tzinfo=UTC)
    assert msk_date(at) == date(2026, 11, 1)
    assert bounds(PeriodKind.MONTH, at).start == date(2026, 11, 1)
    assert bounds(PeriodKind.DAY, at).start == date(2026, 11, 1)


def test_resets_at_is_in_msk() -> None:
    p = bounds(PeriodKind.DAY, datetime(2026, 10, 14, 12, tzinfo=UTC))
    assert p.resets_at.utcoffset() == timedelta(hours=3)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        bounds(PeriodKind.DAY, datetime(2026, 10, 14, 12))  # noqa: DTZ001
    with pytest.raises(ValueError):
        msk_date(datetime(2026, 10, 14, 12))  # noqa: DTZ001


def test_period_starts_at_is_msk_midnight() -> None:
    p = bounds(PeriodKind.WEEK, msk(2026, 10, 14, 15))
    assert p.starts_at == msk(2026, 10, 12)


@pytest.fixture
def process_tz(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize("tz", ["UTC", "America/New_York", "Asia/Tokyo"])
def test_result_does_not_depend_on_process_tz(
    tz: str, monkeypatch: pytest.MonkeyPatch, process_tz: None
) -> None:
    at = datetime(2026, 10, 31, 22, 30, tzinfo=UTC)  # 01:30 МСК 1 ноября
    expected = {k: bounds(k, at) for k in PeriodKind}
    monkeypatch.setenv("TZ", tz)
    time.tzset()
    for k in PeriodKind:
        assert bounds(k, at) == expected[k]
    assert msk_date(at) == date(2026, 11, 1)
