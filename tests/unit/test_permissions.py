"""Права (T016): `PERMISSIONS` — единственная таблица `operationId → роли`, совпадает с `x-roles` контракта."""

from uuid import uuid4

import pytest

from bm.access.auth import hash_password, verify_password
from bm.access.authorize import Forbidden, Principal, Scope, authorize
from bm.access.permissions import PERMISSIONS, Role
from tests.contract.helpers import operation, operations

DEP_A, DEP_B = uuid4(), uuid4()


def _p(role: Role, department=None) -> Principal:  # type: ignore[no-untyped-def]
    return Principal(user_id=uuid4(), login=f"u-{role}", role=role, department_id=department)


def test_permissions_match_contract_x_roles() -> None:
    expected = {
        op_id: frozenset(Role(r) for r in operation(op_id)["x-roles"])
        for op_id in operations()
        if "x-roles" in operation(op_id)
    }
    assert PERMISSIONS == expected


def test_caller_and_system_operations_are_not_admin_operations() -> None:
    for op_id in ("createDecision", "reportUsage", "login", "healthz", "metrics"):
        assert op_id not in PERMISSIONS


def test_admin_allowed_everything() -> None:
    admin = _p(Role.ADMIN)
    for op_id in PERMISSIONS:
        authorize(admin, op_id, Scope(department_id=DEP_B))


def test_role_not_in_x_roles_is_forbidden() -> None:
    with pytest.raises(Forbidden):
        authorize(_p(Role.FINANCE_VIEWER), "listNotifications", Scope())
    with pytest.raises(Forbidden):
        authorize(_p(Role.BUDGET_OWNER, DEP_A), "createDepartment", Scope())


def test_unknown_operation_is_forbidden_by_default() -> None:
    with pytest.raises(Forbidden):
        authorize(_p(Role.ADMIN), "noSuchOperation", Scope())


def test_budget_owner_only_own_department() -> None:
    owner = _p(Role.BUDGET_OWNER, DEP_A)
    authorize(owner, "getDepartment", Scope(department_id=DEP_A))
    with pytest.raises(Forbidden):
        authorize(owner, "getDepartment", Scope(department_id=DEP_B))


def test_budget_owner_cannot_touch_objects_without_department() -> None:
    owner = _p(Role.BUDGET_OWNER, DEP_A)
    with pytest.raises(Forbidden):
        authorize(owner, "markNotificationRead", Scope(department_id=None, scoped=True))


def test_finance_viewer_sees_all_departments() -> None:
    authorize(_p(Role.FINANCE_VIEWER), "getDepartment", Scope(department_id=DEP_B))


def test_password_hash_is_argon2id() -> None:
    h = hash_password("correct-horse-battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct-horse-battery")
    assert not verify_password(h, "wrong")
    assert not verify_password("not-a-hash", "x")
