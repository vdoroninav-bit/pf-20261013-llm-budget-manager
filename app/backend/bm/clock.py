"""Часы — шов для времени. Код берёт «сейчас» только у `Clock`, тесты подставляют `FrozenClock`."""

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Текущий момент с часовым поясом."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """Замороженное время для тестов: не идёт само, двигается `set()` и `advance()`."""

    def __init__(self, at: datetime) -> None:
        self._at = self._check(at)

    @staticmethod
    def _check(at: datetime) -> datetime:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("нужно время с часовым поясом")
        return at

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = self._check(at)

    def advance(self, **delta: int) -> None:
        self._at += timedelta(**delta)
