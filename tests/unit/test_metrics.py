"""T031 (14_tech_rulings.md, Б6): метрики собираются по всем процессам uvicorn.

Два процесса пишут в один каталог `PROMETHEUS_MULTIPROC_DIR`, `/metrics` отдаёт сумму счётчиков
и гистограмм; у gauge режим агрегации задан явно.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from bm import metrics

BACKEND = Path(__file__).resolve().parents[2] / "app" / "backend"

CHILD = """
from bm import metrics
metrics.DECISIONS.labels(outcome="allow", code="").inc()
metrics.DECISION_LATENCY.observe(0.004)
metrics.RESERVATIONS_OPEN.set({n})
"""


def _child(tmp: Path, n: int) -> None:
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(tmp), "PYTHONPATH": str(BACKEND)}
    subprocess.run([sys.executable, "-c", CHILD.format(n=n)], check=True, env=env, timeout=60)  # noqa: S603


def test_two_processes_are_summed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _child(tmp_path, 3)
    _child(tmp_path, 5)
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    text = metrics.render().decode()
    assert 'decisions_total{code="",outcome="allow"} 2.0' in text
    assert "decision_latency_seconds_count 2.0" in text
    assert "reservations_open 5.0" in text  # mostrecent: значение последней записи


def test_gauges_declare_multiprocess_mode() -> None:
    for gauge in (metrics.RESERVATIONS_OPEN, metrics.IMPORT_LAG_HOURS):
        assert gauge._multiprocess_mode == "mostrecent"


def test_single_process_without_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    assert b"decision_latency_seconds" in metrics.render()
