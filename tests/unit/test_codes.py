"""T013: подсказки HTTP-статуса (ADR-005) и тексты решений на русском (принцип IX)."""

from datetime import datetime

import pytest

from bm.domain.codes import http_status_hint, message
from bm.domain.periods import MSK, bounds
from bm.domain.types import DenyCode, LimitState, Outcome, PeriodKind, Verdict

NOW = datetime(2026, 10, 14, 12, tzinfo=MSK)


@pytest.mark.parametrize(
    ("outcome", "code", "hint"),
    [
        (Outcome.ALLOW, None, 200),
        (Outcome.REDIRECT, None, 200),
        (Outcome.DENY, DenyCode.MODEL_NOT_ALLOWED, 403),
        (Outcome.DENY, DenyCode.BUDGET_EXCEEDED, 429),
        (Outcome.DENY, DenyCode.MODEL_UNKNOWN, 422),
        (Outcome.DENY, DenyCode.SUBJECT_UNKNOWN, 422),
    ],
)
def test_http_status_hint(outcome: Outcome, code: DenyCode | None, hint: int) -> None:
    assert http_status_hint(outcome, code) == hint


def _limit(amort: int = 0) -> LimitState:
    return LimitState("department", "d", bounds(PeriodKind.MONTH, NOW), 10_000, 9_800, 0, amort, 0)


def _v(outcome: Outcome, **kw) -> Verdict:
    base = {
        "model": None,
        "reserve": 0,
        "code": None,
        "reason": None,
        "observed_violation": None,
        "applied": None,
        "blocked_by_subs": False,
        "estimate": None,
    }
    base.update(kw)
    return Verdict(outcome=outcome, **base)


def test_deny_budget_message_has_limit_and_reset_msk() -> None:
    text = message(_v(Outcome.DENY, code=DenyCode.BUDGET_EXCEEDED, applied=_limit()), "A")
    assert "бюджет исчерпан" in text
    assert "месячный лимит подразделения 100.00 ₽" in text
    assert "остаток 2.00 ₽" in text
    assert "01.11.2026 00:00 МСК" in text


def test_redirect_message() -> None:
    text = message(_v(Outcome.REDIRECT, model="C", reserve=500, reason="budget_exceeded", applied=_limit()), "A")
    assert "перенаправлен на C" in text and "5.00 ₽" in text


def test_observed_violation_message() -> None:
    text = message(_v(Outcome.ALLOW, model="A", reserve=3000, observed_violation="budget_exceeded", applied=_limit()), "A")
    assert "режим наблюдения" in text


def test_blocked_by_subs_message() -> None:
    v = _v(Outcome.DENY, code=DenyCode.BUDGET_EXCEEDED, applied=_limit(amort=10_000), blocked_by_subs=True)
    assert "занят подписками" in message(v, "A")


@pytest.mark.parametrize("code", list(DenyCode))
def test_every_deny_code_has_russian_message(code: DenyCode) -> None:
    text = message(_v(Outcome.DENY, code=code), "A")
    assert text.startswith("Отказ")
