"""T032 / SC-002: отказ всех компонентов утилиты под потоком (сценарий 1.8, FR-006, принцип III).

Стек поднят: `make up && make failover` или `make up-local && BM_STACK=local make failover` (T093). Два подразделения: fail_mode=open и fail_mode=closed, у каждого сервисный ключ.
Эталонный клиент `budget_client` (T033) шлёт 20 rps в течение 60 с (по 10 rps на подразделение).
Через 5 с — остановка api, worker, postgres (`docker compose stop` или, при BM_STACK=local, SIGTERM/SIGINT →
до 10 с → SIGKILL группы процессов, tests/failover/stack.py). Ожидается:
- 100% вызовов подразделения open выполняются (ответ утилиты или fail-open);
- 100% вызовов подразделения closed, начатых после остановки, получают PolicyUnavailable;
- никаких иных исключений клиента;
- после `start` и `flush()` каждый report_id выполненного вызова есть в БД ровно один раз.

Интерфейс клиента — 12_plan.md §6: BudgetClient(base_url, api_key, state_dir, timeout_ms=50),
decide(subject, model, kind, **kw) → ClientDecision(outcome, fail_open, decision_id, …) или PolicyUnavailable,
report(*, decision_id, tokens, call_status, **kw) — report_id, subject, model, occurred_at передаются в kw,
flush(max_batch=500) → число досланных отчётов.

До T033 тест падает на импорте budget_client (ImportError).
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.failover.stack import BASE_URL, STACK, provision, report_counts, start, stop, wait_ready

RPS_PER_DEPT = 10
DURATION_S = 60
STOP_AT_S = 5
SERVICES = ("api", "worker", "postgres")
WARMUP_ATTEMPTS = 5  # 3Б9: прогрев повторяется до ответа без fail_open, не больше 5 попыток


@dataclass
class Call:
    started: float
    mode: str
    result: str  # served | fail_open | policy_unavailable | error:<класс>
    report_id: str | None = None


@dataclass
class Journal:
    calls: list[Call] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, c: Call) -> None:
        with self.lock:
            self.calls.append(c)


def _load(
    client: Any, policy_unavailable: type[Exception], mode: str, model: str, journal: Journal, t_end: float
) -> None:
    period = 1 / RPS_PER_DEPT
    subject = {"type": "service"}
    next_at = time.monotonic()
    while next_at < t_end:
        started = time.monotonic()
        try:
            dec = client.decide(subject, model, "chat", input_tokens=10, max_output_tokens=10)
        except policy_unavailable:
            journal.add(Call(started, mode, "policy_unavailable"))
        except Exception as e:
            journal.add(Call(started, mode, f"error:{type(e).__name__}"))
        else:
            rid = str(uuid.uuid4())
            # вызов модели выполнен (её здесь нет — утилита модели не вызывает), сообщаем расход
            client.report(
                decision_id=getattr(dec, "decision_id", None),
                tokens={"input": 10, "output": 10},
                call_status="ok",
                report_id=rid,
                subject=subject,
                model=model,
                occurred_at=datetime.now(UTC).isoformat(),
            )
            journal.add(
                Call(started, mode, "fail_open" if getattr(dec, "fail_open", False) else "served", rid)
            )
        next_at += period
        time.sleep(max(0.0, next_at - time.monotonic()))


def test_sc002_stop_all_components_fail_open_and_closed(stack: None, tmp_path: Path) -> None:
    from budget_client.client import BudgetClient, PolicyUnavailable  # T033

    data = provision(("open", "closed"))
    clients = {
        mode: BudgetClient(BASE_URL, data.keys[mode], tmp_path / mode, timeout_ms=50)
        for mode in ("open", "closed")
    }
    # прогрев: клиент получает integration-config (fail_mode подразделения) и кэширует его на диске.
    # Первый вызов может не уложиться в 50 мс (холодный старт, общая 2-CPU среда) и уйти в fail-open —
    # повторяем до ответа утилиты, не больше WARMUP_ATTEMPTS раз (3Б9); сам assert не ослаблен.
    for mode, c in clients.items():
        warm: Any = None
        for _ in range(WARMUP_ATTEMPTS):
            try:
                warm = c.decide({"type": "service"}, data.model, "chat", input_tokens=1, max_output_tokens=1)
            except PolicyUnavailable:
                warm = None
            if warm is not None and not getattr(warm, "fail_open", False):
                break
            time.sleep(0.2)
        assert warm is not None and warm.outcome == "allow" and not getattr(warm, "fail_open", False), (
            f"{mode}: стек не отвечает за {WARMUP_ATTEMPTS} попыток прогрева"
        )

    journal = Journal()
    t0 = time.monotonic()
    t_end = t0 + DURATION_S
    threads = [
        threading.Thread(
            target=_load, args=(c, PolicyUnavailable, mode, data.model, journal, t_end), daemon=True
        )
        for mode, c in clients.items()
    ]
    stopped_at = None
    try:
        for t in threads:
            t.start()
        time.sleep(STOP_AT_S)
        stop(*SERVICES)
        stopped_at = time.monotonic()
        for t in threads:
            t.join(timeout=DURATION_S + 30)
    finally:
        start(*reversed(SERVICES))
        wait_ready(120)

    assert stopped_at is not None
    calls = journal.calls
    errors = [c for c in calls if c.result.startswith("error")]
    assert not errors, f"исключения клиента помимо PolicyUnavailable: {sorted({c.result for c in errors})}"
    total = len(calls)
    assert total >= 0.9 * 2 * RPS_PER_DEPT * DURATION_S, (
        f"поток ниже 20 rps: {total} вызовов за {DURATION_S} с"
    )

    open_calls = [c for c in calls if c.mode == "open"]
    closed_down = [c for c in calls if c.mode == "closed" and c.started >= stopped_at]
    open_down = [c for c in open_calls if c.started >= stopped_at]
    assert open_down and closed_down, "в окне отказа нет вызовов"
    # 100% open выполняются, в окне отказа — через fail-open
    assert all(c.result in ("served", "fail_open") for c in open_calls)
    assert all(c.result == "fail_open" for c in open_down)
    # 100% closed в окне отказа отклоняются клиентом
    rejected = sum(c.result == "policy_unavailable" for c in closed_down)
    assert rejected == len(closed_down), f"closed: отклонено {rejected} из {len(closed_down)}"

    # досылка буфера после восстановления
    deadline = time.monotonic() + 180
    for c in clients.values():
        while c.flush() > 0:
            assert time.monotonic() < deadline, "flush() не опустошил буфер за 180 с"
        assert c.flush() == 0
    executed = [c.report_id for c in calls if c.report_id is not None]
    counts = report_counts(executed)
    missing = [rid for rid, n in counts.items() if n == 0]
    dupes = [rid for rid, n in counts.items() if n > 1]
    assert not missing, f"не досланы {len(missing)} из {len(executed)} отчётов"
    assert not dupes, f"дубли в БД: {len(dupes)}"
    print(  # итог для 15_qa_report.md (видно с `pytest -s`)
        f"SC-002 [{STACK}]: вызовов {total}; open {len(open_calls)} (в окне отказа {len(open_down)}, все fail_open); "
        f"closed в окне отказа {len(closed_down)}, отклонено {rejected}; отчётов {len(executed)}, "
        f"в БД ровно по одному — {len(executed) - len(missing) - len(dupes)}"
    )
