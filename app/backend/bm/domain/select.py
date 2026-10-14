"""Выбор решения allow / redirect / deny — нормативный алгоритм 12_arch_decision.md §4.5, шаги 2–8.

Шаг 1 (субъект) и шаг 9 (запись) делает вызывающий слой (bm.budget). Функция чистая и детерминированная.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal

from bm.domain.money import cost
from bm.domain.types import (
    DecisionInput,
    DenyCode,
    Estimate,
    Kop,
    LimitState,
    ModelInfo,
    Outcome,
    PeriodKind,
    PolicyMode,
    Verdict,
    Violation,
)

_ONE = Decimal(1)
_OWNER_ORDER = {"employee": 0, "department": 1}
_PERIOD_ORDER = {PeriodKind.DAY: 0, PeriodKind.WEEK: 1, PeriodKind.MONTH: 2}


class MissingFxRate(LookupError):
    """Нет курса для валюты запрошенной модели (код ошибки API `fx_rate_missing`)."""

    def __init__(self, currency: str) -> None:
        super().__init__(currency)
        self.currency = currency


def _rate(currency: str, fx: Mapping[str, Decimal]) -> Decimal | None:
    return _ONE if currency == "RUB" else fx.get(currency)


def _estimate(req: DecisionInput, m: ModelInfo) -> Estimate:
    inp = m.default_in if req.input_tokens is None else req.input_tokens
    out = m.default_out if req.max_output_tokens is None else req.max_output_tokens
    from_request = req.input_tokens is not None and req.max_output_tokens is not None
    return Estimate(inp, out, "request" if from_request else "catalog_default")


def _cost(est: Estimate, m: ModelInfo, rate: Decimal) -> Kop:
    return cost(est.input_tokens, est.output_tokens, 0, m.price, rate)


def _applied(limits: Sequence[LimitState]) -> LimitState | None:
    """Лимит с минимальным остатком; при равенстве — сотрудник, затем подразделение; день, неделя, месяц."""
    if not limits:
        return None
    return min(
        limits,
        key=lambda s: (s.remaining, _OWNER_ORDER.get(s.owner_type, 9), _PERIOD_ORDER[s.period.kind]),
    )


def _fits(c: Kop, min_remaining: Kop | None) -> bool:
    return c == 0 or min_remaining is None or c <= min_remaining


def _blocked_by_subs(applied: LimitState | None) -> bool:
    # 13_design_rulings.md В3: амортизация > 0 и ≥ лимита применённого периода.
    return applied is not None and applied.amort > 0 and applied.amort >= applied.limit


def _verdict(
    outcome: Outcome,
    *,
    model: str | None = None,
    reserve: Kop = 0,
    code: DenyCode | None = None,
    redirected: bool = False,
    violation: Violation | None = None,
    applied: LimitState | None = None,
    estimate: Estimate | None = None,
) -> Verdict:
    return Verdict(
        outcome=outcome,
        model=model,
        reserve=reserve,
        code=code,
        reason="budget_exceeded" if redirected else None,
        observed_violation=violation,
        applied=applied,
        blocked_by_subs=_blocked_by_subs(applied),
        estimate=estimate,
    )


def select(
    req: DecisionInput,
    requested: ModelInfo | None,
    allowed: frozenset[str],
    catalog: Sequence[ModelInfo],
    limits: Sequence[LimitState],
    mode: str,
    fx: Mapping[str, Decimal],
) -> Verdict:
    """Решение по запросу `req` к модели `requested` (None — нет в каталоге).

    `allowed` — разрешённые модели подразделения (пусто — ничего не разрешено); `limits` — все применимые
    лимиты сотрудника и подразделения; `mode` — PolicyMode; `fx` — курс валюты к рублю (RUB = 1 всегда).
    Бросает MissingFxRate, если нет курса для валюты запрошенной модели.
    """
    policy = PolicyMode(mode)
    soft = policy is not PolicyMode.ENFORCE

    # Шаг 2. Модель
    if requested is None or not requested.active:
        return _verdict(Outcome.DENY, code=DenyCode.MODEL_UNKNOWN)

    # Шаг 3. Разрешённость
    violation: Violation | None = None
    if requested.id not in allowed:
        if not soft:
            return _verdict(Outcome.DENY, code=DenyCode.MODEL_NOT_ALLOWED)
        violation = "model_not_allowed"

    # Шаг 4. Оценка
    rate = _rate(requested.price.currency, fx)
    if rate is None:
        raise MissingFxRate(requested.price.currency)
    est = _estimate(req, requested)
    c = _cost(est, requested, rate)

    # Шаг 5. Лимиты; шаг 8. Применённый лимит
    applied = _applied(limits)
    min_remaining = applied.remaining if applied is not None else None

    # Шаг 6
    if _fits(c, min_remaining):
        return _verdict(
            Outcome.ALLOW, model=requested.id, reserve=c, violation=violation, applied=applied, estimate=est
        )

    # Шаг 7
    if soft:
        return _verdict(
            Outcome.ALLOW,
            model=requested.id,
            reserve=c,
            violation=violation or "budget_exceeded",
            applied=applied,
            estimate=est,
        )
    if req.kind != "chat":
        return _verdict(Outcome.DENY, code=DenyCode.BUDGET_EXCEEDED, applied=applied)

    best: tuple[Kop, int, str, Estimate] | None = None
    for m in catalog:
        if not (m.active and m.kind == "chat" and m.id in allowed):
            continue
        if (req.tools and not m.supports_tools) or (req.json_mode and not m.supports_json):
            continue
        if m.max_context < req.context_tokens:
            continue
        m_rate = _rate(m.price.currency, fx)
        if m_rate is None:
            continue  # без курса кандидата не оценить — пропускаем
        m_est = _estimate(req, m)
        m_cost = _cost(m_est, m, m_rate)
        if not _fits(m_cost, min_remaining):
            continue
        key = (m_cost, m.tie_order, m.id, m_est)
        if best is None or key[:3] < best[:3]:
            best = key

    if best is None:
        return _verdict(Outcome.DENY, code=DenyCode.BUDGET_EXCEEDED, applied=applied)
    m_cost, _, m_id, m_est = best
    return _verdict(
        Outcome.REDIRECT, model=m_id, reserve=m_cost, redirected=True, applied=applied, estimate=m_est
    )
