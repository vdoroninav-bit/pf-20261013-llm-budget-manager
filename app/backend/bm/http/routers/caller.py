"""Decision API вызывающих систем (тег caller, ключ `X-Api-Key`): решения, расход, остатки, конфигурация.

Тонкий адаптер над `bm.budget.service.Budget`: разбор запроса, перевод `BudgetError` в Problem.
Отказ по решению — не ошибка: `createDecision` отвечает 200 с `outcome=deny` (ADR-005).
"""

import json
import time
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from bm import metrics
from bm.budget.errors import BudgetError
from bm.budget.schemas import (
    BalanceReport,
    Decision,
    DecisionRequest,
    IntegrationConfig,
    UsageBatchResult,
    UsageReport,
    UsageResult,
)
from bm.http.deps import BudgetDep, CallerDep, budget_problem
from bm.http.errors import ProblemError

BATCH_MAX = 1000

router = APIRouter(tags=["caller"])


@router.post("/decisions", operation_id="createDecision")
async def create_decision(body: DecisionRequest, caller: CallerDep, budget: BudgetDep) -> Decision:
    started = time.perf_counter()
    try:
        return await budget.decide(body, caller)
    except BudgetError as exc:
        raise budget_problem(exc) from exc
    finally:
        metrics.DECISION_LATENCY.observe(time.perf_counter() - started)


@router.get("/integration-config", operation_id="getIntegrationConfig", response_model=IntegrationConfig)
async def get_integration_config(request: Request, caller: CallerDep, budget: BudgetDep) -> Response:
    cfg = await budget.integration_config(caller)
    etag = f'"{cfg.version}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(cfg.model_dump(mode="json"), headers={"ETag": etag})


@router.post("/usage", operation_id="reportUsage")
async def report_usage(body: UsageReport, caller: CallerDep, budget: BudgetDep) -> UsageResult:
    try:
        return await budget.report(body, caller)
    except BudgetError as exc:
        raise budget_problem(exc) from exc


def _row_problem(status: int, code: str, detail: str) -> dict[str, Any]:
    titles = {401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 422: "Unprocessable Content"}
    return {
        "type": "about:blank",
        "title": titles.get(status, "Error"),
        "status": status,
        "code": code,
        "detail": detail,
    }


def _invalid_row(exc: ValidationError) -> dict[str, Any]:
    fields = sorted({".".join(str(p) for p in e.get("loc", ())) or "строка" for e in exc.errors()})
    return _row_problem(422, "validation_error", "Некорректная строка пакета: " + ", ".join(fields) + ".")


@router.post("/usage/batch", operation_id="reportUsageBatch", response_model=UsageBatchResult)
async def report_usage_batch(request: Request, caller: CallerDep, budget: BudgetDep) -> JSONResponse:
    """FR-011: до 1000 строк, каждая обрабатывается независимо (своя транзакция, свой результат)."""
    try:
        payload = json.loads(await request.body())
    except ValueError as exc:
        raise ProblemError(422, "validation_error", "Некорректный запрос: тело — не JSON.") from exc
    if not isinstance(payload, dict) or set(payload) != {"items"} or not isinstance(payload["items"], list):
        raise ProblemError(422, "validation_error", "Некорректный запрос: ожидается объект {items: [...]}.")
    items: list[Any] = payload["items"]
    if len(items) > BATCH_MAX:
        raise ProblemError(
            413, "batch_too_large", f"В пакете больше {BATCH_MAX} строк: разбейте его на части."
        )
    if not items:
        raise ProblemError(422, "validation_error", "Некорректный запрос: пакет пуст.")

    results: list[dict[str, Any] | None] = [None] * len(items)
    valid: list[tuple[int, UsageReport]] = []
    for i, item in enumerate(items):
        try:
            valid.append((i, UsageReport.model_validate(item)))
        except ValidationError as exc:
            results[i] = {"index": i, "ok": False, "error": _invalid_row(exc)}
    settled = await budget.settle([r for _, r in valid], caller)
    for (i, _), res in zip(valid, settled, strict=True):
        if isinstance(res, BudgetError):
            results[i] = {"index": i, "ok": False, "error": _row_problem(res.status, res.code, res.detail)}
        else:
            results[i] = {"index": i, "ok": True, "result": res.model_dump(mode="json")}
    return JSONResponse({"results": results})


@router.get("/balance", operation_id="getCallerBalance")
async def get_caller_balance(
    caller: CallerDep,
    budget: BudgetDep,
    login: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
) -> BalanceReport:
    try:
        return await budget.caller_balance(caller, login)
    except BudgetError as exc:
        raise budget_problem(exc) from exc
