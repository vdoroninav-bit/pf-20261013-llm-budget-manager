# 15. Отчёт QA — LLM Budget Manager v1

> Решения QA (`QA: PASS` / `QA: FAIL`) пока нет: первая строка появится после релизного прогона T089.

## Приёмочные тесты до реализации (T020, T030, T032, T040) — 2026-10-15

**Итог.** 99 новых тестов собраны и падают по причине «не реализовано» (404 у отсутствующих маршрутов). Они помечены `xfail(strict=True)`, поэтому `make test` зелёный: **240 passed, 149 xfailed** (до работы было 240 passed, 50 xfailed). 2 отказных теста без стека пропускаются с указанием причины. Контракт 1.0.2 (`14_tech_rulings.md`, Б1) на тесты не влияет: в нём только добавлены ответы 401/403 у операций админки.

### Что сделано

| Задача | Файлы | Тестов | Метка xfail |
|---|---|---|---|
| T020 | `tests/acceptance/test_us1_decisions.py` (1.1–1.7, проверка всех лимитов), `test_edge_cases.py`, `test_messages_ru.py`, `tests/integration/test_concurrency.py` | 52 | `T021` (тест подписок ≥ 100% — дополнительно `T051`) |
| T030 | `tests/acceptance/test_us2_usage.py` (2.1–2.4, 2.6, поздний отчёт, `decision_not_followed`, пакет 1000/10 дублей, 413, баланс = ручной пересчёт), `tests/integration/test_settle_db.py` (`test_expense_date_is_decision_date`, переход месяца, цена на момент решения, обе строки реестра, `report_key`) | 27 | `T031` |
| T040 | `tests/acceptance/test_us3_config.py` (3.1–3.5, dry_run, основная группа и предупреждение, лимиты 5 000 / 20 000 / 60 000, цена с 15-го, `fx_rate_missing`, аудит «было/стало», `test_removed_employee_keeps_period_spend`, ключ показывается один раз и отзыв действует сразу, `revealPseudonym`, права), `tests/fixtures/ad_groups_ok.csv`, `ad_groups_errors.csv` | 20 | `T041/T042` |
| T032 | `tests/failover/{conftest.py, stack.py, test_sc002_failover.py, test_sc003_kill.py}`, цель `make failover` в `Makefile` | 2 | не размечены: в `make test` не входят |
| общее | `tests/fixtures/qa.py`: каталог A/B/C/N/Z/E, засев `ledger_daily`, `bump_config`, `decide`/`report`/`balance` с `assert_matches_contract`, вход в админку | — | — |

Сценарий 2.5 покрыт тестом `test_concurrency` (T020). Сценарий 3.6 покрыт тестом `test_edge_cases::test_model_unknown`.

**Как устроены метки.** На уровне модуля стоит `pytest.mark.xfail(strict=True, raises=(AssertionError, ImportError), reason="T0xx: …")`. Благодаря `raises` ошибка в самом тесте (KeyError, TypeError) даёт FAIL, а не xfail. Когда реализация готова, получается XPASS, `make test` падает, и реализатор снимает метку модуля.

**Отказные тесты.** Без `BM_FAILOVER=1` тесты пропускаются с причиной «требуют Docker и поднятого стека: `make up && make failover`». Цель `make failover` ставит `BM_FAILOVER=1`. Если при этом стека нет, тест **падает**, а не пропускается, чтобы гейт релиза не позеленел молча. В `tests/failover/conftest.py` корневой `egress_guard` переопределён: разрешён только loopback. Данные готовятся через API админки, проверка в БД идёт через `docker compose exec postgres psql` (сеть `core` — internal).

### Команды и вывод (сокращённо)

```text
$ uv run … pytest --collect-only -q
391 tests collected in 2.36s

$ uv run … pytest <новые файлы> -q
2 skipped, 99 xfailed

$ uv run … pytest <новые файлы> -q --runxfail --tb=line | причины
 56 AssertionError: createDecision: 404 …
 14 AssertionError: {"…status":404 …}
  6 AssertionError: importOrg: 404 …
  3 AssertionError: reportUsage: 404 …
 20 assert 404 == 200/201/401/403/413/422 …
   (99 из 99 — 404 на отсутствующем маршруте; ни одной ошибки в самих тестах)

$ make failover            # Docker-демон не запущен
E Failed: Docker-демон недоступен: Cannot connect to the Docker daemon …   (2 errors — ожидаемо)

$ DATABASE_URL=postgresql://bm:bm@127.0.0.1:5432/bm_test SKIP_FRONTEND=1 make test
240 passed, 149 xfailed in 79.60s
Contracts: 3 kept, 0 broken.
run-tests.sh full: OK

$ ruff check tests && ruff format --check tests
All checks passed! / 30 files already formatted
```

### Отклонения

1. **`Makefile`** (зона devops). Цель `failover` вынесена из заглушки `backup restore load failover e2e`. В комментарии Makefile она закреплена за QA. Остальные заглушки не тронуты.
2. **Вспомогательный модуль `tests/fixtures/qa.py`.** `seed.py` (T014) не менялся.
3. **Под живым стеком T032 не проверялся:** Docker не запущен. Проверено только, что без стека тесты пропускаются, а под `make failover` падают с понятной причиной.

### Договорённости, на которые опираются тесты (вопросы техлиду и backend)

1. **Истечение резервов в тестах** вызывается через `app.state.budget.expire_reserves(now) -> int` (`tests/fixtures/qa.py::expire_reserves`). В плане §6 есть `Budget.expire_reserves`, но не сказано, где хранится экземпляр. Если backend выберет другое место, поправить нужно только этот хелпер.
2. **Аудит `subject_out_of_scope`:** ожидается `actor = "caller:<id>"` (как в описании `AuditEntry.actor`) и `action = "forbidden_access"` (как в T016).
3. **Флаги решений в `call_record.flags`:** `over_limit_observed` (1.7) и `model_not_allowed_observed`. Это значения `EventFlag` из контракта.
4. **Номера строк в сводке импорта AD** считаются по файлу, заголовок — строка 1 (ошибки в `ad_groups_errors.csv` — строки 3, 4, 5).
5. **`budget_client` (T033):** `decide()` возвращает объект с `outcome`, `fail_open` и `decision_id`. `report()` принимает `report_id`, `subject`, `model`, `occurred_at` через `**kw`, а `report_id` задаёт вызывающий: иначе SC-002 нельзя проверить «каждый report_id ровно один раз». Пакет ищется в `app/client/budget_client` (добавляется в `sys.path`).
6. **Отказные тесты:** нужна учётная запись админки со сменённым паролем (`BM_ADMIN_LOGIN` / `BM_ADMIN_PASSWORD`, по умолчанию `ADMIN_INITIAL_*` из `deploy/.env`). Пользователь и БД в контейнере postgres по умолчанию `bm` / `bm` (`BM_PG_USER` / `BM_PG_DB`). Значения нужно сверить с T087.
7. **Тест подписок ≥ 100%** засевает `subscription_month` напрямую. Если T051 считает M иначе (без материализации), тест нужно уточнить вместе с T050.
