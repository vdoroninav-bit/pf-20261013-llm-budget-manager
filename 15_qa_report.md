QA: CONDITIONAL

# 15. Отчёт QA — LLM Budget Manager v1

> Решение QA — по релизному прогону T089 (SHA `d98ba2d`), см. раздел «Релизный прогон и гейты (T089)» в конце. FAIL нет. CONDITIONAL из-за обязательного гейта SC-001 (PASS-замена) и лицензии `certifi` (ждёт юриста). Список непроверенного передаётся в `16_release.md`.

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

## Отказные тесты на стеке из локальных процессов и нагрузка (T093, T086) — 2026-10-21

**Итог.** T093 закрыта: SC-002 и SC-003 — **PASS-замена**, `make test` зелёный без `xfail`. T086 не закрыта: нагрузочный тест готов и даёт **SC-001: FAIL** из-за дефекта поставки (ниже Д-1); **SC-004: PASS**. Решения `QA:` по-прежнему нет, оно появится в T089.

### Что сделано
- **T093** (продолжение прерванной попытки, без переписывания):
  - `tests/failover/stack.py`: драйвер `BM_STACK=compose|local`. В режиме local `stop` вызывает `stack.sh stop` (SIGTERM, ожидание до 10 с, затем SIGKILL), `kill` — SIGKILL группы процессов, SQL идёт через asyncpg в кластер стека (`DATABASE_URL` на 5432 запрещён проверкой). При первом входе временный пароль администратора сменяется.
  - Прогрев SC-002: до 5 попыток `decide` без `fail_open` (3Б9).
  - 3Б5: в `test_6_2_csv_injection_is_neutralized` добавлен `bump_config`, `xfail` снят.
  - 3Б2: два теста `endSubscription` — дата в прошлом и повторное открытие закончившейся подписки дают 422.
  - Итоговые строки с числами печатаются в SC-002/SC-003 (видны с `pytest -s`).
- **T086**: `tests/load/{targets.py, run_load.sh}`, цель `make load`.
  - Подготовка: 30 моделей, 10 подразделений, 1 000 сотрудников через `importOrg`.
  - SC-001: 50 rps × 600 с. vegeta в среде нет, поэтому нагрузку даёт генератор на httpx с фиксированной частотой (открытая модель, TCP_NODELAY как у эталонного клиента). Внутренние p95/p99 берутся из гистограммы `/metrics`, клиентские — из замеров генератора.
  - SC-004: 3 ключа × 50 одновременных запросов на границе лимита, факт = оценка × 1,1.
- Мусор `design/screens.md.tmp` удалён. Правку `design/screens.md` в рабочем дереве (3Ф1/3Ф4/3Ф5) я не трогал: она не из зоны QA.

### Команды и вывод (сокращённо)
```text
$ make up-local && make failover BM_STACK=local          # кластер 127.0.0.1:55432, api 2 воркера, worker
SC-002 [local]: вызовов 1200; open 600 (в окне отказа 400, все fail_open); closed в окне отказа 400,
                отклонено 400; отчётов 650, в БД ровно по одному — 650
SC-003 [local]: подтверждено 200 — 678 (до kill 265), потеряно 0, дублей 0; прочее {'conn_errors': 386}
2 passed in 99.47s / 101.47s (два прогона)                # общий кластер 5432 не останавливался

$ stack.sh destroy && make up-local && make load BM_STACK=local     # 50 rps × 600 с, чистый кластер
SC-001 нагрузка: httpx (vegeta не использован); запросов 30000, факт 50.00 rps, успешных 100.00%
  внутри (/metrics, 30000 решений): доля ≤10 мс 91.23% (нужно ≥95%), ≤25 мс 99.69%; p95 ≈ 12.73 мс, p99 ≈ 18.47 мс
  у клиента: p50 52.36 мс, p95 62.88 мс (нужно ≤20), p99 70.97 мс, max 274.29 мс
SC-001: FAIL
SC-004: 3 × 50; разрешено 7, отказ 143 [budget_exceeded]; остаток до 10.00 ₽, Σ резервов 9.80 ₽;
        после отчётов остаток −0.78 ₽, перерасход 0.078% лимита (нужно ≤2%)
SC-004: PASS
make load: FAIL

$ DATABASE_URL=postgresql://bm:bm@127.0.0.1:5432/bm_test_qa make test
766 passed in 213.92s; Contracts: 3 kept, 0 broken; vitest: 28 files, 363 passed     # xfail — 0
$ ruff check / format --check (конфиг backend) по tests/failover, tests/load, двум acceptance — All checks passed
```
Среда во время 10-минутного прогона: loadavg 0,3–5,3 на 2 CPU, параллельно шли `make test` других исполнителей. Числа сняты в среде разработки, а не на эталонном стенде.

### Дефект Д-1 (критичный, владелец — DevOps/Backend, решает техлид): +40 мс на каждый запрос keep-alive при `--workers ≥ 2`
- **[Факт, замер]** При `uvicorn --workers 2` (так в `deploy/Dockerfile` CMD и в `stack.sh`) каждый запрос, кроме первого на соединении, получает около 40 мс задержки. Это видно на `/healthz`: 0,04 с у curl на повторном запросе. У `decide` p50 составляет 48 мс у любого клиента: httpx, http.client с TCP_NODELAY и без него, запрос одной записью. Тот же `bm` с `UVICORN_WORKERS=1` даёт p50 2,6–3,5 мс. Минимальное ASGI-приложение воспроизводит то же: с 1 воркером около 0,5 мс, с 2 воркерами около 41 мс.
- **[Факт]** `uvicorn.Config.bind_socket()` создаёт слушающий сокет с `proto=0` (uvicorn 0.54.0, `socket.socket(family=family)`), а `asyncio.base_events._set_nodelay` ставит TCP_NODELAY только при `proto == IPPROTO_TCP`. **[Вывод]** На сервере в многопроцессном режиме остаётся алгоритм Nagle, он складывается с отложенным ACK клиента — получаются ~40 мс.
- **Последствие [Факт, замер на простаивающей машине].** Эталонный `budget_client` (тайм-аут по умолчанию 50 мс, соединение keep-alive на поток): **32 из 200 решений (16%) ушли в fail_open по тайм-ауту.** В режиме `closed` такие вызовы были бы отклонены. Это нарушает SC-001 у вызывающей стороны и обесценивает контроль бюджета.
- **Подтверждение, что дело только в этом.** Тот же `bm` с 1 воркером на том же кластере, 50 rps × 120 с: внутри ≤10 мс — 99.57%, p95 ≈ 5.95 мс, p99 ≈ 7.98 мс; у клиента p50 8.50 / p95 11.09 / p99 14.48 мс; `make load: PASS`. Тот же 120-секундный прогон на стеке с 2 воркерами: внутри 99.50% ≤10 мс, у клиента p95 56.42 мс — FAIL. Внутренний FAIL 10-минутного прогона (91.23%) [Вывод] объясняется конкуренцией за CPU с прогонами других исполнителей, а не кодом.
- **Варианты исправления [Гипотеза, выбирает техлид]:** (а) ставить TCP_NODELAY на принятые сокеты: свой класс протокола (`--http <модуль>`) или запуск через `uvicorn.Server` с сокетом, созданным с `proto=IPPROTO_TCP`; (б) 1 воркер на процесс и несколько процессов или реплик за балансировщиком; (в) зависимость `httptools`/`uvloop` проверить, сейчас их в lock-файле нет. Проверка исправления — `make up-local && make load` (полные 10 мин) плюс повторный замер `budget_client`.

### Статусы гейтов (3Д2, 4Р)
| Гейт | Статус | Чем отличается / почему |
|---|---|---|
| `make test` (backend + vitest) | PASS | PG 16, не 18 |
| SC-002 (остановка api, worker, postgres) | PASS-замена | Сигналы процессам вместо `docker compose stop`; свой кластер 55432 |
| SC-003 (kill api под нагрузкой) | PASS-замена | SIGKILL группы процессов вместо `docker kill` |
| SC-001 | **FAIL** | Д-1: клиентский p95 62.88 мс > 20 мс; vegeta не использован, стенд не эталонный |
| SC-004 | PASS-замена | Стек из локальных процессов |
| Формат Anthropic `cost_report` (единицы `amount`) | НЕ ПРОВЕРЕНО | 4Б1: стенд заказчика, `admin-guide.md` §14 п. 10 |
| Формат OpenAI `costs` | НЕ ПРОВЕРЕНО | Там же; влияет на разбивку по моделям |
| SC-009, руководство на чистой машине (T090) | НЕ ПРОВЕРЕНО | Реестры образов закрыты (3Д1) |
| Гейты Docker (образ, smoke `compose_only`, backup/restore и failover в Docker, Trivy, PG 18, `egress-host-check`) | НЕ ПРОВЕРЕНО | PASS только по журналу CI для релизного SHA (4Д1) |
| Контейнер `backup` от root | Известное ограничение v1 | 4Д8 (5) |
| Лицензия `certifi` (MPL-2.0) | Открытый пункт | Юрист заказчика (3Д3, 4Д2) |

### Отклонения
1. Цель `load` в `Makefile` (зона DevOps) добавлена ещё в прерванной попытке: `make load` с `STACK_ENV`, `e2e` вынесен отдельно. Прошу DevOps принять или переложить.
2. vegeta нет: нагрузку даёт генератор на httpx, как разрешает T086.
3. Первые прогоны `make test` в этой сессии дали 2 FAIL в `tests/deploy/test_compose_static.py` (`IMPORT_HTTPS_PROXY`): это шла незаконченная правка T097 в рабочем дереве. Повторный прогон зелёный.

### Вопросы техлиду
1. Д-1: кто исправляет (Dockerfile CMD и `stack.sh` у DevOps или код запуска у Backend) и каким вариантом. T086 закрою после повторного 10-минутного `make load` с PASS.
2. Стоит ли поднять тайм-аут `budget_client` по умолчанию (50 мс) или оставить как есть? С ним любой сбой сети, похожий на Д-1, сразу переводит вызовы в fail_open или в отказ.

## Повтор T086 после T098 (Д-1), приёмка T098 — 2026-10-21

**Итог.** Д-1 исправлен: на стеке с 2 воркерами (`python -m bm.serve`) полный 10-минутный `make load` без параллельных прогонов — **SC-001: PASS-замена** (внутренний и клиентский пороги выполнены), **SC-004: PASS-замена**. Эталонный `budget_client` (тайм-аут 50 мс по умолчанию): 0 `fail_open` из 200. `make failover BM_STACK=local` с новой командой запуска — 2 passed. T086 и T098 закрыты (`[x]`). Решения `QA:` по-прежнему нет — оно в T089.

### Условия (5Д2)
- Других агентов и прогонов нет (по `ps`: только PostgreSQL 16 на 5432 в простое, стек QA и генератор). Общий кластер 5432 не трогался; БД стека — свой кластер `127.0.0.1:55432`, `stack.sh destroy` перед прогоном.
- Стек: `api` — `python -m bm.serve --host 127.0.0.1 --port 18080`, 2 воркера (мастер + 2 процесса `multiprocessing.spawn` по `ps`), `worker`.
- **loadavg** (2 CPU): до — `21:53:57 0.41 1.59 1.87` (хвост от `initdb`/старта стека, минуту назад был 1.33); после — `22:04:02 0.77 0.71 1.23`. Ряд каждые 10 с во время прогона: 1-минутный loadavg от 0.13 до 0.81.

### Команды и вывод (сокращённо)
```text
$ bash deploy/local/stack.sh destroy && make up-local
stack.sh: postgres: 127.0.0.1:55432; api: pid 3334; готов: http://127.0.0.1:18080/readyz; worker: pid 3400

$ make load BM_STACK=local                                   # 21:53:57–22:04:02, rc=0
run_load.sh: vegeta не найден — нагрузку даёт генератор на httpx (vegeta не использован)
SC-001 нагрузка: httpx (vegeta не использован); запросов 30000, факт 50.00 rps, успешных 100.00%, коды {'200': 30000}
  внутри (/metrics, 30000 решений): доля ≤10 мс 99.47% (нужно ≥95%), ≤25 мс 99.91% (нужно ≥99%); p95 ≈ 6.32 мс, p99 ≈ 8.62 мс
  у клиента: p50 8.40 мс, p95 11.25 мс (нужно ≤20), p99 15.05 мс, max 177.11 мс
SC-001: PASS
SC-004: 3 экземпляра × 50 одновременных; разрешено 7, отказ 143 ['budget_exceeded']; остаток до 10.00 ₽, Σ резервов 9.80 ₽;
        после отчётов (факт = оценка × 1,1) остаток -0.78 ₽, перерасход 0.78 ₽ = 0.078% лимита (нужно ≤2%)
SC-004: PASS
make load: PASS

# 200 решений эталонным budget_client (5Д1): скрипт вне репозитория (scratchpad), provision(("open",)) из tests/failover/stack.py,
# BudgetClient(base_url, key, state_dir, background=False) без timeout_ms (проверено timeout_ms == 50), 1 прогревочное + 200 подряд
budget_client: решений 200, fail_open 0, исходы {'allow': 200}, timeout_ms 50; медиана 4.65 мс, p95 7.53, p99 10.16, макс 10.69; loadavg 0.16

$ make failover BM_STACK=local PYTEST_ADDOPTS=-s            # loadavg до 0.15, после 0.82
SC-002 [local]: вызовов 1200; open 600 (в окне отказа 494, все fail_open); closed в окне отказа 494, отклонено 494; отчётов 652, в БД ровно по одному — 652
SC-003 [local]: подтверждено 200 — 3198 (до kill 1209), потеряно 0, дублей 0; прочее {'conn_errors': 176}
2 passed in 93.95s                                           # после рестарта api снова запущен через bm.serve

$ make smoke BM_STACK=local
4 passed, 6 skipped (compose_only — только образ/compose, проверяется в CI T092)
```

### Сравнение с прогоном до T098 (тот же генератор, 50 rps × 600 с)
| Метрика | До T098 (`uvicorn --workers 2`, параллельные прогоны) | После T098 (`bm.serve`, без параллельных) | Порог |
|---|---|---|---|
| Внутри ≤10 мс | 91.23% | 99.47% | ≥ 95% |
| Внутри p95 / p99 | ≈12.73 / ≈18.47 мс | ≈6.32 / ≈8.62 мс | ≤10 / ≤25 |
| У клиента p95 | 62.88 мс | 11.25 мс | ≤ 20 |
| `budget_client` fail_open из 200 | 32 | 0 | 0 |

[Вывод] Внутренний FAIL прошлого прогона и клиентские ~40 мс устранены; запас по клиентскому p95 — 1,8×, по p99 до тайм-аута 50 мс — около 5×. Max 177 мс у клиента — единичный выброс; генератор и сервер на одной 2-CPU машине.

### Статусы гейтов (изменения)
| Гейт | Статус | Отличие от эталона |
|---|---|---|
| SC-001 | **PASS-замена** (было FAIL) | Среда разработки, не эталонный стенд; генератор httpx, vegeta не использован; PG 16 |
| SC-004 | PASS-замена | Стек из локальных процессов |
| SC-002 / SC-003 на `bm.serve` | PASS-замена | Сигналы процессам вместо Docker; в Docker — по журналу CI релизного SHA |
| Д-1 | Исправлен (T098), подтверждён QA | В CI релизного SHA регресс-тест входит в `make test` |

Остальные статусы — как в разделе выше и в 5Р (Trivy FAIL до релизного SHA, гейты Docker ждут релизный SHA).

### Отклонения
- Нет. Цель `load` и `run_load.sh` не менялись.

### Вопросы техлиду
- Нет.

## Релизный прогон и гейты (T089) — 2026-10-21, SHA `d98ba2d`

**Итог: `QA: CONDITIONAL`.** FAIL нет. Все проверки Docker по релизному SHA зелёные: сборка, smoke, backup/restore, failover, Trivy, PG 18, SBOM. Без условий не проходят два обязательных гейта конституции. SC-001 по правилу 5Р может получить только PASS-замену. Лицензия `certifi` (MPL-2.0) ждёт подтверждения юриста (3Д3). Остальное непроверенное — в таблице ниже, его принимает заказчик или проверяет на своём стенде (3Д2).

### Основание
- **CI.** Прогон 39663848299 на `d98ba2d`: `completed success`, обе задачи (`test`, `delivery`) — success. Источник — `deploy/ci/runs/d98ba2d.md`: аннотации check-run, которые оркестратор получил через API GitHub. Полного журнала нет, blob.core.windows.net закрыт прокси.
- **Локально, `bm_test`, без параллельных прогонов.** HEAD = `d98ba2d`, в рабочем дереве поверх него только `deploy/ci/runs/d98ba2d.md`. Стек из локальных процессов пересоздан на HEAD (`stack.sh destroy && make up-local`).
- **Код api после 10-минутного SC-001 (сегодня, 21:53–22:04, после T098).** С тех пор изменился только `app/backend/bm/db/migrate.py` (`git diff 92b3414 d98ba2d`). `CREATE TABLE IF NOT EXISTS schema_migrations` перенесён под advisory-lock: это код старта, горячего пути решений он не касается. Поэтому полный прогон SC-001 засчитывается, а короткий прогон на HEAD служит контролем регрессии.

### Команды и вывод (сокращённо)
```text
$ make lint                                                  # HEAD d98ba2d
Contracts: 3 kept, 0 broken. / All checks passed! / 176 files already formatted

$ DATABASE_URL=postgresql://bm:bm@127.0.0.1:5432/bm_test make test     # loadavg до 0.01, rc=0
805 passed, 1 skipped in 216.35s     (skip: tests/unit/test_serve.py — AF_INET6 недоступен в среде)
Contracts: 3 kept, 0 broken.
Test Files 28 passed (28) / Tests 363 passed (363)
run-tests.sh full: OK

$ bash deploy/local/stack.sh destroy && make up-local
postgres: running / api: running / worker: running
$ make smoke BM_STACK=local
4 passed, 6 skipped (compose_only — проверяется в CI)

$ make failover BM_STACK=local PYTEST_ADDOPTS=-s             # loadavg до 2.09, после 1.47
SC-002 [local]: вызовов 1200; open 600 (в окне отказа 493, все fail_open); closed в окне отказа 493, отклонено 493; отчётов 653, в БД ровно по одному — 653
SC-003 [local]: подтверждено 200 — 3499 (до kill 1341), потеряно 0, дублей 0; прочее {'conn_errors': 208}
2 passed in 93.73s
$ make smoke BM_STACK=local                                  # после failover
4 passed, 6 skipped

$ make load BM_STACK=local LOAD_DURATION_S=120               # контроль регрессии; loadavg до 0.82, после 0.43
SC-001 нагрузка: httpx (vegeta не использован); запросов 6000, факт 50.01 rps, успешных 100.00%
  внутри (/metrics): ≤10 мс 99.47% (≥95%), ≤25 мс 99.93% (≥99%); p95 ≈ 6.34 мс, p99 ≈ 8.75 мс
  у клиента: p50 8.49 мс, p95 11.60 мс (≤20), p99 16.30 мс, max 58.07 мс
SC-001: PASS / SC-004: PASS (перерасход 0.078% ≤ 2%) / make load: PASS

# CI 39663848299, d98ba2d (аннотации)
test:     make lint PASS; make compose-check PASS; make test (PG 18.6) PASS; frontend (node 24) PASS
delivery: make build PASS; up PASS; smoke PASS; backup PASS; restore PASS; smoke после restore PASS;
          make failover PASS; CHECK_STRICT=1 make check PASS (WARN npm: 7 moderate); egress-host-check (попытка) PASS
Trivy bm:local: CRITICAL 0; certifi 2026.10.19 — одна установка (app/.venv). Базовый python:3.13-slim: CRITICAL 1
          (certifi 2026.6.17), в итоговый образ не попадает (FROM scratch, T099)
SBOM: sbom-image / sbom-npm / sbom-python .cdx.json с sha256 в аннотации
```

### Гейты (3Д2, 4Р, 5Р)

Обязательные гейты конституции (§ «Перед релизом», 3Д2) отмечены знаком ★.

| Гейт | Статус | Отличие / причина / где проверить |
|---|---|---|
| ★ Приёмочные и `make test` | **PASS** | CI: PG 18.6, `d98ba2d`. Локально: PG 16, 805 passed + vitest 363 passed |
| ★ SC-001 (p95/p99, 50 rps × 10 мин) | **PASS-замена** | Полный прогон после T098 — раздел выше. Короткий прогон на HEAD — PASS. Среда разработки, не эталонный стенд, генератор httpx (vegeta не использован), PG 16. По 5Р выше PASS-замены здесь не поднять. Где проверить: эталонный стенд заказчика, `make up && make load` |
| ★ SC-002 (остановка компонентов) | **PASS** | `make failover` в Docker по CI релизного SHA (5Д4). Локально — PASS-замена (сигналы процессам) |
| SC-003 (kill api под нагрузкой) | **PASS** | Так же, CI `d98ba2d` |
| SC-004 (граница лимита) | PASS-замена | Стек из локальных процессов |
| ★ SCA образа (Trivy) | **PASS** | `bm:local` CRITICAL 0 (было FAIL в 5Р). Базовый образ в поставку не входит |
| ★ SCA Python/npm, уязвимости | **PASS** | `CHECK_STRICT=1 make check` в CI — exit 0. Гейт npm — только critical |
| ★ SCA, лицензии: `certifi` (MPL-2.0) | **НЕ ПРОВЕРЕНО** | Узкое исключение `pypi:certifi@2026.10.19` в `licenses-exceptions.txt` действует, `make check` его принял. По 3Д3 MPL-2.0 вне белого списка, нужно подтверждение юриста заказчика |
| npm: 7 moderate (через react-admin) | Известное ограничение v1 | 5Р. Пересмотреть при обновлении react-admin |
| ★ SBOM | **PASS** | 3 файла CycloneDX, sha256 — в аннотации CI `d98ba2d` |
| Сборка образа, `make up`, smoke с `compose_only` | **PASS** | CI `d98ba2d` |
| Backup / restore + smoke после restore | **PASS** | CI `d98ba2d`. Локально backup/restore не прогонялся |
| PostgreSQL 18 | **PASS** | CI: postgres 18.6 (`make test`), `postgres@sha256:edb0c08d…` |
| `egress-host-check` | **НЕ ПРОВЕРЕНО** → PASS после подтверждения DevOps | В аннотации стоит «(попытка): PASS (1 с)». Правило 5Р требует, чтобы DevOps подтвердил по релизному журналу, что проверка действительно выполнилась. Такого подтверждения для `d98ba2d` в `14_devops.md` нет. [Вывод QA по коду `deploy/ci/egress-host-check.sh`] exit 0 возможен только на ветке «пакетов наружу = 0 → OK»: все случаи «не запускалась» выходят с кодом 2, а шаг `gha.py` при ненулевом коде показал бы не PASS. В CI `.env` взят из `.env.example`, где `IMPORT_ENABLED=false` и `IMPORT_HTTPS_PROXY=` пустой — так требует T089 |
| Формат Anthropic `cost_report` (единицы `amount`, `currency`, модель) | **НЕ ПРОВЕРЕНО** | Нужен реальный ответ провайдера на стенде заказчика (`docs/admin-guide.md` §14, п. 10). При ошибке расход завышается или занижается в 100 раз |
| Формат OpenAI `costs` (модель из `line_item`, `null`-поля) | **НЕ ПРОВЕРЕНО** | Там же. Влияет на разбивку по моделям, а не на сумму |
| SC-009, руководство на чистой машине (T090) | **НЕ ПРОВЕРЕНО** | Здесь реестры образов закрыты (3Д1). Проверяет заказчик или QA на стенде с доступом к образам, по `admin-guide.md` §14 |
| Контейнер `backup` от root | Известное ограничение v1 | 4Д8 (5) |
| `make e2e` (UI в браузере) | Не входит в T089 | Цель — заглушка. UI покрыт vitest (363), API-сценарии P1 — приёмочными тестами |

### Почему CONDITIONAL, а не PASS
По 3Д2 `QA: PASS` ставится, только когда все ★-гейты в статусе PASS. Два ★-гейта не в PASS:
1. SC-001 — PASS-замена, потому что стенд не эталонный.
2. SCA по лицензиям: `certifi` MPL-2.0 ждёт юриста.

FAIL нет, блокеров и критичных дефектов нет. По 3Д2 релиз в `16_release.md` называется **условным**. Заказчик либо явно принимает список выше (SC-001-замена, `certifi`, `egress-host-check`, форматы Anthropic/OpenAI, SC-009, SC-004-замена, известные ограничения), либо проверяет его на своём стенде по `admin-guide.md` §14.

### Отклонения
1. **Нет полного журнала CI.** Доказательство — аннотации check-run (`deploy/ci/runs/d98ba2d.md`), а 3Д1(б) требует журнал `gh run view --log`. Принято по прецеденту 5Д4: CI `34d28d0` разбирали так же. Аннотации содержат сводку каждого шага, версии, digest и sha256 SBOM.
2. **Локально не прогонялись `make backup`/`make restore`, `make check` и `make egress-host-check`:** здесь нет Docker и Trivy. Эти гейты закрыты по CI.
3. **Локальный стек оставлен поднятым на HEAD**, как и до прогона. Он работает на своём кластере 55432, общий 5432 не трогался.

### Дефекты
Новых нет.

### Вопросы техлиду
1. DevOps: подтвердить по CI `d98ba2d`, что `egress-host-check` выполнилась (контейнеры, «пакетов наружу = 0»). Если да, гейт становится PASS. На вердикт `CONDITIONAL` это не влияет.
2. Устраивают ли аннотации check-run вместо полного журнала (п. 1 отклонений) для закрытия T087 и T092?
