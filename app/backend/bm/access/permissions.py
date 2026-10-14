"""Права админки: `PERMISSIONS` — единственная таблица `operationId → роли` (12_plan.md §6).

Значения совпадают с `x-roles` в 12_openapi.yaml (тест `tests/unit/test_permissions.py`). Из этой же
таблицы строится матрица «роль × операция» SC-010 (T070). Операции вызывающих систем и системные операции
сюда не входят: у них нет сессии админки.
"""

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    BUDGET_OWNER = "budget_owner"  # только своё подразделение, только чтение расхода
    FINANCE_VIEWER = "finance_viewer"  # все подразделения, только чтение, без сотрудников


ADMIN, BUDGET_OWNER, FINANCE_VIEWER = Role.ADMIN, Role.BUDGET_OWNER, Role.FINANCE_VIEWER


def _r(*roles: Role) -> frozenset[Role]:
    return frozenset(roles)


PERMISSIONS: dict[str, frozenset[Role]] = {
    # auth
    "logout": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "me": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "changePassword": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    # org
    "importOrg": _r(ADMIN),
    # departments
    "listDepartments": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "createDepartment": _r(ADMIN),
    "getDepartment": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "updateDepartment": _r(ADMIN),
    "putDepartmentLimits": _r(ADMIN),
    "getDepartmentBalance": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "listDepartmentBalanceItems": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    # employees
    "listEmployees": _r(ADMIN, BUDGET_OWNER),
    "getEmployee": _r(ADMIN, BUDGET_OWNER),
    "putEmployeeLimits": _r(ADMIN),
    "revealPseudonym": _r(ADMIN),
    # models
    "listModels": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "createModel": _r(ADMIN),
    "getModel": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "updateModel": _r(ADMIN),
    "listModelPrices": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "createModelPrice": _r(ADMIN),
    # fx
    "listFxRates": _r(ADMIN, FINANCE_VIEWER),
    "putFxRate": _r(ADMIN),
    # subscriptions
    "listSubscriptions": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "createSubscription": _r(ADMIN),
    "previewSubscription": _r(ADMIN),
    "getSubscription": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "endSubscription": _r(ADMIN),
    "deleteSubscription": _r(ADMIN),
    # provider-sources
    "listProviderSources": _r(ADMIN),
    "createProviderSource": _r(ADMIN),
    "updateProviderSource": _r(ADMIN),
    "deleteProviderSource": _r(ADMIN),
    # callers
    "listCallers": _r(ADMIN),
    "createCaller": _r(ADMIN),
    "updateCaller": _r(ADMIN),
    "revokeCaller": _r(ADMIN),
    # admin-users
    "listAdminUsers": _r(ADMIN),
    "createAdminUser": _r(ADMIN),
    "updateAdminUser": _r(ADMIN),
    # imports
    "getImportStatus": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "listImportRuns": _r(ADMIN),
    "runImport": _r(ADMIN),
    # reports
    "getSpendReport": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "getSpendCsv": _r(ADMIN, BUDGET_OWNER, FINANCE_VIEWER),
    "getReconciliation": _r(ADMIN, FINANCE_VIEWER),
    # notifications
    "listNotifications": _r(ADMIN, BUDGET_OWNER),
    "markNotificationRead": _r(ADMIN, BUDGET_OWNER),
    # events, audit
    "listEvents": _r(ADMIN, BUDGET_OWNER),
    "listAudit": _r(ADMIN),
}

# Операции, доступные пользователю с `must_change_password=true` до смены пароля.
ALLOWED_BEFORE_PASSWORD_CHANGE = frozenset({"me", "logout", "changePassword"})
