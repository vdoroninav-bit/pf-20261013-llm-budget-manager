---
description: "Задачи LLM Budget Manager v1 по ролям"
---

# Tasks: LLM Budget Manager v1

**Вход**: `12_plan.md` (обязателен), `10_tech_spec.md` (сценарии US1–US7), `12_openapi.yaml` (контракт), `12_arch_decision.md` §4.5 (алгоритм решения), `10_constitution.md`.
**Старт**: только после записи «согласен» в `12_arch_decision.md` → «Согласование» и после прохождения Г1 (вопрос 2 в APPROVAL).
**Исполнители**: AI-агенты, каждая задача делается за один проход. Поэтому у каждой задачи указаны точные файлы, зависимости и команда, которая доказывает готовность. Задача закрыта, только когда к ней приложен вывод этой команды (принцип VII).

## Формат: `[ID] [P?] [Story] [роль] Описание`

- **[P]** — задачу можно делать параллельно с соседними: файлы разные, зависимостей между ними нет.
- **[Story]** — сценарий спецификации: US1 решения, US2 расход, US3 настройка, US4 подписки, US5 импорт, US6 отчёты, роли и уведомления, US7 MCP. **[—]** — общая инфраструктура.
- **Роли**: designer, backend, frontend, ml, devops, qa. **ml: задач нет** (`ML_REQUIRED: no`, ADR-001).
- **TDD**: задача qa с тестами сценария всегда идёт раньше задачи backend/frontend с реализацией. Тесты сначала падают, это фиксируется выводом `pytest` в описании PR.
- **Урезание** (принцип VIII, порядок фиксирован): 🔻1 — MCP (T080–T081), 🔻2 — webhook (T073), 🔻3 — коннектор OpenAI (T063).

## Сводка по ролям и оценке (чел.-нед, `12_arch_decision.md` §5.1)

| Роль | Задачи | Оценка |
|---|---|---|
| designer | T003 | 0,3 |
| backend | T010–T019, T021, T031, T033, T041, T042, T051, T061–T063, T071, T073, T081, T091 | 11,5 (из них 🔻 0,6 + 0,2 + 0,4) |
| frontend | T002, T043, T052, T064, T072 | 2,5 |
| devops | T001, T087, T088, T090 | 1,0 |
| qa | T020, T030, T032, T040, T050, T060, T070, T080, T086, T089 | 1,5 |
| ml | — | 0 |
| техлид | ревью, приёмка, контрольная точка недели 5 (вне списка задач) | 1,5 |

---

## Phase 1: Setup

- [ ] **T001 [—] [devops] Каркас репозитория и команд.**
  - Файлы: `Makefile`, `app/backend/pyproject.toml`, `app/backend/uv.lock`, `app/backend/importlinter.ini`, `app/backend/bm/__init__.py`, `deploy/docker-compose.test.yml` (сервисы `postgres-test` и `tests`), `deploy/Dockerfile` (стадия `python-runtime`), `deploy/.env.example` (все переменные из `12_plan.md` §8), `tests/conftest.py` (пустые фикстуры), `.gitignore` (`.env`, `node_modules`, `__pycache__`).
  - Версии: Python 3.13, FastAPI 0.143.0, asyncpg 0.32.0, mcp 2.3.0 (опционально, extra `mcp`). Остальные зависимости фиксируются lock-файлом.
  - Готово: `make test` проходит с 0 тестов и exit 0; `make test-unit` проходит с exit 0.
  - Зависимости: —.
- [ ] **T002 [P] [—] [frontend] Каркас админки.**
  - Файлы: `app/frontend/{package.json, package-lock.json, vite.config.ts, tsconfig.json, index.html}`, `src/main.tsx`, `src/App.tsx`, `src/api/schema.d.ts` (команда `npm run gen:api` = `openapi-typescript ../../12_openapi.yaml`), `src/authProvider.ts` (`/api/v1/auth/login|logout|me`, CSRF из ответа login в заголовок `X-CSRF-Token`), `src/dataProvider.ts` (списки `{items,total}`, `page`, `page_size`, `sort`, `order`), `src/i18n/ru.ts`.
  - Ограничения: только MIT-ядро react-admin, шрифты системные, никаких внешних URL в сборке.
  - Тест: `src/__tests__/dataProvider.test.ts` проверяет преобразование `getList` в запрос и обратно.
  - Готово: `npm --prefix app/frontend run test && npm --prefix app/frontend run build` дают exit 0; `grep -rE "https?://" app/frontend/dist` пуст.
  - Зависимости: T001.
- [ ] **T003 [P] [—] [designer] Минимальные макеты и токены.**
  - Файлы: `design/tokens.json` (цвета, отступы, системный стек шрифтов), `design/screens.md`.
  - Содержание `screens.md`: 8 экранов — Вход; Панель подразделения (лимиты, расход по источникам, резервы, остаток, дата сброса, разбивка по моделям); Подразделения и лимиты; Каталог моделей и история цен; Подписки с предпросмотром доли; Загрузка AD-файла со сводкой; Импорт провайдеров и «Состояние»; Отчёты, CSV и сверка. Для каждого экрана — поля, подписи на русском, пустые и ошибочные состояния, видимость по ролям.
  - Готово: техлид принял `screens.md` (чек-лист: у каждого FR-027…FR-031 есть экран).
  - Зависимости: —.

---

## Phase 2: Foundational

**Ни один сценарий не начинается до конца этой фазы.**

- [ ] **T010 [P] [—] [backend] Тесты `money` и `periods`.** Тесты пишутся раньше кода и должны упасть.
  - Файлы: `tests/unit/test_money.py`, `tests/unit/test_periods.py`.
  - Обязательные случаи:
    - `cost(1000, 400, 0, price(in=10, out=50, RUB), 1)` = 3000 коп. (= 10 + 20 ₽);
    - кэшированные токены без `cached_per_1k` тарифицируются по цене входа;
    - USD × fx с `ROUND_HALF_UP`;
    - `kop_to_str(-5) == "-0.05"`;
    - `bounds(WEEK, понедельник 00:00 МСК)` начинается в этот понедельник;
    - `bounds(MONTH, 2026-10-31 23:59:59+03:00).resets_at == 2026-11-01T00:00+03:00`;
    - результат не зависит от `TZ` процесса (`monkeypatch.setenv("TZ","UTC")` и `time.tzset()`).
  - Готово: `pytest tests/unit/test_money.py tests/unit/test_periods.py` — тесты FAIL с ImportError.
  - Зависимости: T001.
- [ ] **T011 [P] [US4] [backend] Тесты `amortize`.**
  - Файл: `tests/unit/test_amortize.py`.
  - Случаи:
    - пример 4.1: M = 750 000 коп., октябрь 2026 (31 день), активна с 01.10; окно [01.10, 10.10] = 241 935 коп. (2 419,35 ₽); доля 10.10 = 24 193 коп. (241,93 ₽ — накопительное правило, APPROVAL вопрос 3А; при ответе 3Б тест меняется на 24 194);
    - сумма всех дней месяца = M ровно;
    - старт 15-го и окончание 20-го;
    - `test_week_across_months_two_fx` — неделя 28.09–04.10 с разными M в сентябре и октябре.
  - Готово: тесты FAIL.
  - Зависимости: T001.
- [ ] **T012 [P] [US1] [backend] Табличные тесты `select()`.**
  - Файл: `tests/unit/test_select.py`.
  - Строки таблицы:
    - сценарии 1.1–1.7 с точными суммами спецификации;
    - эмбеддинги без перенаправления;
    - нет инструментов или JSON у дешёвой модели → `deny`;
    - самый строгий лимит (личный дневной 500 ₽ исчерпан, у подразделения есть остаток);
    - модель с нулевой ценой всегда помещается;
    - равная цена → `tie_order`, затем `id`;
    - `test_negative_remaining_only_zero_price`;
    - `blocked_by_subs`;
    - `observe` + модель не разрешена → `allow` с `observed_violation=model_not_allowed`;
    - пустой `allowed` → `deny model_not_allowed` при `enforce`.
  - Готово: тесты FAIL.
  - Зависимости: T001.
- [ ] **T013 [—] [backend] Чистые функции домена.**
  - Файлы: `app/backend/bm/domain/{types.py, money.py, periods.py, amortize.py, select.py, codes.py}`.
  - Сигнатуры — `12_plan.md` §6, алгоритм — `12_arch_decision.md` §4.5. Ввода-вывода нет, `float` не используется.
  - Готово: `pytest tests/unit -q` проходит полностью.
  - Зависимости: T010, T011, T012.
- [ ] **T014 [—] [backend] Схема БД, раннер миграций, фикстуры.**
  - Файлы: `bm/db/migrate.py`, `bm/db/migrations/0001_init.sql` (все таблицы из `12_plan.md` §7, партиции `call_record` на текущий и следующий месяц, `REVOKE UPDATE, DELETE ON audit_log`), `tests/conftest.py` (фикстуры `db` — чистая схема на тест, `clock` — `FrozenClock`, `seed`), `tests/fixtures/seed.py` (построитель: подразделение, сотрудник, модель с ценой, лимиты, ключ — прямыми INSERT), `tests/integration/test_migrations.py`.
  - Готово: `make test` проходит; `test_migrations` проверяет наличие таблиц и запрет UPDATE на `audit_log`.
  - Зависимости: T001.
- [ ] **T015 [—] [backend] Каркас приложения и контрактный тест.**
  - Файлы: `bm/settings.py`, `bm/main.py` (`create_app`), `bm/http/errors.py` (`application/problem+json`, `detail` на русском), `bm/http/routers/system.py` (`/healthz`, `/readyz`, `/metrics`), `tests/contract/test_openapi_contract.py`.
  - Контрактный тест: (а) `12_openapi.yaml` валиден (openapi-spec-validator); (б) хелпер `assert_matches_contract(response, operation_id)` на openapi-core, им пользуются все приёмочные тесты; (в) каждая `operationId` из контракта зарегистрирована в приложении (тест помечается `xfail(strict=True)`, пока не реализованы все роутеры).
  - Готово: `make test` проходит.
  - Зависимости: T014.
- [ ] **T016 [—] [backend] Вход, роли, аудит.**
  - Файлы: `bm/access/{auth.py, permissions.py, authorize.py, audit.py}`, `bm/http/routers/auth.py`, `tests/unit/test_permissions.py`, `tests/acceptance/test_auth.py`.
  - Правила:
    - argon2id; cookie `bm_session` с `HttpOnly; SameSite=Strict`; CSRF на изменяющих запросах;
    - блокировка входа после 10 неудач на 15 мин (`429 login_locked`);
    - первый администратор создаётся из `ADMIN_INITIAL_LOGIN` / `ADMIN_INITIAL_PASSWORD` с `must_change_password=true`;
    - `PERMISSIONS` — единственная таблица прав `operationId → роли`, значения совпадают с `x-roles` в контракте (тест сверяет);
    - отказ доступа пишется в аудит (`forbidden_access`).
  - Готово: `make test`.
  - Зависимости: T015.
- [ ] **T017 [P] [—] [backend] Egress и тест «0 исходящих».**
  - Файлы: `bm/egress/client.py`, `app/backend/importlinter.ini` (контракт: `httpx`, `urllib`, `http.client`, `socket` запрещены вне `bm.egress`), `tests/conftest.py` (autouse-фикстура подменяет `socket.socket.connect` и разрешает только хост и порт PostgreSQL), `tests/security/test_no_egress.py`.
  - Правила `egress`:
    - `get_client("anthropic")` при `IMPORT_ENABLED=false` выбрасывает `EgressDisabled`;
    - разрешённые хосты — только `api.anthropic.com` и `api.openai.com`;
    - webhook разрешён только на частные адреса: 10/8, 172.16/12, 192.168/16, 127/8.
  - Готово: `make test`; `lint-imports` проходит.
  - Зависимости: T015.
- [ ] **T018 [P] [—] [backend] Снимок конфигурации.**
  - Файлы: `bm/budget/snapshot.py` (`ConfigSnapshot`: подразделения, назначения на сегодня, лимиты, разрешённые модели, каталог с текущими ценами, курсы, ключи по SHA-256, подписки; `load(conn)`; `ensure_fresh(conn)` одним запросом к `config_version`), `bm/config/service.py` (`apply(conn, change, actor)`: запись + аудит + `config_version += 1` в одной транзакции), `tests/integration/test_snapshot.py`.
  - Готово: `make test`.
  - Зависимости: T014.
- [ ] **T019 [P] [—] [backend] Ядро уведомлений.**
  - Файлы: `bm/notify/service.py` (`create_once(conn, kind, department_id, period, period_start, message) -> bool`, UNIQUE по ключу), `bm/notify/channels.py` (`InApp`), `bm/http/routers/notifications.py` (`listNotifications`, `markNotificationRead`), `tests/integration/test_notify_once.py`.
  - Готово: `make test`.
  - Зависимости: T016.

**Контрольная точка**: `make test` зелёный, каркас админки собирается.

---

## Phase 3: US1 — Решение перед вызовом (P1) 🎯 MVP

**Независимая проверка.** Тестовый клиент шлёт запросы при заданных бюджетах и каталоге; решения совпадают с ожидаемыми.

- [ ] **T020 [US1] [qa] Приёмочные тесты решений.** Тесты пишутся до реализации.
  - Файлы: `tests/acceptance/test_us1_decisions.py`, `tests/acceptance/test_edge_cases.py`, `tests/acceptance/test_messages_ru.py`, `tests/integration/test_concurrency.py`.
  - Покрытие:
    - сценарии 1.1–1.7 через `POST /api/v1/decisions`, каждый ответ проверяется `assert_matches_contract`;
    - пограничные случаи: `subject_unknown`; `model_unknown`; модель удалена из разрешённых; нулевая цена; равная цена; отрицательный остаток; подписки ≥ 100%; `subject_out_of_scope` → 403 + запись в аудит; `http_status_hint` для каждого кода;
    - `message` содержит кириллицу и дату сброса по МСК;
    - `test_concurrency`: 3 пула × 50 одновременных запросов на границе лимита; Σ `reserve` ≤ остатка до теста, строго.
  - Готово: тесты собраны и FAIL (404 или ImportError).
  - Зависимости: T015, T014.
- [ ] **T021 [US1] [backend] `Budget.decide` и роутеры вызывающей системы.**
  - Файлы: `bm/budget/{service.py, repo.py}`, `bm/http/deps.py` (аутентификация по `X-Api-Key`: SHA-256 → снимок, отозванный ключ → 401), `bm/http/schemas.py` (Pydantic 1:1 с контрактом, `extra="forbid"`), `bm/http/routers/caller.py` (`createDecision`, `getIntegrationConfig` с ETag), `tests/integration/test_decide_db.py`.
  - Транзакция: `ensure_fresh` → `SELECT … FROM budget_lock … ORDER BY owner_type, owner_id FOR UPDATE` → один `SUM … FILTER` по `ledger_daily` → `select()` → `INSERT call_record` → `UPDATE budget_lock`.
  - Готово: `pytest tests/acceptance/test_us1_decisions.py tests/acceptance/test_edge_cases.py tests/integration/test_concurrency.py` проходит, `make test` зелёный.
  - Зависимости: T013, T018, T020.

**Контрольная точка**: US1 работает независимо (решения по засеянным данным).

---

## Phase 4: US2 — Расход и остатки (P1)

- [ ] **T030 [US2] [qa] Приёмочные тесты расхода.** Тесты пишутся до реализации.
  - Файлы: `tests/acceptance/test_us2_usage.py`, `tests/integration/test_settle_db.py`.
  - Покрытие:
    - сценарии 2.1–2.6: точная сумма 2.1; дубль по `decision_id`; истечение через 15 мин с `FrozenClock` → `no_report`; оборванный стрим; отчёт без решения;
    - `test_late_report_after_expiry_replaces_estimate`;
    - `test_expense_date_is_decision_date` (решение 23:59:59, отчёт 00:00:05 МСК);
    - `decision_not_followed`;
    - пакет из 1000 строк с 10 дублями → 990 `charged` и 10 `duplicate`;
    - `GET /api/v1/balance` совпадает с ручным пересчётом.
  - Готово: тесты FAIL.
  - Зависимости: T020.
- [ ] **T031 [US2] [backend] `settle`, пакет, истечение резервов, баланс, worker.**
  - Файлы: `bm/budget/service.py` (`settle`, `balance`, `expire_reserves`), `bm/http/routers/caller.py` (`reportUsage`, `reportUsageBatch`, `getCallerBalance`), `bm/worker/{main.py, jobs.py}` (цикл задач, `pg_try_advisory_lock` на задачу, `job_run`; задача `expire_reserves` раз в 60 с).
  - Правила:
    - 200 отдаётся только после COMMIT;
    - `report_key` обеспечивает идемпотентность;
    - факт пишется в `ledger_daily` сотрудника и подразделения в той же транзакции;
    - резерв снимается целиком.
  - Готово: `pytest tests/acceptance/test_us2_usage.py tests/integration/test_settle_db.py` и `make test`.
  - Зависимости: T021, T030.
- [ ] **T032 [P] [US1] [qa] Отказные тесты SC-002 и SC-003.** Тесты пишутся до клиента.
  - Файлы: `tests/failover/test_sc002_failover.py`, `tests/failover/test_sc003_kill.py`.
  - SC-002: стек поднят (`make up`), 2 подразделения (`open` и `closed`); `docker compose stop api worker postgres` под потоком 20 rps на 60 с → 100% вызовов `open` проходят, 100% `closed` получают `PolicyUnavailable`; после `start` и `flush()` все `report_id` есть в БД ровно один раз.
  - SC-003: `docker kill api` под нагрузкой; каждый ответ 200 на `/usage` есть в БД.
  - Готово: тесты FAIL (клиента нет).
  - Зависимости: T030.
- [ ] **T033 [US1] [backend] Эталонный клиент `budget_client`.**
  - Файлы: `app/client/budget_client/{pyproject.toml, budget_client/client.py, buffer.py, config_cache.py}`, `tests/unit/test_client_buffer.py`.
  - Правила:
    - таймаут 50 мс;
    - кэш `integration-config` и `fail_mode` по субъекту на диске, обновление раз в 60 с;
    - буфер — SQLite (stdlib) с `PRAGMA synchronous=FULL`, ключ `report_id`;
    - `flush()` пакетами по 500 с экспоненциальной паузой.
  - Готово: `pytest tests/unit/test_client_buffer.py`; после T087 — `make up && make failover`.
  - Зависимости: T031, T032.

---

## Phase 5: US3 — Оргструктура, бюджеты, каталог (P1)

- [ ] **T040 [P] [US3] [qa] Приёмочные тесты настройки.** Тесты пишутся до реализации.
  - Файлы: `tests/acceptance/test_us3_config.py`, `tests/fixtures/ad_groups_ok.csv`, `tests/fixtures/ad_groups_errors.csv`.
  - Покрытие:
    - сценарии 3.1–3.6;
    - `dry_run` ничего не меняет;
    - основная группа и предупреждение;
    - лимиты 5 000 / 20 000 / 60 000;
    - цена с 15-го — старые события не меняются;
    - USD-цена без курса → `422 fx_rate_missing`;
    - каждое изменение пишется в аудит со значениями «было» и «стало»;
    - `test_removed_employee_keeps_period_spend`;
    - ключ показывается один раз, отзыв действует сразу (следующий `decide` → 401).
  - Готово: тесты FAIL.
  - Зависимости: T015.
- [ ] **T041 [US3] [backend] Загрузка AD-файла.**
  - Файлы: `bm/org_import/service.py` (`import_ad_csv(conn, data: bytes, dry_run: bool, actor) -> OrgImportSummary`), `bm/http/routers/org.py` (`importOrg`).
  - Формат: CSV `login;display_name;group;is_primary`, UTF-8. Псевдоним — `emp-` + 10 случайных hex, выдаётся один раз.
  - Готово: тесты 3.1, 3.2 и `test_removed_*` проходят.
  - Зависимости: T016, T018, T040.
- [ ] **T042 [US3] [backend] Админ-CRUD.**
  - Файлы: `bm/http/routers/{departments.py, employees.py, models.py, fx.py, callers.py, admin_users.py}`. Операции — все `operationId` тегов departments, employees, models, fx, callers, admin-users из контракта.
  - Правила: изменения идут только через `config.apply` (аудит и `config_version`); `revealPseudonym` пишет аудит `reveal_pseudonym`; новое подразделение создаётся с `policy_mode=observe`.
  - Готово: `pytest tests/acceptance/test_us3_config.py` и `make test`.
  - Зависимости: T016, T018, T040.
- [ ] **T043 [P] [US3] [frontend] Экраны настройки.**
  - Файлы: `src/resources/{departments, employees, models, callers, adminUsers}/*`, `src/pages/OrgImport.tsx`, `src/__tests__/*.test.tsx`.
  - Содержание: вход; подразделения (режим, fail_mode, разрешённые модели, лимиты день/неделя/месяц); сотрудники по псевдонимам с личными лимитами; модели с историей цен; ключи (модальное окно «ключ показан один раз»); пользователи админки; загрузка AD со сводкой.
  - Работа идёт по типам из `schema.d.ts`; в тестах API мокается через MSW.
  - Готово: `npm --prefix app/frontend run test` — у каждого ресурса есть тест «рендерит русские подписи и отправляет запрос по контракту».
  - Зависимости: T002, T003.

**Контрольная точка — неделя 5 (техлид).** US1–US3 зелёные, `make up && make smoke` проходит после T087. Факт по трудоёмкости сравнивается с планом (11,0 чел.-нед к этому моменту). Если прогноз выше рамки, применяется урезание 🔻1 → 🔻2 → 🔻3. Если урезания не хватает — стоп и решение заказчика (SC-013). Порог: 4,0 чел.-мес, или 4,17 чел.-мес (2 млн ₽), если заказчик выбрал вариант 1А.

---

## Phase 6: US4 — Подписки (P2)

- [ ] **T050 [US4] [qa] Приёмочные тесты подписок.** Тесты пишутся до реализации.
  - Файл: `tests/acceptance/test_us4_subscriptions.py`.
  - Покрытие:
    - сценарии 4.1–4.6;
    - подписка сотрудника списывается и из его лимита, и из лимита подразделения;
    - курс в USD: курс месяца / последний с предупреждением / «нет курса»;
    - предпросмотр доли до сохранения;
    - уведомление `subs_share` один раз;
    - закрытие — прошлое сохраняется;
    - `unallocated` не списывается;
    - курс изменён задним числом — M месяца не меняется.
  - Готово: тесты FAIL.
  - Зависимости: T030.
- [ ] **T051 [US4] [backend] Подписки и амортизация в остатке.**
  - Файлы: `bm/http/routers/subscriptions.py` (5 операций), `bm/budget/service.py` (амортизация в `balance` и `decide` через `subscription_month`), `bm/worker/jobs.py` (задача на 1-е число 00:05 МСК: материализовать `subscription_month`, проверить долю подписок).
  - Правила: денежные поля подписки неизменяемы, их защищает триггер из T014.
  - Готово: `pytest tests/acceptance/test_us4_subscriptions.py` и `make test`.
  - Зависимости: T031, T019, T050.
- [ ] **T052 [P] [US4] [frontend] Подписки и курсы.**
  - Файлы: `src/resources/subscriptions/*` (форма с живым предпросмотром `previewSubscription`, предупреждение при доле ≥ порога), `src/resources/fxRates/*`, тесты.
  - Готово: `npm --prefix app/frontend run test`.
  - Зависимости: T043.

---

## Phase 7: US5 — Импорт Anthropic и OpenAI (P2)

- [ ] **T060 [US5] [qa] Приёмочные тесты импорта и эталонный остаток SC-007.** Тесты пишутся до реализации.
  - Файлы: `tests/acceptance/test_us5_import.py`, `tests/integration/test_import_pipeline.py`, `tests/acceptance/test_sc007_reference_balance.py`, `tests/fixtures/anthropic/*.json`, `tests/fixtures/openai/*.json`.
  - Фикстуры берутся из записанных ответов спайка (день 2). Если их нет, фикстуры собираются по документации провайдеров с пометкой `# synthetic` и заменяются после спайка.
  - Покрытие:
    - сценарии 5.1–5.7: `import` против `decision_api`; несопоставленный источник → `unallocated` и уведомление; повтор без изменений не меняет итог; уточнение заменяет значение; 2 неудачи подряд → уведомление; при выключенном флаге — 0 соединений; поздний импорт после смены месяца;
    - SC-007: токенные события + импорт + подписка USD + переход месяца, остаток совпадает с ручным расчётом до копейки.
  - Готово: тесты FAIL.
  - Зависимости: T031.
- [ ] **T061 [US5] [backend] Конвейер импорта и справочник источников.**
  - Файлы: `bm/provider_import/{pipeline.py, sources/base.py, sources/recorded.py}`, `bm/http/routers/{provider_sources.py, imports.py}`, `bm/worker/jobs.py` (запуск в `IMPORT_HOUR_MSK`, повтор раз в час до `IMPORT_RETRY_UNTIL_HOUR_MSK`, окно `IMPORT_WINDOW_DAYS`).
  - Правила:
    - upsert строки → дельта в `ledger_daily` подразделения в той же транзакции;
    - пересчёт статуса строки по текущему `mode`;
    - Anthropic с `kind≠workspace` → `422 unsupported_source_kind`.
  - Готово: тесты T060 на `RecordedSource` проходят, `make test` зелёный.
  - Зависимости: T017, T019, T060.
- [ ] **T062 [P] [US5] [backend] Коннектор Anthropic.**
  - Файлы: `bm/provider_import/sources/anthropic.py`, `tests/integration/test_anthropic_source.py`.
  - Работа: `cost_report` с шагом `1d`, группировка по `workspace_id` и `description`, пагинация, сумма в USD; модель извлекается из `description`. Ключ берётся только из `ANTHROPIC_ADMIN_KEY(_FILE)`, HTTP-клиент — только через `egress`. В тестах вместо сети — транспорт httpx с фикстурами.
  - Готово: `make test`.
  - Зависимости: T061.
- [ ] **T063 [P] [US5] [backend] 🔻3 Коннектор OpenAI.**
  - Файлы: `bm/provider_import/sources/openai.py`, `tests/integration/test_openai_source.py`.
  - Работа: `GET /organization/costs`, `bucket_width=1d`, `group_by=project_id,line_item`, пагинация. Названия полей сумм сверяются с фикстурами спайка.
  - Готово: `make test`.
  - Зависимости: T061.
- [ ] **T064 [P] [US5] [frontend] Источники и состояние импорта.**
  - Файлы: `src/resources/providerSources/*`, `src/pages/ImportStatus.tsx` (включён или нет, ключ задан или нет, последняя загрузка, отставание, несопоставленные источники, кнопка «Загрузить сейчас»), тесты.
  - Готово: `npm --prefix app/frontend run test`.
  - Зависимости: T043.

---

## Phase 8: US6 — Отчёты, роли, уведомления (P2)

- [ ] **T070 [US6] [qa] Приёмочные тесты отчётов и безопасности.** Тесты пишутся до реализации.
  - Файлы: `tests/acceptance/test_us6_reports_roles.py`, `tests/security/{test_roles_matrix.py, test_no_content.py, test_secrets_not_leaked.py}`.
  - Покрытие:
    - сценарии 6.1–6.6;
    - CSV без идентификаторов сотрудников, с защитой от CSV-инъекции;
    - пороги 80% и 100% — по одному уведомлению за период;
    - матрица «роль × операция» строится из `PERMISSIONS` и контракта: 0 успешных обращений к чужим данным, 100% отказов в аудите;
    - в схеме БД нет текстовых колонок, кроме белого списка; в логах прогона нет полей `prompt`, `messages`, `content`;
    - значения `*_ADMIN_KEY` не встречаются в логах, ответах и дампе БД.
  - Готово: тесты FAIL.
  - Зависимости: T042.
- [ ] **T071 [US6] [backend] Отчёты, баланс подразделения, журналы, фоновые задачи.**
  - Файлы: `bm/reports/service.py`, `bm/http/routers/{reports.py, events.py, audit.py}`, `bm/http/routers/departments.py` (`getDepartmentBalance`, `listDepartmentBalanceItems`), `bm/worker/jobs.py`.
  - Фоновые задачи: пороги раз в минуту; `agg_daily` раз в сутки; ретенция — создание партиций на 2 месяца вперёд и `DROP` партиций старше `RETENTION_DETAIL_MONTHS`.
  - Готово: `pytest tests/acceptance/test_us6_reports_roles.py tests/security` и `make test`; контрактный тест из T015 снимает `xfail` для всех операций, кроме MCP.
  - Зависимости: T031, T019, T051, T061, T070.
- [ ] **T072 [P] [US6] [frontend] Панель, отчёты, журналы.**
  - Файлы: `src/pages/{Dashboard, Reports, Reconciliation, Notifications}.tsx`, `src/resources/{events, audit}/*`, меню по ролям, тесты.
  - Содержание: панель — по каждому периоду лимит, расход по источникам, резервы, остаток, дата сброса и пометка «бюджет занят подписками»; разбивка по моделям; выгрузка CSV.
  - Готово: `npm --prefix app/frontend run test && npm --prefix app/frontend run build`.
  - Зависимости: T043.
- [ ] **T073 [US6] [backend] 🔻2 Webhook-канал.**
  - Файлы: `bm/notify/channels.py` (`Webhook` через `egress("webhook")`, повтор 3 раза, `webhook_status`), `tests/integration/test_webhook.py` (локальный HTTP-сервер на 127.0.0.1).
  - Готово: `make test`.
  - Зависимости: T071.

---

## Phase 9: US7 — MCP (P3) 🔻1

- [ ] **T080 [US7] [qa] Тест эквивалентности HTTP и MCP.** Тест пишется до реализации.
  - Файл: `tests/acceptance/test_us7_mcp.py`.
  - Покрытие: сценарии 1.1–1.4 и 2.1 параметризуются по адаптеру (`http`, `mcp`); результаты совпадают поле в поле, кроме `decision_id` и `created_at`. Без ключа → ошибка `unauthorized`.
  - Готово: тест FAIL.
  - Зависимости: T031.
- [ ] **T081 [US7] [backend] MCP-сервер.**
  - Файл: `bm/mcp/server.py` — mcp 2.3, Streamable HTTP, монтирование в `create_app` на `/mcp`, инструменты `request_decision`, `report_usage`, `get_balance` поверх `Budget`, тот же `X-Api-Key` и `subject_scope`.
  - Готово: `pytest tests/acceptance/test_us7_mcp.py` и `make test`.
  - Зависимости: T080.

---

## Phase 10: Поставка и сквозные проверки

- [ ] **T086 [P] [—] [qa] Нагрузочный тест SC-001 и SC-004.**
  - Файлы: `tests/load/{targets.py, run_load.sh}`, цель `make load`.
  - Работа: генерация целей vegeta для 1 000 сотрудников и 30 моделей; 50 rps × 10 мин; p95 и p99 берутся из гистограммы `/metrics` и из отчёта vegeta; граница лимита: факт = оценка × 1,1, перерасход ≤ 2%.
  - Готово: `make up && make load` печатает PASS и значения p95/p99.
  - Зависимости: T031, T087.
- [ ] **T087 [—] [devops] Полная поставка.**
  - Файлы: `deploy/docker-compose.yml` (сервисы `api`, `worker`, `postgres:18`, `backup`; сети `core` с `internal: true` для `postgres` и `backup`, `edge` для `api` и `worker`; healthcheck; `restart: unless-stopped`; порт `${BIND_ADDR}:${API_PORT}:8080`; секреты через `*_FILE`), `deploy/Dockerfile` (стадия node собирает SPA, стадия python копирует `dist` в `/app/static/admin`), `deploy/backup/{backup.sh, restore.sh}`, `tests/smoke/test_smoke.py` (тест пишется первым).
  - Цели Makefile: `up`, `down`, `smoke`, `backup`, `restore`.
  - Готово: `make up && make smoke && make backup && make restore && make smoke`; время от `git clone` до первого решения ≤ 30 мин по `docs/admin-guide.md` (SC-009, замер QA).
  - Зависимости: T031, T043.
- [ ] **T088 [P] [—] [devops] SDL-проверки.**
  - Файлы: `deploy/ci/check.sh`, `deploy/ci/licenses-allow.txt` (MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, PostgreSQL, PSF-2.0, 0BSD, Python-2.0), цели `check` и `egress-host-check`.
  - Работа: CycloneDX SBOM для Python и npm, pip-audit, npm audit, Trivy по образу. Сборка падает на GPL, AGPL, SSPL, LGPL и на `@react-admin/ra-*`.
  - Готово: `make check` с exit 0 и SBOM в `deploy/ci/out/`.
  - Зависимости: T001.
- [ ] **T090 [P] [—] [devops] Руководство администратора.**
  - Файл: `docs/admin-guide.md` (RU): установка за ≤ 30 мин; переменные окружения; резервное копирование и восстановление; включение импорта и ключи; правила денег (копейки, накопительная амортизация, сутки UTC импорта); режимы политики; условие о локальном акте перед `enforce`.
  - Готово: техлид принял документ, QA прошёл его на чистой машине.
  - Зависимости: T087.
- [ ] **T091 [P] [—] [backend] Руководство по интеграции.**
  - Файл: `docs/integration-guide.md` (RU): протокол `decisions` → вызов модели → `usage`; `http_status_hint`; эталонная схема с таймаутом 50 мс, fail-open и буфером; пример на `budget_client`; рекомендация закрыть прямой доступ к провайдерам.
  - Готово: техлид принял документ.
  - Зависимости: T033.
- [ ] **T089 [—] [qa] Релизный прогон и гейты.**
  - Работа: `make test`, `make up`, `make smoke`, `make load`, `make failover`, `make check`, `make egress-host-check` (стенд QA), тест восстановления, установка по руководству.
  - Результат — `15_qa_report.md` (выход QA).
  - Готово: все гейты конституции зелёные.
  - Зависимости: все задачи, не урезанные решением на контрольной точке.

---

## Dependencies & Execution Order

```text
T001 ─┬─ T010,T011,T012 [P] ── T013 ─┐
      ├─ T014 ─ T015 ─┬─ T016 ─ T019 │
      │               ├─ T017 [P]    │
      │               └─ T018 [P] ───┤
      │                              ├─ T020 ─ T021 ─ T030 ─ T031 ─┬─ T032 ─ T033
      ├─ T002 [P] ─ T043 ─ T052/T064/T072 [P]                     ├─ T050 ─ T051
      ├─ T088 [P]                                                  ├─ T060 ─ T061 ─ T062/T063 [P]
T003 [P] ─┘                                                         ├─ T080 ─ T081 (🔻1)
T015 ─ T040 [P] ─ T041, T042 ─ T070 ─ T071 ─ T073 (🔻2)              └─ T087 ─ T086, T090
```

**Критический путь (Backend 1):** T013 → T021 → T031 → T061 → T071 → T089. Параллельно идут Backend 2 (T016–T019, T041, T042, T051, T033), frontend (T002, T043, …) и devops (T087, T088).

**Внутри сценария:** тест qa → реализация backend → зелёный `make test` → коммит `<название проекта>: <задача>`.

## Implementation Strategy

1. **MVP**: Phase 1–5 (US1–US3) + T033 + T087. После этого — контрольная точка недели 5, `make up && make smoke`, отказной тест.
2. **P2**: US4 → US5 (Anthropic, затем OpenAI) → US6.
3. **Stretch**: webhook, затем MCP — только по решению на контрольной точке.
4. Каждая фаза заканчивается зелёным `make test`. Расширение объёма вне этого файла — только через техлида.
