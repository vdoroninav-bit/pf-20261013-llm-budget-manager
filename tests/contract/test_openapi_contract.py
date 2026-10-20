"""Контрактный тест (T015): контракт валиден, ответы проверяются хелпером, операции зарегистрированы.

(в) Каждая `operationId` контракта зарегистрирована в приложении с тем же методом и путём.
Операции, роутеры которых ещё не написаны, перечислены в `PENDING` и помечены `xfail(strict=True)`:
как только роутер появится, тест станет XPASS и упадёт — операцию нужно убрать из `PENDING`.
T071 снимает xfail для всех операций, кроме MCP.
"""

from typing import Any

import httpx
import pytest
from openapi_spec_validator import validate

from tests.contract.helpers import assert_matches_contract, contract, operations

# Операции, которые ещё не реализованы (задача, которая их закрывает).
PENDING: dict[str, str] = {}


def _registered(app: Any) -> dict[str, tuple[set[str], str]]:
    """operationId → (методы в нижнем регистре, путь) по схеме, которую строит само приложение.

    В FastAPI 0.143 `include_router` ленивый, поэтому маршруты берутся из `app.openapi()`, а не `app.routes`.
    """
    out: dict[str, tuple[set[str], str]] = {}
    for path, item in app.openapi()["paths"].items():
        for method, op in item.items():
            op_id = op.get("operationId")
            if op_id:
                methods, _ = out.setdefault(op_id, (set(), path))
                methods.add(method.lower())
    return out


def _params() -> list[Any]:
    params = []
    for op_id in sorted(operations()):
        marks = []
        if op_id in PENDING:
            marks.append(pytest.mark.xfail(strict=True, reason=f"роутер появится в {PENDING[op_id]}"))
        params.append(pytest.param(op_id, marks=marks, id=op_id))
    return params


# ---------------------------------------------------------------- (а) контракт валиден


def test_contract_is_valid_openapi() -> None:
    validate(contract())


def test_pending_lists_only_contract_operations() -> None:
    assert set(PENDING) <= set(operations()), set(PENDING) - set(operations())


# ---------------------------------------------------------------- (б) хелпер assert_matches_contract


async def test_helper_accepts_contract_response(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert_matches_contract(resp, "healthz")


async def test_helper_rejects_undeclared_status(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    fake = httpx.Response(418, json={"status": "ok"}, request=resp.request)
    with pytest.raises(AssertionError, match="не объявлен"):
        assert_matches_contract(fake, "healthz")


async def test_helper_rejects_body_outside_schema(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    fake = httpx.Response(200, json={"status": "maybe"}, request=resp.request)
    with pytest.raises(AssertionError, match="не соответствует контракту"):
        assert_matches_contract(fake, "healthz")


async def test_helper_rejects_wrong_operation(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    with pytest.raises(AssertionError):
        assert_matches_contract(resp, "readyz")


# ---------------------------------------------------------------- (в) операции зарегистрированы


@pytest.mark.parametrize("operation_id", _params())
def test_operation_registered(operation_id: str, settings: Any) -> None:
    from bm.main import create_app

    method, path = operations()[operation_id]
    registered = _registered(create_app(settings))
    assert operation_id in registered, (
        f"operationId={operation_id} ({method.upper()} {path}) не зарегистрирован"
    )
    methods, app_path = registered[operation_id]
    assert method in methods and app_path == path, (operation_id, methods, app_path, method, path)


def test_app_has_no_operations_outside_contract(settings: Any) -> None:
    from bm.main import create_app

    extra = set(_registered(create_app(settings))) - set(operations())
    assert not extra, f"в приложении есть операции вне контракта: {sorted(extra)}"


async def test_app_does_not_serve_swagger_ui(client: httpx.AsyncClient) -> None:
    """Swagger UI тянет скрипты с CDN — запрещено принципом IV; контракт — 12_openapi.yaml."""
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 404, path
