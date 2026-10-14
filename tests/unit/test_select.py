"""T012: табличные тесты select() — нормативный алгоритм 12_arch_decision.md §4.5, шаги 2–8.

Каталог подобран под суммы спецификации (сценарии 1.1–1.7) при оценке 1 000 вход / 400 выход:
  A: 10 ₽ + 50 ₽ за 1k → 10 + 20 = 30 ₽ (3000 коп.), чат, инструменты, JSON
  B: 5 + 25 → 15 ₽, чат, инструменты, JSON
  C: 1 + 10 → 1 + 4 = 5 ₽ (500 коп.), чат, инструменты, JSON
  N: 0.5 + 5 → 2.5 ₽, чат, БЕЗ инструментов и JSON (дешёвая, но несовместимая)
  Z: 0 + 0 → 0 ₽, чат, без инструментов, контекст 8k (своя модель без ставки)
  E: эмбеддинги, 0.1 ₽/1k → 0.10 ₽
"""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from bm.domain.periods import MSK, bounds
from bm.domain.select import MissingFxRate, select
from bm.domain.types import (
    DecisionInput,
    DenyCode,
    LimitState,
    ModelInfo,
    Outcome,
    PeriodKind,
    PolicyMode,
    Price,
)

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=MSK)
FX = {"USD": Decimal("92.5")}
DEPT = "11111111-1111-1111-1111-111111111111"
EMP = "emp-0123456789"


def model(
    mid: str,
    p_in: str,
    p_out: str,
    *,
    kind: str = "chat",
    tools: bool = True,
    json: bool = True,
    ctx: int = 200_000,
    tie: int = 100,
    active: bool = True,
    currency: str = "RUB",
    default_in: int = 1000,
    default_out: int = 400,
) -> ModelInfo:
    return ModelInfo(
        id=mid,
        kind=kind,
        supports_tools=tools,
        supports_json=json,
        max_context=ctx,
        default_in=default_in,
        default_out=default_out,
        tie_order=tie,
        active=active,
        price=Price(
            id=uuid4(), in_per_1k=Decimal(p_in), out_per_1k=Decimal(p_out), cached_per_1k=None, currency=currency
        ),
    )


A = model("A", "10", "50")
B = model("B", "5", "25")
C = model("C", "1", "10")
N = model("N", "0.5", "5", tools=False, json=False)
Z = model("Z", "0", "0", tools=False, json=False, ctx=8000)
E = model("E", "0.1", "0", kind="embedding", tools=False, json=False)
CATALOG = (A, B, C, N, Z, E)
BY_ID = {m.id: m for m in CATALOG}


def limit(
    owner: str = "department",
    period: PeriodKind = PeriodKind.MONTH,
    lim: int = 10_000_000,
    *,
    spent: int = 0,
    imported: int = 0,
    amort: int = 0,
    reserved: int = 0,
) -> LimitState:
    return LimitState(
        owner_type=owner,
        owner_ref=DEPT if owner == "department" else EMP,
        period=bounds(period, NOW),
        limit=lim,
        spent_token=spent,
        spent_import=imported,
        amort=amort,
        reserved=reserved,
    )


def req(kind: str = "chat", *, tools: bool = False, json: bool = False, ctx: int = 0, inp=1000, out=400) -> DecisionInput:
    return DecisionInput(
        kind=kind, input_tokens=inp, max_output_tokens=out, tools=tools, json_mode=json, context_tokens=ctx
    )


def run(
    requested: str | None,
    *,
    allowed: set[str],
    limits: list[LimitState],
    mode: PolicyMode = PolicyMode.ENFORCE,
    r: DecisionInput | None = None,
    catalog=CATALOG,
    fx=FX,
):
    by_id = {m.id: m for m in catalog}
    return select(
        r or req(),
        by_id.get(requested) if requested else None,
        frozenset(allowed),
        catalog,
        limits,
        mode,
        fx,
    )


# ------------------------------------------------------------------ сценарии 1.1–1.7


def test_1_1_allow_with_reserve() -> None:
    # лимит 100 000 ₽/мес, израсходовано 50 000 ₽, оценка A = 30 ₽
    v = run("A", allowed={"A", "B"}, limits=[limit(lim=10_000_000, spent=5_000_000)])
    assert v.outcome is Outcome.ALLOW
    assert v.model == "A"
    assert v.reserve == 3000
    assert v.code is None and v.reason is None and v.observed_violation is None
    assert v.applied is not None and v.applied.remaining == 5_000_000
    assert v.estimate is not None
    assert (v.estimate.input_tokens, v.estimate.output_tokens, v.estimate.source) == (1000, 400, "request")


def test_1_2_model_not_allowed_enforce() -> None:
    v = run("A", allowed={"B"}, limits=[limit()])
    assert v.outcome is Outcome.DENY
    assert v.code is DenyCode.MODEL_NOT_ALLOWED
    assert v.reserve == 0
    assert v.model is None


def test_1_3_redirect_to_cheapest_compatible() -> None:
    # остаток 20 ₽, A = 30 ₽, C = 5 ₽ и поддерживает инструменты
    v = run("A", allowed={"A", "B", "C"}, limits=[limit(lim=10_000_000, spent=9_998_000)], r=req(tools=True))
    assert v.outcome is Outcome.REDIRECT
    assert v.model == "C"
    assert v.reserve == 500
    assert v.reason == "budget_exceeded"
    assert v.code is None


def test_1_4_deny_when_nothing_fits_with_applied_limit_and_reset() -> None:
    # остаток 3 ₽ — не помещается ни A (30), ни B (15), ни C (5)
    v = run("A", allowed={"A", "B", "C"}, limits=[limit(lim=10_000_000, spent=9_999_700)])
    assert v.outcome is Outcome.DENY
    assert v.code is DenyCode.BUDGET_EXCEEDED
    assert v.reserve == 0
    assert v.applied is not None
    assert (v.applied.owner_type, v.applied.period.kind) == ("department", PeriodKind.MONTH)
    assert v.applied.remaining == 300
    assert v.applied.period.resets_at.isoformat() == "2026-11-01T00:00:00+03:00"


@pytest.mark.parametrize("needs", [{"tools": True}, {"json": True}])
def test_1_4_cheap_model_lacks_tools_or_json_deny(needs: dict[str, bool]) -> None:
    # остаток 3 ₽ — помещается только N (2.5 ₽), но у неё нет инструментов и JSON
    v = run("A", allowed={"A", "N"}, limits=[limit(lim=10_000_000, spent=9_999_700)], r=req(**needs))
    assert v.outcome is Outcome.DENY
    assert v.code is DenyCode.BUDGET_EXCEEDED


def test_1_4_cheap_model_without_needs_is_redirect_target() -> None:
    v = run("A", allowed={"A", "N"}, limits=[limit(lim=10_000_000, spent=9_999_700)])
    assert (v.outcome, v.model, v.reserve) == (Outcome.REDIRECT, "N", 250)


def test_1_5_embeddings_never_redirected() -> None:
    # E = 0.10 ₽ на 1 000 входных; остаток 0; есть бесплатная чат-модель Z, но эмбеддинги не перенаправляются
    v = run("E", allowed={"E", "Z"}, limits=[limit(lim=100, spent=100)], r=req("embedding", out=0))
    assert v.outcome is Outcome.DENY
    assert v.code is DenyCode.BUDGET_EXCEEDED
    assert v.model is None


def test_1_6_strictest_limit_personal_day_exhausted() -> None:
    limits = [
        limit("employee", PeriodKind.DAY, 50_000, spent=50_000),  # личный дневной 500 ₽ исчерпан
        limit("department", PeriodKind.MONTH, 10_000_000, spent=1_000_000),  # у подразделения 90 000 ₽
    ]
    v = run("A", allowed={"A", "C"}, limits=limits)
    assert v.outcome is Outcome.DENY
    assert v.code is DenyCode.BUDGET_EXCEEDED
    assert v.applied is not None
    assert (v.applied.owner_type, v.applied.period.kind, v.applied.remaining) == ("employee", PeriodKind.DAY, 0)


def test_1_6_strictest_limit_redirects_to_zero_price() -> None:
    limits = [
        limit("employee", PeriodKind.DAY, 50_000, spent=50_000),
        limit("department", PeriodKind.MONTH, 10_000_000, spent=1_000_000),
    ]
    v = run("A", allowed={"A", "C", "Z"}, limits=limits)
    assert (v.outcome, v.model, v.reserve) == (Outcome.REDIRECT, "Z", 0)


@pytest.mark.parametrize("mode", [PolicyMode.OBSERVE, PolicyMode.WARN])
def test_1_7_observe_and_warn_allow_over_limit(mode: PolicyMode) -> None:
    v = run("A", allowed={"A", "C"}, limits=[limit(lim=10_000_000, spent=10_000_000)], mode=mode)
    assert v.outcome is Outcome.ALLOW
    assert v.model == "A"
    assert v.reserve == 3000
    assert v.observed_violation == "budget_exceeded"
    assert v.code is None


# ------------------------------------------------------------------ пограничные случаи


def test_zero_price_model_always_fits() -> None:
    v = run("Z", allowed={"Z"}, limits=[limit(lim=100, spent=1_000)])
    assert (v.outcome, v.model, v.reserve) == (Outcome.ALLOW, "Z", 0)


def test_equal_price_tie_order_then_id() -> None:
    c1 = model("C1", "1", "10", tie=20)
    c2 = model("C2", "1", "10", tie=10)
    c3 = model("C0", "1", "10", tie=10)
    cat = (A, c1, c2, c3)
    v = run("A", allowed={"A", "C1", "C2", "C0"}, limits=[limit(lim=2000)], catalog=cat)
    assert v.model == "C0"  # tie_order 10 у C0 и C2, затем по id
    v = run("A", allowed={"A", "C1", "C2"}, limits=[limit(lim=2000)], catalog=cat)
    assert v.model == "C2"


def test_cheapest_wins_over_tie_order() -> None:
    cheap = model("Y", "1", "5", tie=999)  # 1 + 2 = 3 ₽
    v = run("A", allowed={"A", "C", "Y"}, limits=[limit(lim=2000)], catalog=(A, C, cheap))
    assert (v.model, v.reserve) == ("Y", 300)


def test_negative_remaining_only_zero_price() -> None:
    # остаток уже отрицательный (поздний импорт) — перенаправление только на модель с нулевой ценой
    lim = limit(lim=10_000, spent=5_000, imported=6_000)
    assert lim.remaining == -1_000
    v = run("A", allowed={"A", "C", "N", "Z"}, limits=[lim])
    assert (v.outcome, v.model, v.reserve) == (Outcome.REDIRECT, "Z", 0)
    v = run("A", allowed={"A", "C", "N"}, limits=[lim])
    assert (v.outcome, v.code) == (Outcome.DENY, DenyCode.BUDGET_EXCEEDED)
    assert v.applied is not None and v.applied.remaining == -1_000


def test_zero_price_candidate_respects_context_and_needs() -> None:
    lim = limit(lim=0)
    v = run("A", allowed={"A", "Z"}, limits=[lim], r=req(ctx=16_000))
    assert v.outcome is Outcome.DENY  # у Z контекст 8k
    v = run("A", allowed={"A", "Z"}, limits=[lim], r=req(tools=True))
    assert v.outcome is Outcome.DENY  # у Z нет инструментов


def test_blocked_by_subscriptions() -> None:
    # амортизация подписок за месяц ≥ лимита
    v = run("A", allowed={"A", "C"}, limits=[limit(lim=100_000, amort=100_000)])
    assert (v.outcome, v.code) == (Outcome.DENY, DenyCode.BUDGET_EXCEEDED)
    assert v.blocked_by_subs is True


def test_not_blocked_by_subs_when_zero_limit_and_no_subs() -> None:
    # 13_design_rulings.md В3: признак = amort > 0 и amort ≥ limit
    v = run("A", allowed={"A"}, limits=[limit(lim=0)])
    assert v.outcome is Outcome.DENY
    assert v.blocked_by_subs is False


def test_not_blocked_by_subs_when_spend_is_tokens() -> None:
    v = run("A", allowed={"A"}, limits=[limit(lim=100_000, spent=100_000, amort=1)])
    assert v.blocked_by_subs is False


def test_observe_model_not_allowed_is_allow_with_violation() -> None:
    v = run("A", allowed={"B"}, limits=[limit()], mode=PolicyMode.OBSERVE)
    assert (v.outcome, v.model, v.reserve) == (Outcome.ALLOW, "A", 3000)
    assert v.observed_violation == "model_not_allowed"
    assert v.code is None


def test_warn_model_not_allowed_is_allow_with_violation() -> None:
    v = run("A", allowed=set(), limits=[], mode=PolicyMode.WARN)
    assert (v.outcome, v.observed_violation) == (Outcome.ALLOW, "model_not_allowed")


def test_empty_allowed_enforce_deny() -> None:
    v = run("A", allowed=set(), limits=[limit()])
    assert (v.outcome, v.code) == (Outcome.DENY, DenyCode.MODEL_NOT_ALLOWED)


def test_unknown_model_deny_even_in_observe() -> None:
    v = run(None, allowed={"A"}, limits=[], mode=PolicyMode.OBSERVE)
    assert (v.outcome, v.code, v.model) == (Outcome.DENY, DenyCode.MODEL_UNKNOWN, None)


def test_inactive_model_is_unknown() -> None:
    off = replace(A, active=False)
    v = run("A", allowed={"A"}, limits=[], catalog=(off, C))
    assert (v.outcome, v.code) == (Outcome.DENY, DenyCode.MODEL_UNKNOWN)


def test_inactive_model_not_a_redirect_candidate() -> None:
    off_c = replace(C, active=False)
    v = run("A", allowed={"A", "C"}, limits=[limit(lim=2000)], catalog=(A, off_c))
    assert (v.outcome, v.code) == (Outcome.DENY, DenyCode.BUDGET_EXCEEDED)


def test_not_allowed_model_not_a_candidate() -> None:
    v = run("A", allowed={"A"}, limits=[limit(lim=2000)])
    assert (v.outcome, v.code) == (Outcome.DENY, DenyCode.BUDGET_EXCEEDED)


def test_embedding_model_not_a_chat_candidate() -> None:
    v = run("A", allowed={"A", "E"}, limits=[limit(lim=2000)])
    assert (v.outcome, v.code) == (Outcome.DENY, DenyCode.BUDGET_EXCEEDED)


def test_context_requirement_filters_candidates() -> None:
    small = model("S", "1", "10", ctx=4000)
    v = run("A", allowed={"A", "S"}, limits=[limit(lim=2000)], catalog=(A, small), r=req(ctx=8000))
    assert v.outcome is Outcome.DENY
    v = run("A", allowed={"A", "S"}, limits=[limit(lim=2000)], catalog=(A, small), r=req(ctx=4000))
    assert v.model == "S"


def test_no_limits_means_no_restriction() -> None:
    v = run("A", allowed={"A"}, limits=[])
    assert (v.outcome, v.reserve, v.applied) == (Outcome.ALLOW, 3000, None)


def test_exact_fit_is_allowed() -> None:
    v = run("A", allowed={"A"}, limits=[limit(lim=3000)])
    assert v.outcome is Outcome.ALLOW


def test_reserved_reduces_remaining() -> None:
    v = run("A", allowed={"A"}, limits=[limit(lim=10_000, reserved=7_001)])
    assert (v.outcome, v.code) == (Outcome.DENY, DenyCode.BUDGET_EXCEEDED)


def test_all_limits_must_fit_for_redirect() -> None:
    # C (5 ₽) помещается в дневной лимит сотрудника, но не в недельный подразделения (остаток 4 ₽)
    limits = [
        limit("employee", PeriodKind.DAY, 50_000),
        limit("department", PeriodKind.WEEK, 10_000, spent=9_600),
    ]
    v = run("A", allowed={"A", "C"}, limits=limits)
    assert (v.outcome, v.code) == (Outcome.DENY, DenyCode.BUDGET_EXCEEDED)
    assert v.applied is not None and (v.applied.owner_type, v.applied.period.kind) == ("department", PeriodKind.WEEK)


def test_applied_limit_tie_employee_before_department_then_day_week_month() -> None:
    limits = [
        limit("department", PeriodKind.DAY, 1000),
        limit("employee", PeriodKind.MONTH, 1000),
        limit("employee", PeriodKind.WEEK, 1000),
    ]
    v = run("A", allowed={"A"}, limits=limits)
    assert v.applied is not None
    assert (v.applied.owner_type, v.applied.period.kind) == ("employee", PeriodKind.WEEK)


def test_redirect_uses_candidate_default_out_when_max_output_not_given() -> None:
    # A по умолчанию: 1000 / 400 → 30 ₽. Кандидат C2 с default_out 100: 1 + 1 = 2 ₽
    c2 = model("C2", "1", "10", default_out=100)
    v = run("A", allowed={"A", "C2"}, limits=[limit(lim=2500)], catalog=(A, c2), r=req(inp=1000, out=None))
    assert (v.outcome, v.model, v.reserve) == (Outcome.REDIRECT, "C2", 200)
    assert v.estimate is not None
    assert (v.estimate.output_tokens, v.estimate.source) == (100, "catalog_default")


def test_estimate_from_catalog_defaults() -> None:
    v = run("A", allowed={"A"}, limits=[], r=req(inp=None, out=None))
    assert v.reserve == 3000
    assert v.estimate is not None and v.estimate.source == "catalog_default"


def test_usd_model_priced_with_fx() -> None:
    usd = model("U", "0.003", "0.015", currency="USD")
    # (1000 × 0.003 + 400 × 0.015) / 1000 = 0.009 USD × 92.5 = 0.8325 ₽ → 83 коп.
    v = run("U", allowed={"U"}, limits=[], catalog=(usd,))
    assert v.reserve == 83


def test_missing_fx_for_requested_usd_model_raises() -> None:
    usd = model("U", "0.003", "0.015", currency="USD")
    with pytest.raises(MissingFxRate):
        run("U", allowed={"U"}, limits=[], catalog=(usd,), fx={})


def test_usd_candidate_without_fx_is_skipped() -> None:
    usd = model("U", "0.001", "0.001", currency="USD")
    v = run("A", allowed={"A", "U", "C"}, limits=[limit(lim=2000)], catalog=(A, usd, C), fx={})
    assert v.model == "C"


def test_select_is_deterministic_regardless_of_catalog_order() -> None:
    c1 = model("C1", "1", "10", tie=5)
    c2 = model("C2", "1", "10", tie=5)
    lims = [limit(lim=2000)]
    v1 = run("A", allowed={"A", "C1", "C2"}, limits=lims, catalog=(A, c1, c2))
    v2 = run("A", allowed={"A", "C1", "C2"}, limits=lims, catalog=(c2, c1, A))
    assert v1 == v2
