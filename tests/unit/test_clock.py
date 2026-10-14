"""T014: часы — системные и замороженные для тестов."""

from datetime import UTC, datetime, timedelta

import pytest

from bm.clock import FrozenClock, SystemClock
from bm.domain.periods import MSK


def test_frozen_clock_now_set_advance() -> None:
    c = FrozenClock(datetime(2026, 10, 14, 12, tzinfo=MSK))
    assert c.now() == datetime(2026, 10, 14, 12, tzinfo=MSK)
    c.advance(minutes=15)
    assert c.now() == datetime(2026, 10, 14, 12, 15, tzinfo=MSK)
    c.set(datetime(2026, 11, 1, tzinfo=MSK))
    assert c.now() == datetime(2026, 11, 1, tzinfo=MSK)


def test_frozen_clock_rejects_naive() -> None:
    with pytest.raises(ValueError):
        FrozenClock(datetime(2026, 10, 14))  # noqa: DTZ001


def test_system_clock_is_aware_and_current() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert abs(now - datetime.now(UTC)) < timedelta(seconds=5)
