"""Метрики Prometheus (контракт, операция `metrics`). Только счётчики и длительности — без содержимого и ПДн.

Несколько воркеров uvicorn (14_tech_rulings.md, Б6): если задан `PROMETHEUS_MULTIPROC_DIR`, каждый процесс
пишет значения в файлы этого каталога, а `/metrics` собирает их всех (`MultiProcessCollector`): счётчики
и гистограммы суммируются, у gauge режим агрегации задан явно (`mostrecent` — последнее записанное значение).
Каталог очищается при старте контейнера (tmpfs, T087). Без переменной — метрики одного процесса.
Gauge открытых резервов убран (14_tech_rulings.md, пакет 2, В1-6): его ставил worker,
а `/metrics` отдаёт `api`.
"""

import os

from prometheus_client import (
    CONTENT_TYPE_PLAIN_0_0_4,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)

MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"

CONTENT_TYPE = CONTENT_TYPE_PLAIN_0_0_4

DECISION_LATENCY = Histogram(
    "decision_latency_seconds",
    "Время обработки POST /api/v1/decisions внутри сервиса",
    buckets=(0.001, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)
DECISIONS = Counter("decisions_total", "Решения по исходу и коду", ["outcome", "code"])
REPORTS = Counter("reports_total", "Отчёты о расходе по статусу обработки", ["status"])
IMPORT_LAG_HOURS = Gauge(
    "import_lag_hours", "Отставание импорта провайдера, часов", ["provider"], multiprocess_mode="mostrecent"
)
JOB_FAILURES = Counter("job_failures_total", "Сбои фоновых задач", ["job"])

# Нулевые ряды, чтобы метрики с метками были видны в /metrics до первого события.
for _provider in ("anthropic", "openai"):
    IMPORT_LAG_HOURS.labels(provider=_provider)


def render() -> bytes:
    if os.environ.get(MULTIPROC_ENV):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry)
    return generate_latest(REGISTRY)
