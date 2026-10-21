"""T032 / SC-003: подтверждённые отчёты не теряются при принудительной остановке (ADR-002: 200 только после COMMIT).

Стек поднят. 4 потока шлют пары «решение → отчёт» напрямую в HTTP API. Через 10 с — `docker compose kill api`
(при BM_STACK=local — SIGKILL всей группы процессов api: мастер uvicorn и воркеры), затем `start api`. Поток продолжается до 30 с; ошибки соединения в окне перезапуска
ожидаемы и не считаются. Проверка: КАЖДЫЙ report_id, на который API ответил 200, есть в БД ровно один раз.

Эталонный клиент здесь не нужен: проверяется сервер. Тест пропускается без BM_FAILOVER=1 (см. conftest.py).
"""

import threading
import time
import uuid
from datetime import UTC, datetime

import httpx

from tests.failover.stack import BASE_URL, STACK, kill, provision, report_counts, start, wait_ready

THREADS = 4
DURATION_S = 30
KILL_AT_S = 10


def _worker(
    key: str, model: str, t_end: float, confirmed: list[str], lock: threading.Lock, stats: dict[str, int]
) -> None:
    headers = {"X-Api-Key": key}
    with httpx.Client(base_url=BASE_URL, timeout=2, headers=headers) as http:
        while time.monotonic() < t_end:
            rid = str(uuid.uuid4())
            try:
                d = http.post(
                    "/api/v1/decisions",
                    json={
                        "subject": {"type": "service"},
                        "model": model,
                        "kind": "chat",
                        "input_tokens": 10,
                        "max_output_tokens": 10,
                    },
                )
                decision_id = d.json()["decision_id"] if d.status_code == 200 else None
                body = {
                    "report_id": rid,
                    "tokens": {"input": 10, "output": 10},
                    "call_status": "ok",
                }
                if decision_id:
                    body["decision_id"] = decision_id
                else:
                    body |= {
                        "subject": {"type": "service"},
                        "model": model,
                        "occurred_at": datetime.now(UTC).isoformat(),
                    }
                r = http.post("/api/v1/usage", json=body)
            except httpx.HTTPError:
                with lock:
                    stats["conn_errors"] += 1
                time.sleep(0.05)
                continue
            with lock:
                if r.status_code == 200:
                    confirmed.append(rid)
                else:
                    stats[f"http_{r.status_code}"] = stats.get(f"http_{r.status_code}", 0) + 1


def test_sc003_kill_api_under_load_loses_no_confirmed_report(stack: None) -> None:
    data = provision(("open",))
    confirmed: list[str] = []
    lock = threading.Lock()
    stats: dict[str, int] = {"conn_errors": 0}
    t_end = time.monotonic() + DURATION_S
    threads = [
        threading.Thread(
            target=_worker, args=(data.keys["open"], data.model, t_end, confirmed, lock, stats), daemon=True
        )
        for _ in range(THREADS)
    ]
    for t in threads:
        t.start()
    time.sleep(KILL_AT_S)
    with lock:
        before_kill = len(confirmed)
    try:
        kill("api")
    finally:
        start("api")
        wait_ready(120)
    for t in threads:
        t.join(timeout=DURATION_S + 30)

    assert before_kill > 0, f"до kill не было подтверждённых отчётов: {stats}"
    assert len(confirmed) > before_kill, f"после перезапуска API отчёты не принимались: {stats}"
    unexpected = {k: v for k, v in stats.items() if k.startswith("http_")}
    assert not unexpected, f"неожиданные HTTP-статусы на /usage: {unexpected}"
    counts = report_counts(confirmed)
    lost = [rid for rid, n in counts.items() if n == 0]
    dupes = [rid for rid, n in counts.items() if n > 1]
    assert not lost, f"потеряно {len(lost)} из {len(confirmed)} подтверждённых отчётов (SC-003)"
    assert not dupes
    print(  # итог для 15_qa_report.md (видно с `pytest -s`)
        f"SC-003 [{STACK}]: подтверждено 200 — {len(confirmed)} (до kill {before_kill}), "
        f"потеряно {len(lost)}, дублей {len(dupes)}; прочее {stats}"
    )
