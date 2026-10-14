"""Машинные коды решений, подсказки HTTP-статуса (ADR-005) и тексты на русском (принцип IX).

Коды едины для HTTP API и MCP (FR-002).
"""

from bm.domain.money import kop_to_str
from bm.domain.types import DenyCode, LimitState, Outcome, PeriodKind, Verdict

HTTP_STATUS_HINT: dict[DenyCode, int] = {
    DenyCode.MODEL_NOT_ALLOWED: 403,
    DenyCode.BUDGET_EXCEEDED: 429,
    DenyCode.MODEL_UNKNOWN: 422,
    DenyCode.SUBJECT_UNKNOWN: 422,
}

_PERIOD_RU = {PeriodKind.DAY: "дневной", PeriodKind.WEEK: "недельный", PeriodKind.MONTH: "месячный"}
_OWNER_RU = {"employee": "личный", "department": "подразделения"}


def http_status_hint(outcome: Outcome, code: DenyCode | None) -> int:
    """Подсказка шлюзу: 200 для allow/redirect; для deny — 403/422/429 по коду."""
    if outcome is not Outcome.DENY or code is None:
        return 200
    return HTTP_STATUS_HINT[code]


def describe_limit(s: LimitState) -> str:
    """«месячный лимит подразделения 100000.00 ₽, остаток 20.00 ₽, сброс 01.11.2026 00:00 МСК»."""
    reset = s.period.resets_at.strftime("%d.%m.%Y %H:%M")
    return (
        f"{_PERIOD_RU[s.period.kind]} лимит {_OWNER_RU.get(s.owner_type, s.owner_type)} "
        f"{kop_to_str(s.limit)} ₽, остаток {kop_to_str(s.remaining)} ₽, сброс {reset} МСК"
    )


def message(v: Verdict, requested_model: str) -> str:
    """Понятное объяснение решения на русском (поле `Decision.message`)."""
    if v.outcome is Outcome.REDIRECT:
        text = (
            f"Бюджет не покрывает модель {requested_model}: запрос перенаправлен на {v.model}, "
            f"резерв {kop_to_str(v.reserve)} ₽."
        )
    elif v.outcome is Outcome.ALLOW:
        text = f"Разрешено: модель {v.model}, резерв {kop_to_str(v.reserve)} ₽."
        if v.observed_violation == "budget_exceeded":
            text += " Лимит превышен — режим наблюдения, вызов не ограничен."
        elif v.observed_violation == "model_not_allowed":
            text += " Модель не разрешена подразделению — режим наблюдения, вызов не ограничен."
    else:
        text = {
            DenyCode.MODEL_NOT_ALLOWED: f"Отказ: модель {requested_model} не разрешена подразделению.",
            DenyCode.BUDGET_EXCEEDED: "Отказ: бюджет исчерпан, подходящей более дешёвой модели нет.",
            DenyCode.MODEL_UNKNOWN: f"Отказ: модели {requested_model} нет в каталоге или она выключена.",
            DenyCode.SUBJECT_UNKNOWN: "Отказ: сотрудник не найден или не состоит ни в одном подразделении.",
        }[v.code or DenyCode.BUDGET_EXCEEDED]
        if v.code is DenyCode.BUDGET_EXCEEDED and v.blocked_by_subs:
            text += " Бюджет занят подписками."
    if v.applied is not None and (v.outcome is not Outcome.ALLOW or v.observed_violation):
        text += f" Применён {describe_limit(v.applied)}."
    return text
