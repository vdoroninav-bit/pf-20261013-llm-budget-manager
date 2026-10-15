"""Ошибки модуля `budget`. Адаптеры (HTTP, MCP) переводят их в свой формат: `code` — машинный код
контракта, `detail` — текст на русском (принцип IX)."""


class BudgetError(Exception):
    """Ошибка запроса вызывающей системы (не решение: отказ по решению — это `outcome=deny`)."""

    status: int = 422
    code: str = "validation_error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class Unauthenticated(BudgetError):
    status = 401
    code = "unauthorized"


class SubjectOutOfScope(BudgetError):
    status = 403
    code = "subject_out_of_scope"


class DecisionNotFound(BudgetError):
    status = 404
    code = "decision_not_found"


class SubjectNotFound(BudgetError):
    """Баланс: субъект не найден (`getCallerBalance` → 404)."""

    status = 404
    code = "subject_unknown"


class InvalidReport(BudgetError):
    """Отчёт о расходе нельзя учесть (422). `code` уточняет причину."""

    def __init__(self, detail: str, code: str = "validation_error") -> None:
        super().__init__(detail)
        self.code = code


class FxRateMissing(BudgetError):
    code = "fx_rate_missing"
