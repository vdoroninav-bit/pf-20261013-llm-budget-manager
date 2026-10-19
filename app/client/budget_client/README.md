# budget_client — эталонный клиент LLM Budget Manager

Эталонная схема интеграции вызывающей стороны (FR-006, принцип III конституции, SC-002).
Только стандартная библиотека Python ≥ 3.11, без сторонних зависимостей и без телеметрии.
Сеть — только к адресу `base_url`; переменные `HTTP(S)_PROXY` не применяются.

```python
from pathlib import Path
from budget_client import BudgetClient, PolicyUnavailable

client = BudgetClient("http://bm.local:8080", api_key, Path("/var/lib/myapp/budget"))  # timeout_ms=50
try:
    d = client.decide({"type": "employee", "login": "ivanov"}, "claude-x", "chat",
                      input_tokens=1200, max_output_tokens=800)
except PolicyUnavailable:
    ...  # утилита недоступна, политика fail-closed: вызов модели отклонить (policy_unavailable)
if d.outcome == "deny":
    ...  # отказ утилиты: d.code, d.http_status_hint, d.message
# d.fail_open=True — утилита не ответила за 50 мс, вызов выполняется без решения
... вызов модели d.model ...
client.report(decision_id=d.decision_id, tokens={"input": 1180, "output": 640}, call_status="ok",
              report_id=str(uuid4()), subject={"type": "employee", "login": "ivanov"}, model=d.model)
```

| Поведение | Значение |
|---|---|
| Таймаут `decide()` | 50 мс на весь запрос (`timeout_ms`) |
| Сбой утилиты (таймаут, нет соединения, 5xx, 429) | `fail_mode` субъекта: `open` → `fail_open=True`; `closed` → `PolicyUnavailable` |
| Ошибка запроса (401, 403, 404, 422) | `ClientRequestError` — ошибка интеграции, не маскируется fail-open |
| Кэш `integration-config` и `fail_mode` по субъекту | `state_dir/config-<отпечаток ключа>.json`, обновление раз в `refresh_s` (60 с), `If-None-Match` |
| Буфер отчётов | `state_dir/buffer-<отпечаток ключа>.sqlite3`, `synchronous=FULL`, ключ `report_id` |
| Досылка | фон после каждого `report()`; `flush(max_batch=500)` — пакеты по 500, паузы 0,5→1→2… ≤ 30 с, 5 повторов |
| Отклонённые сервером строки (4xx строки) | остаются в буфере в состоянии `rejected`, см. `rejected()` |

Ключ API на диск не пишется. В буфере лежат тела отчётов (логин субъекта, модель, токены) —
каталог `state_dir` должен быть доступен только процессу вызывающей системы.

Тесты: `pytest tests/unit/test_client_buffer.py` (из корня проекта); отказной тест — `make up && make failover`.
