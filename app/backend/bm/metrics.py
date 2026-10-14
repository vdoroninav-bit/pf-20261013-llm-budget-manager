"""Метрики Prometheus (контракт, операция `metrics`). Только счётчики и длительности — без содержимого и ПДн.

Метрики процесса: при нескольких воркерах uvicorn каждый отдаёт свои значения
(multiprocess-режим — T086/T087).
"""

from prometheus_client import CONTENT_TYPE_PLAIN_0_0_4, REGISTRY, Counter, Gauge, Histogram, generate_latest

CONTENT_TYPE = CONTENT_TYPE_PLAIN_0_0_4

DECISION_LATENCY = Histogram(
    "decision_latency_seconds",
    "Время обработки POST /api/v1/decisions внутри сервиса",
    buckets=(0.001, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)
DECISIONS = Counter("decisions_total", "Решения по исходу и коду", ["outcome", "code"])
REPORTS = Counter("reports_total", "Отчёты о расходе по статусу обработки", ["status"])
RESERVATIONS_OPEN = Gauge("reservations_open", "Открытые резервы (по данным последнего прохода worker)")
IMPORT_LAG_HOURS = Gauge("import_lag_hours", "Отставание импорта провайдера, часов", ["provider"])
JOB_FAILURES = Counter("job_failures_total", "Сбои фоновых задач", ["job"])

# Нулевые ряды, чтобы метрики с метками были видны в /metrics до первого события.
for _provider in ("anthropic", "openai"):
    IMPORT_LAG_HOURS.labels(provider=_provider)


def render() -> bytes:
    return generate_latest(REGISTRY)
