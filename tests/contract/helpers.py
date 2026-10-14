"""Проверка ответов по контракту `12_openapi.yaml` (T015). Ею пользуются все приёмочные тесты.

    resp = await client.get("/healthz")
    assert_matches_contract(resp, "healthz")

openapi-core используется напрямую, без интеграции с FastAPI (extra `fastapi` у openapi-core 0.23.1
требует fastapi < 0.140, а у нас 0.143.0 — 14_devops.md, отклонение 3).
"""

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import httpx
import yaml
from openapi_core import OpenAPI
from openapi_core.datatypes import RequestParameters
from werkzeug.datastructures import Headers

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "12_openapi.yaml"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


@cache
def contract() -> dict[str, Any]:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


@cache
def _openapi() -> OpenAPI:
    return OpenAPI.from_dict(contract())


@cache
def operations() -> dict[str, tuple[str, str]]:
    """operationId → (метод в нижнем регистре, шаблон пути)."""
    ops: dict[str, tuple[str, str]] = {}
    for path, item in contract()["paths"].items():
        for method in HTTP_METHODS:
            op = item.get(method)
            if op is not None:
                ops[op["operationId"]] = (method, path)
    return ops


def operation(operation_id: str) -> dict[str, Any]:
    method, path = operations()[operation_id]
    return contract()["paths"][path][method]


def _path_regex(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]+\})", template)
    return re.compile("^" + "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts) + "$")


@dataclass
class _Request:
    host_url: str
    path: str
    method: str
    body: bytes | None
    content_type: str
    parameters: RequestParameters = field(default_factory=RequestParameters)


@dataclass
class _Response:
    status_code: int
    headers: Headers
    content_type: str
    data: bytes | None


def assert_matches_contract(response: httpx.Response, operation_id: str) -> None:
    """Ответ соответствует операции `operation_id`: метод, путь, объявленный статус, тип и схема тела."""
    assert operation_id in operations(), f"в контракте нет operationId={operation_id}"
    method, template = operations()[operation_id]
    req = response.request
    assert req.method.lower() == method, f"{operation_id}: метод {req.method} ≠ {method.upper()}"
    assert _path_regex(template).match(req.url.path), f"{operation_id}: путь {req.url.path} не подходит под {template}"
    declared = operation(operation_id)["responses"]
    assert str(response.status_code) in declared, (
        f"{operation_id}: статус {response.status_code} не объявлен в контракте ({sorted(declared)}); "
        f"тело: {response.text[:500]}"
    )
    server = contract()["servers"][0]["url"]
    o_req = _Request(
        host_url=server,
        path=req.url.path,
        method=method,
        body=req.content or None,
        content_type=req.headers.get("content-type", ""),
        parameters=RequestParameters(
            query=dict(req.url.params),
            header=Headers(list(req.headers.items())),
            cookie={},
        ),
    )
    o_resp = _Response(
        status_code=response.status_code,
        headers=Headers(list(response.headers.items())),
        content_type=response.headers.get("content-type", ""),
        data=response.content,
    )
    try:
        _openapi().validate_response(o_req, o_resp)
    except Exception as e:
        details = getattr(e, "schema_errors", None) or getattr(e, "__cause__", None) or e
        raise AssertionError(f"{operation_id}: ответ не соответствует контракту: {e!r}; {details!r}") from e
