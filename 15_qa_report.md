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

## Исправление двух тестов и приёмочные тесты до реализации (T050, T060, T070) — 2026-10-19

**Итог.** Два дефекта в тестах QA, найденные backend, исправлены, с обоих тестов снят точечный `xfail`, оба зелёные. Написаны 77 новых тестов для T050/T060/T070: 61 собран и падает по причине «не реализовано» (`xfail(strict=True, raises=(AssertionError, ImportError))`), 16 — проверки безопасности, которые уже сейчас проходят на готовом коде (регрессионные, без `xfail`). `make test` на `bm_test_qa`: **478 passed, 83 xfailed**, import-linter 3/3, `run-tests.sh full: OK`. `ruff check` / `ruff format --check` по `app/backend tests deploy/ci` чистые.

### 1. Исправленные тесты (дефекты QA из 14_dev_backend.md)

| Тест | Причина | Исправление |
|---|---|---|
| `test_us2_usage.py::test_decision_not_followed_charges_actual_model` | Засев 99 980 ₽ на сегодня превышал дневной лимит подразделения (10 000 ₽), по §4.5 п. 5 правильный ответ — `deny` | Засев перенесён на вчера (`day=TODAY − 1`): остаток месяца 20 ₽, дневной лимит не затронут, A → `redirect` на C. Проверки не менялись |
| `test_us3_config.py::test_3_5_new_price_from_15th_old_events_unchanged` | Часы сдвигались на 22 ч, а сессия живёт 12 ч → 401 на `listModelPrices` | После `clock.set(15-е 10:00)` выполняется повторный вход. Проверки не менялись |

```text
$ pytest tests/acceptance/test_us2_usage.py::test_decision_not_followed_charges_actual_model \
         tests/acceptance/test_us3_config.py::test_3_5_new_price_from_15th_old_events_unchanged
2 passed in 4.04s
```

### 2. Новые тесты

| Задача | Файлы | Тестов | Метка |
|---|---|---|---|
| T050 | `tests/acceptance/test_us4_subscriptions.py` — 4.1 (241,93 ₽ за день, 1 451,61 за неделю, 2 419,35 за месяц; амортизация в `decide`), 4.2 (сотрудник + подразделение), 4.3 (курс месяца / последний + `last_known_rate_used` / `fx_rate_missing`, курс задним числом не меняет M), 4.4 (предпросмотр до сохранения без побочных эффектов, `subs_share` один раз админу и владельцу, задача 1-го числа 00:05 фиксирует M и предупреждает), 4.5 (закрытие, удаление = сегодня, денежные поля неизменяемы), 4.6 | 15 | `T051` |
| T060 | `tests/acceptance/test_us5_import.py` — 5.1–5.7, справочник (`source_exists`, `unsupported_source_kind`), `runImport`/`listImportRuns`/`getImportStatus`, смена `mode` → дельта, коннекторы на подменном транспорте | 22 | `T061`; коннекторы `T062`/`T063`; сверка и отчёт позднего импорта `T071` |
| | `tests/integration/test_import_pipeline.py` — ключ строки, 4 параллельных запуска без двойного списания, инвариант «реестр = Σ charged», строка без курса, неудачный запуск ничего не меняет | 5 | `T061` |
| | `tests/acceptance/test_sc007_reference_balance.py` — эталонный набор (токены + резерв + импорт USD + подписка USD + истечение резерва + переход месяца + поздний импорт), ручной расчёт до копейки в docstring | 2 | `T051 + T061`; админка и прослеживаемость `T071` |
| | `tests/fixtures/anthropic/*.synthetic.json`, `tests/fixtures/openai/*.synthetic.json` + `README.md` в каждой папке | — | — |
| T070 | `tests/acceptance/test_us6_reports_roles.py` — 6.1 (панель владельца, только своё подразделение, псевдонимы, 403 + аудит на чужое), 6.2 (CSV: BOM, `;`, заголовок по контракту, без идентификаторов сотрудников, защита от CSV-инъекции `= + - @`), 4.6 «не распределено», 6.3 (80% и 100% по одному на период, `webhook_status=disabled`), 6.5 (аудит подписок, источников, ролей через `listAudit`), журнал событий | 8 | `T071` |
| | `tests/security/test_roles_matrix.py` — матрица из `PERMISSIONS` × контракт: запрещённая роль → 403 `forbidden` + `forbidden_access` в аудите; разрешённая → не 401/403; без сессии → 401; владелец против чужого подразделения; списки владельца | 12 (7 проходят, 5 `xfail`) | непостроенные операции (`PENDING` контрактного теста) — `T051/T061/T071` |
| | `tests/security/test_no_content.py` — белый список текстовых колонок схемы, нет колонок `prompt/messages/content…`, поля содержимого → 422 и их значения нет ни в ответе, ни в журнале, ни в БД | 7 (проходят) | — |
| | `tests/security/test_secrets_not_leaked.py` — `*_ADMIN_KEY` нет в `Settings`, ответах, журнале, дампе БД; API импорта; запуск коннектора с ответами 200/401/500 | 6 (2 проходят) | `T061`, `T062` |
| общее | `tests/fixtures/qa.py`: `relogin`, `session_as`, `worker_tick`, `notifications`, `one`; `tests/fixtures/imports.py`: `cost_line`, `recorded`, `run_import`, `provider_source`, `FakeProvider` (MockTransport с пагинацией) | — | — |

Метки на уровне функции перекрывают метку модуля. Поэтому тест, который ждёт T071, остаётся `xfail` после снятия метки T061 с модуля.

```text
$ pytest <8 новых файлов> -q
16 passed, 61 xfailed in 76.34s

$ pytest <8 новых файлов> -q --runxfail --tb=line | причины
 20 ModuleNotFoundError: bm.provider_import.sources.base
 14 createSubscription: 404          3 previewSubscription: 404
  8 ModuleNotFoundError: bm.provider_import.sources.anthropic
  2 …sources.recorded                1 …sources.openai
  5 404 not_found на отсутствующих маршрутах   2 assert 404 == 409 (runImport)
  3 createProviderSource: маршрута нет в приложении   1 без сессии 404   1 чужое подразделение → 404
  1 ожидался ровно один элемент, получено 0 (threshold_80: задачи порогов ещё нет)
   (61 из 61 — нет модуля или маршрута; ошибок в самих тестах нет)

$ DATABASE_URL=postgresql://bm:bm@127.0.0.1:5432/bm_test_qa SKIP_FRONTEND=1 make test
478 passed, 83 xfailed in 208.57s
Contracts: 3 kept, 0 broken.
run-tests.sh full: OK
```

### Дефекты

Новых дефектов в коде нет. Проходящие проверки безопасности на готовом коде: матрица ролей по 30 построенным операциям (403 + аудит, 401 без сессии), изоляция владельца бюджета (`getDepartment`, `getEmployee`, `markNotificationRead`, списки), отказ полей содержимого без эха в ответе и журнале, отсутствие ключей провайдеров в ответах, журнале и БД.

### Отклонения

1. **Папка `tests/e2e/` не создана.** Как и в прошлый раз, тесты лежат там, где их требует `12_tasks.md` (`tests/acceptance`, `tests/integration`, `tests/security`), и запускаются `make test`.
2. **Фикстуры провайдеров синтетические.** Записанных ответов спайка в репозитории нет. Структура OpenAI сверена с типами SDK `openai` 3.35.1 (`types/admin/organization/usage_costs_response.py`) [Факт]. Структура Anthropic `cost_report` собрана по описанию в `10_tech_qa.md` / `11_arch_classic.md`: документация провайдера из этой среды недоступна, а в SDK `anthropic` 1.15.0 этого эндпоинта нет. Что сверить на спайке, перечислено в `tests/fixtures/anthropic/README.md`.

### Договорённости, на которые опираются тесты (backend, проверить при T051/T061–T063/T071)

1. **Конвейер импорта:** `pipeline.run(pool, source, day_from, day_to, now)` — к сигнатуре из 12_plan.md §6 добавлен пул. Тестовый источник: `RecordedSource(provider, lines, *, error_class=None)`, где `error_class` — значение `ImportErrorClass`. Коннекторы: `AnthropicSource(settings, *, transport=None)` / `OpenAISource(...)`, `transport` передаётся в `egress.get_client`; при выключенном флаге — `EgressDisabled`. Если реализация будет другой, правится только `tests/fixtures/imports.py`.
2. **Worker:** задачи подписок, импорта и порогов входят в `default_jobs`. Тесты вызывают один такт через `qa.worker_tick(app)`; при новой сигнатуре правится только он.
3. **Строка импорта без курса USD** сохраняется с `amount_kop = NULL`, в реестр не идёт. После ввода курса следующая загрузка окна списывает её.
4. **Сумма Anthropic `amount` — в центах** [Гипотеза, сверить на спайке]. Суммы OpenAI переводятся в `Decimal` без двоичных «хвостов» (`Decimal(str(value))`).

### Вопросы техлиду

1. **Доля подписок (FR-020, `SubscriptionPreview.amortization_rub`, `subs_share`)** в тестах считается за **весь** период: месяц = M. Иначе предупреждение «при наступлении нового периода» не срабатывает, ведь в первый день доля ничтожна. Эту трактовку проверяют `test_4_4_preview_month_counts_whole_month_with_existing_subscriptions` и `test_4_4_new_month_job_materializes_month_and_warns`. Подтвердите. Для дневного лимита обе трактовки совпадают.
2. **Нужно ли запрещать `endSubscription` датой в прошлом** (422)? Такое закрытие стёрло бы уже прошедшие дни, а это противоречит 4.5. Сейчас тестом не закреплено.
3. **`getSpendReport` для «не распределено»:** тест ждёт, что подписка учитывается по прошедшим дням (1 400 ₽ на 14.10 при M = 3 100 ₽), а не за весь месяц. Подтвердите.
