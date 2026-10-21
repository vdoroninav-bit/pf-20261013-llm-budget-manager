---
description: "Задачи LLM Budget Manager v1 по ролям"
---

# Tasks: LLM Budget Manager v1

**Вход**: `12_plan.md` (обязателен), `10_tech_spec.md` (сценарии US1–US7), `12_openapi.yaml` (контракт), `12_arch_decision.md` §4.5 (алгоритм решения), `10_constitution.md`.
**Старт**: только после записи «согласен» в `12_arch_decision.md` → «Согласование» и после прохождения Г1 (вопрос 2 в APPROVAL).
**Исполнители**: AI-агенты, каждая задача делается за один проход. Поэтому у каждой задачи указаны точные файлы, зависимости и команда, которая доказывает готовность. Задача закрыта, только когда к ней приложен вывод этой команды (принцип VII).
**Решения техлида по ходу разработки**: `13_design_rulings.md` (дизайн, контракт 1.0.1), `14_tech_rulings.md` (вопросы backend и frontend, контракт 1.0.2; пакеты 2, 3, 4 и 5 — без правок контракта), `14_checkpoint_week5.md` (контрольная точка недели 5). Пометка «(14_tech_rulings.md, Б…/Ф…)» у пункта задачи означает, что пункт добавлен этим решением; коды пакета 3 начинаются с цифры 3 (3Б…, 3Ф…, 3Д…), пакета 4 — с цифры 4 (4Б…, 4Д…, 4Р), пакета 5 — с цифры 5 (5Б…, 5Д…, 5Р).
**Тестовые БД** (14_tech_rulings.md, пакет 2, Q8): у каждого исполнителя своя БД на локальном PostgreSQL — `bm_test_b1`, `bm_test_b2`, `bm_test_fe`, `bm_test_qa`, `bm_test_devops` (`CREATE DATABASE … OWNER bm`). Вывод команды готовности, полученный на своей БД, принимается. `bm_test` — только для прогона оркестратора и релизного прогона T089, без параллельных запусков.

## Формат: `[ID] [P?] [Story] [роль] Описание`

- **[P]** — задачу можно делать параллельно с соседними: файлы разные, зависимостей между ними нет.
- **[Story]** — сценарий спецификации: US1 решения, US2 расход, US3 настройка, US4 подписки, US5 импорт, US6 отчёты, роли и уведомления, US7 MCP. **[—]** — общая инфраструктура.
- **Роли**: designer, backend, frontend, ml, devops, qa. **ml: задач нет** (`ML_REQUIRED: no`, ADR-001).
- **TDD**: задача qa с тестами сценария всегда идёт раньше задачи backend/frontend с реализацией. Тесты сначала падают, это фиксируется выводом `pytest` в описании PR.
- **Урезание** (принцип VIII, порядок фиксирован): 🔻1 — MCP (T080–T081), 🔻2 — webhook (T073), 🔻3 — коннектор OpenAI (T063).
- **Итог контрольной точки недели 5** (`14_checkpoint_week5.md`, 2026-10-21): **🔻1 применён** — T080 и T081 в v1 не делаются. 🔻2 и 🔻3 не применены: webhook и коннектор OpenAI остаются в v1. Если T073 не закрыта к началу T089, применяется 🔻2.

## Сводка по ролям и оценке (чел.-нед, `12_arch_decision.md` §5.1)

| Роль | Задачи | Оценка |
|---|---|---|
| designer | T003 | 0,3 |
| backend | T010–T019, T021, T031, T033, T041, T042, T051, T061–T063, T071, T073, ~~T081~~ (урезана), T091, T096, T097, T098 | 11,5 (из них 🔻 0,6 + 0,2 + 0,4; 🔻1 = 0,4 урезан) |
| frontend | T002, T043, T052, T064, T072, T095 | 2,5 |
| devops | T001, T087, T088, T090, T092, T099; часть T098 | 1,0 |
| qa | T020, T030, T032, T040, T050, T060, T070, ~~T080~~ (урезана), T086, T089, T093 | 1,5 |
| ml | — | 0 |
| техлид | ревью, приёмка, контрольная точка недели 5 (вне списка задач) | 1,5 |

T092, T093 и T095 оценку не меняют. T092 и T093 переносят проверки поставки из Docker в CI и на стек из локальных процессов, T095 — доводка в рамках строки frontend (`14_checkpoint_week5.md` §3) [Вывод]. T096 и T097 (пакет 4) — доводка дефектов поставки в рамках строки backend, оценку тоже не меняют [Вывод; T096 ≈ 0,5 дня, T097 ≈ 1–2 ч — Гипотеза]. T098 и T099 (пакет 5) — дефекты поставки, оценку не меняют [Вывод; T098 ≈ 0,5 дня, T099 ≈ 2–4 ч — Гипотеза].

---

## Phase 1: Setup

- [x] **T001 [—] [devops] Каркас репозитория и команд.**
  - Файлы: `Makefile`, `app/backend/pyproject.toml`, `app/backend/uv.lock`, `app/backend/importlinter.ini`, `app/backend/bm/__init__.py`, `deploy/docker-compose.test.yml` (сервисы `postgres-test` и `tests`), `deploy/Dockerfile` (стадия `python-runtime`), `deploy/.env.example` (все переменные из `12_plan.md` §8), `tests/conftest.py` (пустые фикстуры), `.gitignore` (`.env`, `node_modules`, `__pycache__`).
  - Версии: Python 3.13, FastAPI 0.143.0, asyncpg 0.32.0, mcp 2.3.0 (опционально, extra `mcp`). Остальные зависимости фиксируются lock-файлом. *(Extra `mcp` удаляется в T087 после урезания 🔻1 — 14_tech_rulings.md, 3К.)*
  - Готово: `make test` проходит с 0 тестов и exit 0; `make test-unit` проходит с exit 0.
  - Закрыта техлидом по выводу в `14_devops.md` (14_tech_rulings.md, пакет 3, 3Т). Отклонение «`make test` на локальном PostgreSQL, контейнер — `make test-docker`» принято.
  - Зависимости: —.
- [x] **T002 [P] [—] [frontend] Каркас админки.**
  - Файлы: `app/frontend/{package.json, package-lock.json, vite.config.ts, tsconfig.json, index.html}`, `src/main.tsx`, `src/App.tsx`, `src/api/schema.d.ts` (команда `npm run gen:api` = `openapi-typescript ../../12_openapi.yaml`), `src/authProvider.ts` (`/api/v1/auth/login|logout|me`, CSRF из ответа login в заголовок `X-CSRF-Token`), `src/dataProvider.ts` (списки `{items,total}`, `page`, `page_size`, `sort`, `order`), `src/i18n/ru.ts`, `scripts/check-dist.mjs`.
  - Ограничения: только MIT-ядро react-admin, шрифты системные, сборка не загружает ничего с внешних адресов; телеметрия react-admin выключена (`disableTelemetry`) и это закреплено тестом.
  - Тест: `src/__tests__/dataProvider.test.ts` проверяет преобразование `getList` в запрос и обратно; `src/__tests__/checkDist.test.ts`; тест «не отправляет телеметрию react-admin» в `App.test.tsx`.
  - Готово: `npm --prefix app/frontend run test && npm --prefix app/frontend run build` дают exit 0; шаг `build` включает `scripts/check-dist.mjs`, который падает на любом `http(s)://` или `//host` в `index.html` и CSS и на любом адресе в JS вне белого списка (XML-namespace, `http://localhost` как база `new URL()`, адреса в текстах ошибок react/mui/react-router, адрес телеметрии ra-core — только пока действует тест на `disableTelemetry`). Критерий изменён решением техлида (14_tech_rulings.md, Ф1): пустой `grep -rE "https?://" dist` недостижим без правки кода react-dom.
  - Зависимости: T001.
- [x] **T003 [P] [—] [designer] Минимальные макеты и токены.**
  - Файлы: `design/tokens.json` (цвета, отступы, системный стек шрифтов), `design/screens.md`.
  - Содержание `screens.md`: 8 экранов — Вход; Панель подразделения (лимиты, расход по источникам, резервы, остаток, дата сброса, разбивка по моделям); Подразделения и лимиты; Каталог моделей и история цен; Подписки с предпросмотром доли; Загрузка AD-файла со сводкой; Импорт провайдеров и «Состояние»; Отчёты, CSV и сверка. Для каждого экрана — поля, подписи на русском, пустые и ошибочные состояния, видимость по ролям.
  - Готово: техлид принял `screens.md` (чек-лист: у каждого FR-027…FR-031 есть экран).
  - Принята техлидом 2026-10-21 (14_tech_rulings.md, пакет 3, 3Т): экраны §1–8 и вспомогательные §9–11, матрица FR → экран покрывает FR-027…FR-031. Правки текста §5.2 (3Ф1) и §11 (3Ф5) внесены техлидом в пакете 4 (4Ф1).
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
  - Не закрыта (14_tech_rulings.md, пакет 3, 3Т): вывода красной фазы нет ни в одном отчёте. Чтобы закрыть, Backend 1 прикладывает: вывод `pytest -v` четырёх файлов T010–T012 с сопоставлением обязательных случаев и имён тестов, и мутационную проверку на временной копии (`ROUND_HALF_UP` → `ROUND_HALF_EVEN`; границы периодов по `TZ` процесса) — каждая мутация даёт красный тест.
  - Зависимости: T001.
- [ ] **T011 [P] [US4] [backend] Тесты `amortize`.**
  - Файл: `tests/unit/test_amortize.py`.
  - Случаи:
    - пример 4.1: M = 750 000 коп., октябрь 2026 (31 день), активна с 01.10; окно [01.10, 10.10] = 241 935 коп. (2 419,35 ₽); доля 10.10 = 24 193 коп. (241,93 ₽ — накопительное правило, APPROVAL вопрос 3А; при ответе 3Б тест меняется на 24 194);
    - сумма всех дней месяца = M ровно;
    - старт 15-го и окончание 20-го;
    - `test_week_across_months_two_fx` — неделя 28.09–04.10 с разными M в сентябре и октябре.
  - Готово: тесты FAIL.
  - Не закрыта (пакет 3, 3Т): то же, что T010; мутация — накопительное округление заменено округлением по периодам (24 193 → 24 194) даёт красный тест.
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
  - Не закрыта (пакет 3, 3Т): то же, что T010; мутация — `tie_order` игнорируется — даёт красный тест.
  - Зависимости: T001.
- [x] **T013 [—] [backend] Чистые функции домена.**
  - Файлы: `app/backend/bm/domain/{types.py, money.py, periods.py, amortize.py, select.py, codes.py}`.
  - Сигнатуры — `12_plan.md` §6, алгоритм — `12_arch_decision.md` §4.5. Ввода-вывода нет, `float` не используется.
  - Готово: `pytest tests/unit -q` проходит полностью.
  - Закрыта техлидом (пакет 3, 3Т): `tests/unit` входит в `make test` (`run-tests.sh full`), прогон оркестратора 2026-10-21 — 583 passed, 0 failed; import-linter «bm.domain не зависит от ввода-вывода» — KEPT.
  - Зависимости: T010, T011, T012.
- [x] **T014 [—] [backend] Схема БД, раннер миграций, фикстуры.**
  - Файлы: `bm/db/migrate.py`, `bm/db/migrations/0001_init.sql` (все таблицы из `12_plan.md` §7, партиции `call_record` на текущий и следующий месяц, `REVOKE UPDATE, DELETE ON audit_log`), `tests/conftest.py` (фикстуры `db` — чистая схема на тест, `clock` — `FrozenClock`, `seed`), `tests/fixtures/seed.py` (построитель: подразделение, сотрудник, модель с ценой, лимиты, ключ — прямыми INSERT), `tests/integration/test_migrations.py`.
  - Готово: `make test` проходит; `test_migrations` проверяет наличие таблиц и запрет UPDATE на `audit_log`.
  - Закрыта техлидом (пакет 3, 3Т): `make test` зелёный во всех отчётах с 2026-10-14; в `test_migrations` есть `test_all_tables_exist`, `test_audit_log_insert_allowed_update_delete_forbidden`, `test_audit_log_privileges_revoked_for_owner`.
  - Зависимости: T001.
- [x] **T015 [—] [backend] Каркас приложения и контрактный тест.**
  - Файлы: `bm/settings.py`, `bm/main.py` (`create_app`), `bm/http/errors.py` (`application/problem+json`, `detail` на русском), `bm/http/routers/system.py` (`/healthz`, `/readyz`, `/metrics`), `tests/contract/test_openapi_contract.py`.
  - Контрактный тест: (а) `12_openapi.yaml` валиден (openapi-spec-validator); (б) хелпер `assert_matches_contract(response, operation_id)` на openapi-core, им пользуются все приёмочные тесты; (в) каждая `operationId` из контракта зарегистрирована в приложении (тест помечается `xfail(strict=True)`, пока не реализованы все роутеры).
  - Готово: `make test` проходит.
  - Зависимости: T014.
- [x] **T016 [—] [backend] Вход, роли, аудит.**
  - Файлы: `bm/access/{auth.py, permissions.py, authorize.py, audit.py}`, `bm/http/routers/auth.py`, `tests/unit/test_permissions.py`, `tests/acceptance/test_auth.py`.
  - Правила:
    - argon2id; cookie `bm_session` с `HttpOnly; SameSite=Strict`; CSRF на изменяющих запросах;
    - блокировка входа после 10 неудач на 15 мин (`429 login_locked`);
    - первый администратор создаётся из `ADMIN_INITIAL_LOGIN` / `ADMIN_INITIAL_PASSWORD` с `must_change_password=true`;
    - `PERMISSIONS` — единственная таблица прав `operationId → роли`, значения совпадают с `x-roles` в контракте (тест сверяет);
    - отказ доступа пишется в аудит (`forbidden_access`).
  - Готово: `make test`.
  - Зависимости: T015.
- [x] **T017 [P] [—] [backend] Egress и тест «0 исходящих».**
  - Файлы: `bm/egress/client.py`, `app/backend/importlinter.ini` (контракт: `httpx`, `urllib`, `http.client`, `socket` запрещены вне `bm.egress`), `tests/conftest.py` (autouse-фикстура подменяет `socket.socket.connect` и разрешает только хост и порт PostgreSQL), `tests/security/test_no_egress.py`.
  - Правила `egress`:
    - `get_client("anthropic")` при `IMPORT_ENABLED=false` выбрасывает `EgressDisabled`;
    - разрешённые хосты — только `api.anthropic.com` и `api.openai.com`;
    - webhook разрешён только на частные адреса: 10/8, 172.16/12, 192.168/16, 127/8.
  - Готово: `make test`; `lint-imports` проходит.
  - Зависимости: T015.
- [x] **T018 [P] [—] [backend] Снимок конфигурации.**
  - Файлы: `bm/budget/snapshot.py` (`ConfigSnapshot`: подразделения, назначения на сегодня, лимиты, разрешённые модели, каталог с текущими ценами, курсы, ключи по SHA-256, подписки; `load(conn)`; `ensure_fresh(conn)` одним запросом к `config_version`), `bm/config/service.py` (`apply(conn, change, actor)`: запись + аудит + `config_version += 1` в одной транзакции), `tests/integration/test_snapshot.py`.
  - Готово: `make test`.
  - Зависимости: T014.
- [x] **T019 [P] [—] [backend] Ядро уведомлений.**
  - Файлы: `bm/notify/service.py` (`create_once(conn, kind, department_id, period, period_start, message) -> bool`, UNIQUE по ключу), `bm/notify/channels.py` (`InApp`), `bm/http/routers/notifications.py` (`listNotifications`, `markNotificationRead`), `tests/integration/test_notify_once.py`.
  - Готово: `make test`.
  - Зависимости: T016.

**Контрольная точка**: `make test` зелёный, каркас админки собирается.

---

## Phase 3: US1 — Решение перед вызовом (P1) 🎯 MVP

**Независимая проверка.** Тестовый клиент шлёт запросы при заданных бюджетах и каталоге; решения совпадают с ожидаемыми.

- [x] **T020 [US1] [qa] Приёмочные тесты решений.** Тесты пишутся до реализации.
  - Файлы: `tests/acceptance/test_us1_decisions.py`, `tests/acceptance/test_edge_cases.py`, `tests/acceptance/test_messages_ru.py`, `tests/integration/test_concurrency.py`.
  - Покрытие:
    - сценарии 1.1–1.7 через `POST /api/v1/decisions`, каждый ответ проверяется `assert_matches_contract`;
    - пограничные случаи: `subject_unknown`; `model_unknown`; модель удалена из разрешённых; нулевая цена; равная цена; отрицательный остаток; подписки ≥ 100%; `subject_out_of_scope` → 403 + запись в аудит; `http_status_hint` для каждого кода;
    - `message` содержит кириллицу и дату сброса по МСК;
    - `test_concurrency`: 3 пула × 50 одновременных запросов на границе лимита; Σ `reserve` ≤ остатка до теста, строго.
  - Готово: тесты собраны и FAIL (404 или ImportError).
  - Зависимости: T015, T014.
- [x] **T021 [US1] [backend] `Budget.decide` и роутеры вызывающей системы.**
  - Файлы: `bm/budget/{service.py, repo.py}`, `bm/http/deps.py` (аутентификация по `X-Api-Key`: SHA-256 → снимок, отозванный ключ → 401), `bm/http/schemas.py` (Pydantic 1:1 с контрактом, `extra="forbid"`), `bm/http/routers/caller.py` (`createDecision`, `getIntegrationConfig` с ETag), `tests/integration/test_decide_db.py`.
  - Транзакция: `ensure_fresh` → `SELECT … FROM budget_lock … ORDER BY owner_type, owner_id FOR UPDATE` → один `SUM … FILTER` по `ledger_daily` → `select()` → `INSERT call_record` → `UPDATE budget_lock`.
  - Готово: `pytest tests/acceptance/test_us1_decisions.py tests/acceptance/test_edge_cases.py tests/integration/test_concurrency.py` проходит, `make test` зелёный.
  - Зависимости: T013, T018, T020.

**Контрольная точка**: US1 работает независимо (решения по засеянным данным).

---

## Phase 4: US2 — Расход и остатки (P1)

- [x] **T030 [US2] [qa] Приёмочные тесты расхода.** Тесты пишутся до реализации.
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
- [x] **T031 [US2] [backend] `settle`, пакет, истечение резервов, баланс, worker.**
  - Файлы: `bm/budget/service.py` (`settle`, `balance`, `expire_reserves`), `bm/http/routers/caller.py` (`reportUsage`, `reportUsageBatch`, `getCallerBalance`), `bm/worker/{main.py, jobs.py}` (цикл задач, `pg_try_advisory_lock` на задачу, `job_run`; задача `expire_reserves` раз в 60 с), `bm/metrics.py`.
  - Правила:
    - 200 отдаётся только после COMMIT;
    - `report_key` обеспечивает идемпотентность;
    - факт пишется в `ledger_daily` сотрудника и подразделения в той же транзакции;
    - резерв снимается целиком;
    - метрики собираются по всем процессам uvicorn: multiprocess-режим prometheus-client (каталог из `PROMETHEUS_MULTIPROC_DIR`, режим агрегации у gauge задан явно); тест — два процесса пишут в один каталог, `/metrics` отдаёт сумму (14_tech_rulings.md, Б6).
  - Готово: `pytest tests/acceptance/test_us2_usage.py tests/integration/test_settle_db.py` и `make test`.
  - Зависимости: T021, T030.
- [x] **T032 [P] [US1] [qa] Отказные тесты SC-002 и SC-003.** Тесты пишутся до клиента.
  - Файлы: `tests/failover/test_sc002_failover.py`, `tests/failover/test_sc003_kill.py`.
  - SC-002: стек поднят (`make up`), 2 подразделения (`open` и `closed`); `docker compose stop api worker postgres` под потоком 20 rps на 60 с → 100% вызовов `open` проходят, 100% `closed` получают `PolicyUnavailable`; после `start` и `flush()` все `report_id` есть в БД ровно один раз.
  - SC-003: `docker kill api` под нагрузкой; каждый ответ 200 на `/usage` есть в БД.
  - Готово: тесты FAIL (клиента нет).
  - Запуск на стеке из локальных процессов — T093 (14_tech_rulings.md, пакет 3, 3Д1).
  - Зависимости: T030.
- [x] **T033 [US1] [backend] Эталонный клиент `budget_client`.**
  - Файлы: `app/client/budget_client/{pyproject.toml, budget_client/client.py, buffer.py, config_cache.py}`, `tests/unit/test_client_buffer.py`.
  - Правила:
    - таймаут 50 мс;
    - кэш `integration-config` и `fail_mode` по субъекту на диске, обновление раз в 60 с;
    - буфер — SQLite (stdlib) с `PRAGMA synchronous=FULL`, ключ `report_id`;
    - `flush()` пакетами по 500 с экспоненциальной паузой;
    - интерфейс, на который опираются отказные тесты (14_tech_rulings.md, пакет 2, QA5): `decide()` возвращает объект с `outcome`, `fail_open`, `decision_id`; `report()` принимает `report_id` (задаёт вызывающий), `subject`, `model`, `occurred_at`; пакет лежит в `app/client/budget_client`.
  - Готово: `pytest tests/unit/test_client_buffer.py`; после T087 — `make up && make failover`.
  - Зависимости: T031, T032.

---

## Phase 5: US3 — Оргструктура, бюджеты, каталог (P1)

- [x] **T040 [P] [US3] [qa] Приёмочные тесты настройки.** Тесты пишутся до реализации.
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
- [x] **T041 [US3] [backend] Загрузка AD-файла.**
  - Файлы: `bm/org_import/service.py` (`import_ad_csv(conn, data: bytes, dry_run: bool, actor) -> OrgImportSummary`), `bm/http/routers/org.py` (`importOrg`).
  - Формат: CSV `login;display_name;group;is_primary`, UTF-8. Псевдоним — `emp-` + 10 случайных hex, выдаётся один раз.
  - Готово: тесты 3.1, 3.2 и `test_removed_*` проходят.
  - Состояние (14_tech_rulings.md, пакет 2, В2-0): реализация сделана; `test_removed_employee_keeps_period_spend` упирается в `getDepartmentBalance` (T071). Отметка `[x]` ставится в T071 после снятия `xfail` с этого теста.
  - Зависимости: T016, T018, T040.
- [x] **T042 [US3] [backend] Админ-CRUD.**
  - Файлы: `bm/http/routers/{departments.py, employees.py, models.py, fx.py, callers.py, admin_users.py}`. Операции — все `operationId` тегов departments, employees, models, fx, callers, admin-users из контракта.
  - Правила: изменения идут только через `config.apply` (аудит и `config_version`); `revealPseudonym` пишет аудит `reveal_pseudonym`; новое подразделение создаётся с `policy_mode=observe`.
  - Доводка T016/T019 по контракту 1.0.2 (14_tech_rulings.md, Б1–Б2):
    - отказные ответы 401/403 в `tests/acceptance/test_auth.py` и `tests/integration/test_notify_once.py` проверяются через `assert_matches_contract`; новые роутеры этой задачи — так же;
    - переменная `SESSION_COOKIE_SECURE` (bool, по умолчанию `false`) в `bm/settings.py`; при `true` cookie `bm_session` получает флаг `Secure`; тест на оба значения. Срок сессии остаётся константой 12 ч.
  - Готово: `pytest tests/acceptance/test_us3_config.py` и `make test`.
  - Зависимости: T016, T018, T040.
- [x] **T043 [P] [US3] [frontend] Экраны настройки.**
  - Файлы: `src/resources/{departments, employees, models, callers, adminUsers}/*`, `src/pages/OrgImport.tsx`, `src/__tests__/*.test.tsx`.
  - Первый шаг (14_tech_rulings.md, Б1): `npm run gen:api` по контракту 1.0.2 и версия `1.0.2` в `src/__tests__/resources.contract.test.ts`; на `403 password_change_required` — переход на экран смены пароля.
  - Содержание: вход; подразделения (режим, fail_mode, разрешённые модели, лимиты день/неделя/месяц); сотрудники по псевдонимам с личными лимитами; модели с историей цен; ключи (модальное окно «ключ показан один раз»); пользователи админки; загрузка AD со сводкой.
  - Работа идёт по типам из `schema.d.ts`; в тестах API мокается фейковым `fetch` `src/__tests__/support/contractApi.ts`, который сверяет каждый запрос и ответ с `12_openapi.yaml` (вместо MSW — 14_tech_rulings.md, пакет 2, Ф3). Имя ресурса react-admin совпадает с маршрутом из screens.md: `admin-users` (папка `adminUsers/` остаётся; пакет 2, Ф2).
  - Готово: `npm --prefix app/frontend run test` — у каждого ресурса есть тест «рендерит русские подписи и отправляет запрос по контракту».
  - Зависимости: T002, T003.

**Контрольная точка — неделя 5 (техлид).** US1–US3 зелёные, `make up && make smoke` проходит после T087. Факт по трудоёмкости сравнивается с планом (11,0 чел.-нед к этому моменту). Если прогноз выше рамки, применяется урезание 🔻1 → 🔻2 → 🔻3. Если урезания не хватает — стоп и решение заказчика (SC-013). Порог: 4,0 чел.-мес, или 4,17 чел.-мес (2 млн ₽), если заказчик выбрал вариант 1А.
**Итог (2026-10-21, `14_checkpoint_week5.md`):** US1–US3 зелёные. `make up && make smoke` не выполнены: реестры образов закрыты, порядок подтверждения поставки — 14_tech_rulings.md, 3Д1–3Д2. Прогноз с webhook — 4,13 чел.-мес, в рамке; с MCP — 4,23, выше порога. Применён 🔻1 (MCP).

---

## Phase 6: US4 — Подписки (P2)

- [x] **T050 [US4] [qa] Приёмочные тесты подписок.** Тесты пишутся до реализации.
  - Файл: `tests/acceptance/test_us4_subscriptions.py`.
  - Первый шаг — доводка засева в тестах T030/T040 (14_tech_rulings.md, пакет 2, В1-1 и В2-д). Проверки не меняются, точечные `xfail` снимаются:
    - `test_us2_usage.py::test_decision_not_followed_charges_actual_model` — 99 980 ₽ засеваются на `TODAY - timedelta(days=1)`, а не на сегодня;
    - `test_us3_config.py::test_3_5_new_price_from_15th_old_events_unchanged` — повторный вход после `clock.set(15-е 10:00)` (сессия живёт 12 ч).
  - Покрытие:
    - сценарии 4.1–4.6;
    - подписка сотрудника списывается и из его лимита, и из лимита подразделения;
    - курс в USD: курс месяца / последний с предупреждением / «нет курса»;
    - предпросмотр доли до сохранения;
    - уведомление `subs_share` один раз;
    - закрытие — прошлое сохраняется;
    - `unallocated` не списывается;
    - курс изменён задним числом — M месяца не меняется.
  - Готово: тесты FAIL (новые); тесты T030/T040 после доводки — PASS.
  - Зависимости: T030.
- [x] **T051 [US4] [backend] Подписки и амортизация в остатке.**
  - Файлы: `bm/http/routers/subscriptions.py` (5 операций), `bm/budget/service.py` (амортизация в `balance` и `decide` через `subscription_month`), `bm/worker/jobs.py` (задача на 1-е число 00:05 МСК: материализовать `subscription_month`, проверить долю подписок).
  - Правила: денежные поля подписки неизменяемы, их защищает триггер из T014. M месяца берётся только из материализованной `subscription_month` — на это опирается тест QA «подписки ≥ 100%» (14_tech_rulings.md, пакет 2, QA7).
  - Готово: `pytest tests/acceptance/test_us4_subscriptions.py` и `make test`; снят точечный `xfail(T051)` с `test_subscriptions_over_100_percent_block_paid_calls`.
  - Подтверждено (14_tech_rulings.md, пакет 3, 3Б1–3Б3): доля подписок считается за весь период; `endSubscription` датой в прошлом и повторное открытие — 422; «не распределено» в отчёте — по прошедшим дням.
  - Зависимости: T031, T019, T050.
- [x] **T052 [P] [US4] [frontend] Подписки и курсы.**
  - Файлы: `src/resources/subscriptions/*` (форма с живым предпросмотром `previewSubscription`, предупреждение при доле ≥ порога), `src/resources/fxRates/*` (имя ресурса react-admin — `fx-rates`, совпадает с маршрутом `#/fx-rates`, на который уже ведёт ссылка «Ввести курс»; 14_tech_rulings.md, пакет 2, Ф2), тесты.
  - Готово: `npm --prefix app/frontend run test`.
  - Доводка — T095 (пакет 3, 3Ф2): список сотрудников в форме загружается всеми страницами.
  - Зависимости: T043.

---

## Phase 7: US5 — Импорт Anthropic и OpenAI (P2)

- [x] **T060 [US5] [qa] Приёмочные тесты импорта и эталонный остаток SC-007.** Тесты пишутся до реализации.
  - Файлы: `tests/acceptance/test_us5_import.py`, `tests/integration/test_import_pipeline.py`, `tests/acceptance/test_sc007_reference_balance.py`, `tests/fixtures/anthropic/*.json`, `tests/fixtures/openai/*.json`.
  - Фикстуры берутся из записанных ответов спайка (день 2). Если их нет, фикстуры собираются по документации провайдеров с пометкой `# synthetic` и заменяются после спайка.
  - Покрытие:
    - сценарии 5.1–5.7: `import` против `decision_api`; несопоставленный источник → `unallocated` и уведомление; повтор без изменений не меняет итог; уточнение заменяет значение; 2 неудачи подряд → уведомление; при выключенном флаге — 0 соединений; поздний импорт после смены месяца;
    - SC-007: токенные события + импорт + подписка USD + переход месяца, остаток совпадает с ручным расчётом до копейки.
  - Готово: тесты FAIL.
  - Зависимости: T031.
- [x] **T061 [US5] [backend] Конвейер импорта и справочник источников.**
  - Файлы: `bm/provider_import/{pipeline.py, sources/base.py, sources/recorded.py}`, `bm/http/routers/{provider_sources.py, imports.py}`, `bm/worker/jobs.py` (запуск в `IMPORT_HOUR_MSK`, повтор раз в час до `IMPORT_RETRY_UNTIL_HOUR_MSK`, окно `IMPORT_WINDOW_DAYS`).
  - Правила:
    - upsert строки → дельта в `ledger_daily` подразделения в той же транзакции;
    - пересчёт статуса строки по текущему `mode`;
    - Anthropic с `kind≠workspace` → `422 unsupported_source_kind`;
    - ключ уведомлений без периода (14_tech_rulings.md, Б5): `import_failure` и `import_unallocated` — `department_id=NULL`, `period=NULL`, `period_start` = дата МСК, когда обнаружено условие (не больше одного уведомления каждого вида в сутки); `create_once` отклоняет `period_start=None` (`ValueError`), тест на это.
  - Готово: тесты T060 на `RecordedSource` проходят, `make test` зелёный.
  - Дефект против FR-031 (14_tech_rulings.md, пакет 3, 3Б6): изменения справочника источников не пишутся в аудит. Исправляется первым шагом T062; T061 не переоткрывается.
  - Зависимости: T017, T019, T060.
- [x] **T062 [P] [US5] [backend] Коннектор Anthropic.**
  - Первый шаг (14_tech_rulings.md, пакет 3, 3Б6): `createProviderSource`, `updateProviderSource`, `deleteProviderSource` идут через `config.apply` (аудит «было/стало», `config_version`); снят точечный `xfail` с `tests/acceptance/test_us6_reports_roles.py::test_6_5_audit_records_who_when_before_after`. Без этого T062 не закрывается.
  - Файлы: `bm/provider_import/sources/anthropic.py`, `tests/integration/test_anthropic_source.py`; для прокси — `bm/egress/client.py`, `bm/settings.py`, `tests/security/test_no_egress.py`.
  - Работа: `cost_report` с шагом `1d`, группировка по `workspace_id` и `description`, пагинация, сумма в USD; модель извлекается из `description`. Ключ берётся только из `ANTHROPIC_ADMIN_KEY(_FILE)`, HTTP-клиент — только через `egress`. В тестах вместо сети — транспорт httpx с фикстурами.
  - Прокси для импорта (решение заказчика по Б4 — «сделайте оба»; 14_tech_rulings.md, пакет 2, Б4):
    - `egress` по-прежнему не берёт прокси из окружения (`trust_env=False`); стандартные `HTTP_PROXY`/`HTTPS_PROXY` не действуют;
    - новая переменная `IMPORT_HTTPS_PROXY` в `bm/settings.py`, по умолчанию пусто. Пусто — прямой выход, как раньше. Задана — явный `proxy=` у httpx-клиента **только** для назначений `anthropic` и `openai`; к `webhook` не применяется никогда;
    - значение — URL со схемой `http://` или `https://`, иначе ошибка конфигурации при старте (текст на русском);
    - логин и пароль из URL прокси не попадают в журналы, `job_run`, ответы API и на экран «Состояние импорта» (маскируются);
    - белый список хостов назначения в `egress` не меняется: через прокси идут запросы только к `api.anthropic.com` / `api.openai.com`;
    - тесты: прокси пусто → клиент без прокси; прокси задан → прокси у клиента `anthropic`, у `webhook` его нет; `IMPORT_ENABLED=false` при заданном прокси → `EgressDisabled` и 0 соединений, включая соединение с прокси; URL с паролем не появляется в журнале.
  - Риск формата (14_tech_rulings.md, пакет 4, 4Б1): единицы `amount` (центы, ₽ = /100) по документации провайдера не подтверждены; фикстуры синтетические. Код не меняется, в релизном гейте пункт идёт как **НЕ ПРОВЕРЕНО** (4Р) до сверки первого реального ответа с консолью Anthropic на стенде заказчика (`docs/admin-guide.md` §14, п. 10). При расхождении в 100 раз правятся делитель, фикстуры и тест.
  - Межмодульный интерфейс (пакет 4, 4Б2): `bm.provider_import.catalog.create/update/delete` принимают обязательный `now` — принято.
  - Готово: `make test`.
  - Зависимости: T061.
- [x] **T063 [P] [US5] [backend] 🔻3 Коннектор OpenAI.**
  - Остаётся в v1 (контрольная точка недели 5, `14_checkpoint_week5.md`).
  - Файлы: `bm/provider_import/sources/openai.py`, `tests/integration/test_openai_source.py`.
  - Работа: `GET /organization/costs`, `bucket_width=1d`, `group_by=project_id,line_item`, пагинация. Названия полей сумм сверяются с фикстурами спайка.
  - Прокси (14_tech_rulings.md, пакет 2, Б4): клиент берётся только из `egress("openai")`, поэтому `IMPORT_HTTPS_PROXY` применяется автоматически; своей настройки прокси в коннекторе нет. Тест: при заданном прокси он есть у клиента `openai`. Настройка прокси сделана в T062 и остаётся, даже если T063 урезана.
  - Готово: `make test`.
  - Зависимости: T061.
- [x] **T064 [P] [US5] [frontend] Источники и состояние импорта.**
  - Файлы: `src/resources/providerSources/*`, `src/pages/ImportStatus.tsx` (включён или нет, ключ задан или нет, последняя загрузка, отставание, несопоставленные источники, кнопка «Загрузить сейчас»), тесты.
  - Готово: `npm --prefix app/frontend run test`.
  - Зависимости: T043.

---

## Phase 8: US6 — Отчёты, роли, уведомления (P2)

- [x] **T070 [US6] [qa] Приёмочные тесты отчётов и безопасности.** Тесты пишутся до реализации.
  - Файлы: `tests/acceptance/test_us6_reports_roles.py`, `tests/security/{test_roles_matrix.py, test_no_content.py, test_secrets_not_leaked.py}`.
  - Покрытие:
    - сценарии 6.1–6.6;
    - CSV без идентификаторов сотрудников, с защитой от CSV-инъекции;
    - пороги 80% и 100% — по одному уведомлению за период; порог считается по `used_pct` (расход без резервов, 14_tech_rulings.md, пакет 2, В1-5);
    - матрица «роль × операция» строится из `PERMISSIONS` и контракта: 0 успешных обращений к чужим данным, 100% отказов в аудите;
    - в схеме БД нет текстовых колонок, кроме белого списка; в логах прогона нет полей `prompt`, `messages`, `content`;
    - значения `*_ADMIN_KEY` не встречаются в логах, ответах и дампе БД.
  - Готово: тесты FAIL.
  - Зависимости: T042.
- [x] **T071 [US6] [backend] Отчёты, баланс подразделения, журналы, фоновые задачи.**
  - Файлы: `bm/reports/service.py`, `bm/http/routers/{reports.py, events.py, audit.py}`, `bm/http/routers/departments.py` (`getDepartmentBalance`, `listDepartmentBalanceItems`), `bm/worker/jobs.py`, `bm/logging.py`.
  - Фоновые задачи: пороги раз в минуту; `agg_daily` раз в сутки; ретенция — создание партиций на 2 месяца вперёд и `DROP` партиций старше `RETENTION_DETAIL_MONTHS`.
  - Ключ уведомлений без периода (14_tech_rulings.md, Б5): `fx_missing` — `department_id=NULL`, `period=NULL`, `period_start` = 1-е число месяца без курса (одно уведомление на месяц); `reservation_no_report` — подразделение резерва, `period=NULL`, `period_start` = дата МСК обнаружения.
  - Правила `LimitBalance` (14_tech_rulings.md, пакет 2, В1-5) — одинаково в `getCallerBalance`, `getDepartmentBalance` и проверке порогов:
    - `spent` — весь расход периода (токены, импорт, амортизация подписок), без резервов; `remaining_rub = limit − spent − reserved`;
    - `used_pct = spent / limit × 100`, один знак, `ROUND_HALF_UP`, может быть больше 100; при `limit = 0`: `spent = 0` → `"0.0"`, иначе `"100.0"`;
    - `over_limit = spent > limit` (фактический перерасход; резервы его не создают). Текущий код T031 (`remaining < 0`) приводится к этому правилу с тестом.
  - Журналы и метрики (14_tech_rulings.md, пакет 2, В1-6, В1-7):
    - `bm/logging.py` — JSON-форматтер на stdlib `logging` (без новых зависимостей): одна строка JSON, поля `ts`, `level`, `logger`, `msg` и белый список доп. полей; уровень из `LOG_LEVEL`; подключается в `api` и `worker`; тест — строка журнала разбирается как JSON и не содержит значений `*_ADMIN_KEY` и `IMPORT_HTTPS_PROXY`;
    - gauge `reservations_open` из worker убирается (worker `/metrics` не отдаёт, общий каталог multiprocess между контейнерами не заводится).
  - `decision_not_followed` (пакет 2, В1-3): `call_record.final_model` = фактическая модель, по которой списаны деньги; отчёты и разбивка по моделям идут по `final_model`. Новая колонка и миграция не нужны.
  - Готово: `pytest tests/acceptance/test_us6_reports_roles.py tests/security` и `make test`; контрактный тест из T015 снимает `xfail` для всех операций, кроме MCP; снят `xfail` с `test_removed_employee_keeps_period_spend` и T041 отмечена `[x]`.
  - Зависимости: T031, T019, T051, T061, T070.
- [x] **T072 [P] [US6] [frontend] Панель, отчёты, журналы.**
  - Файлы: `src/pages/{Dashboard, Reports, Reconciliation, Notifications}.tsx`, `src/resources/{events, audit}/*`, меню по ролям, тесты.
  - Содержание: панель — по каждому периоду лимит, расход по источникам, резервы, остаток, дата сброса и пометка «бюджет занят подписками»; разбивка по моделям; выгрузка CSV.
  - Доступность (14_tech_rulings.md, пакет 2, Ф1): axe не подключается. Контраст проверяет тест `src/__tests__/contrast.test.ts`: функция по формуле WCAG 2.x (без зависимостей) считает отношение для всех пар из 13_design.md §8 по значениям `design/tokens.json` в обеих темах; порог 4,5:1 для текста и 3:1 для `border-strong`. ARIA и клавиатура — запросами `getByRole`/`getByLabelText` и проверками фокуса, как в T043.
  - Готово: `npm --prefix app/frontend run test && npm --prefix app/frontend run build`.
  - Зависимости: T043.
- [x] **T073 [US6] [backend] 🔻2 Webhook-канал.**
  - **Входит в v1** (контрольная точка недели 5, `14_checkpoint_week5.md`). Если к началу T089 задача не закрыта с выводом команды готовности, применяется 🔻2: `WEBHOOK_URL` игнорируется, уведомления создаются с `webhook_status=disabled`, ветка `pending` удаляется.
  - Первый шаг (14_tech_rulings.md, пакет 3, 3Б7): время записи аудита берётся из `Clock` приложения во всех местах записи (`config.apply`, `forbidden_access`, вход, `reveal_pseudonym`, импорт AD); `DEFAULT now()` у колонки остаётся. Тест: под `FrozenClock` запись видна в `listAudit` за замороженный интервал, `at` равно замороженному времени.
  - Файлы: `bm/notify/channels.py` (`Webhook` через `egress("webhook")`, повтор 3 раза, `webhook_status`), `tests/integration/test_webhook.py` (локальный HTTP-сервер на 127.0.0.1).
  - Уточнение (пакет 3): при заданном `WEBHOOK_URL` статус `pending` получают уведомления **всех** видов, не только созданные задачей порогов (сейчас `subs_share` и уведомления импорта создаются с `disabled`). Доставка — задачей worker: `pending` → `delivered` или → `failed` после 3 неудачных попыток. В теле запроса — JSON с полями `id`, `kind`, `department_id`, `period`, `period_start`, `message`, `created_at`, без идентификаторов сотрудников. Формат описывается в `docs/admin-guide.md` (T090). Тесты: доставка; 3 неудачи → `failed`; `WEBHOOK_URL` пуст → `disabled` и 0 соединений; публичный адрес → отказ `egress`.
  - Пакет 4 (14_tech_rulings.md, 4Б3, 4Б4): готовность принята — 720 passed и 1 xfailed; единственное постороннее падение (`test_compose_static.py`, `DATABASE_URL` из окружения) устранено DevOps. 3 попытки внутри одного такта без колонки счётчика и миграции — приняты. Бюджет времени такта (30 с) — отдельной задачей T096, T073 не переоткрывается.
  - Готово: `make test`.
  - Зависимости: T071.

---

## Phase 9: US7 — MCP (P3) 🔻1 — УРЕЗАНО

**Урезано на контрольной точке недели 5** (`14_checkpoint_week5.md`): прогноз с MCP — 4,23 чел.-мес / 2,03 млн ₽, выше порога SC-013. В v1 задачи не делаются, T089 их не ждёт. Вернуть — только решением заказчика (там же, п. 5).

- [ ] **T080 [US7] [qa] Тест эквивалентности HTTP и MCP.** **🔻1 УРЕЗАНА — в v1 не делается.** Тест пишется до реализации.
  - Файл: `tests/acceptance/test_us7_mcp.py`.
  - Покрытие: сценарии 1.1–1.4 и 2.1 параметризуются по адаптеру (`http`, `mcp`); результаты совпадают поле в поле, кроме `decision_id` и `created_at`. Без ключа → ошибка `unauthorized`.
  - Готово: тест FAIL.
  - Зависимости: T031.
- [ ] **T081 [US7] [backend] MCP-сервер.** **🔻1 УРЕЗАНА — в v1 не делается.**
  - Файл: `bm/mcp/server.py` — mcp 2.3, Streamable HTTP, монтирование в `create_app` на `/mcp`, инструменты `request_decision`, `report_usage`, `get_balance` поверх `Budget`, тот же `X-Api-Key` и `subject_scope`.
  - Готово: `pytest tests/acceptance/test_us7_mcp.py` и `make test`.
  - Зависимости: T080.

---

## Phase 10: Поставка и сквозные проверки

**Среда разработки** (14_tech_rulings.md, пакет 3, 3Д1–3Д2): реестры образов закрыты (403). Поставка подтверждается статически, в CI (T092, с журналом прогона) и на стеке из локальных процессов (T087, T093). Каждый гейт получает статус PASS / PASS-замена / НЕ ПРОВЕРЕНО / FAIL.

- [x] **T086 [P] [—] [qa] Нагрузочный тест SC-001 и SC-004.**
  - Файлы: `tests/load/{targets.py, run_load.sh}`, цель `make load`.
  - Работа: генерация целей vegeta для 1 000 сотрудников и 30 моделей; 50 rps × 10 мин; p95 и p99 берутся из гистограммы `/metrics` и из отчёта vegeta; граница лимита: факт = оценка × 1,1, перерасход ≤ 2%.
  - В этой среде (пакет 3, 3Д1): прогон идёт на стеке из локальных процессов (`make up-local`). Если vegeta получить нельзя, нагрузку даёт генератор на `httpx` (уже есть в dev-зависимостях) с фиксированной частотой; p95/p99 берутся из `/metrics`, в отчёте указано «vegeta не использован». Числа помечаются «среда разработки, не эталонный стенд», статус гейта SC-001 — PASS-замена.
  - Пакет 5 (14_tech_rulings.md, 5Д1, 5Д2, 5Д8): тест готов, SC-004 — PASS-замена; **SC-001 — FAIL из-за дефекта Д-1** (~40 мс на каждом повторном запросе keep-alive при 2 воркерах), исправляется в T098. После T098 QA повторяет полный 10-минутный `make up-local && make load BM_STACK=local` **без параллельных прогонов других исполнителей** (`loadavg` до и после — в отчёте) и замер эталонного `budget_client` (200 решений, тайм-аут по умолчанию 50 мс — 0 `fail_open` по тайм-ауту). Тайм-аут `budget_client` по умолчанию не меняется. Цель `load` в `Makefile` принимает DevOps.
  - Готово: `make up && make load` печатает PASS и значения p95/p99 (здесь — `make up-local && make load`).
  - Зависимости: T031, T087, T098.
- [x] **T087 [—] [devops] Полная поставка.**
  - Файлы: `deploy/docker-compose.yml` (сервисы `api`, `worker`, `postgres:18`, `backup`; сети `core` с `internal: true` для `postgres` и `backup`, `edge` для `api` и `worker`; healthcheck; `restart: unless-stopped`; порт `${BIND_ADDR}:${API_PORT}:8080`; секреты через `*_FILE`), `deploy/Dockerfile` (стадия node собирает SPA, стадия python копирует `dist` в `/app/static/admin`), `deploy/backup/{backup.sh, restore.sh}`, `tests/smoke/test_smoke.py` (тест пишется первым).
  - Дополнительно (14_tech_rulings.md, Б2, Б6): `SESSION_COOKIE_SECURE=false` с комментарием в `deploy/.env.example` («true — если админка открыта через HTTPS»); у `api` — `PROMETHEUS_MULTIPROC_DIR` на `tmpfs`, каталог очищается при старте контейнера. Каталог только у `api`, с `worker` не делится (пакет 2, В1-6).
  - Дополнительно (14_tech_rulings.md, пакет 2, Б4): `IMPORT_HTTPS_PROXY=` (пусто) в `deploy/.env.example` с комментарием «пусто — прямой выход к API провайдеров; задайте `http(s)://хост:порт`, если выход только через корпоративный прокси; действует только на импорт расхода при `IMPORT_ENABLED=true`, стандартные HTTP(S)_PROXY утилита игнорирует». Переменная передаётся тем сервисам, которые запускают импорт (`worker`, а также `api`, если кнопка «Загрузить сейчас» выполняет импорт в процессе `api`).
  - Дополнительно (пакет 2, QA6): пользователь и БД PostgreSQL в контейнере — `bm` / `bm`; значения `BM_ADMIN_LOGIN` / `BM_ADMIN_PASSWORD` для отказных тестов по умолчанию берутся из `ADMIN_INITIAL_*`.
  - Дополнительно (пакет 3, 3К, 3Б9, 3Д1):
    - extra `mcp` удаляется из `app/backend/pyproject.toml` и `uv.lock`, вместе с комментарием `UV_EXTRAS` в `deploy/Dockerfile`;
    - `app/client` добавляется в пути `ruff` целей `lint` и `fmt`;
    - статическая проверка: `docker compose -f deploy/docker-compose.yml config --quiet` и `tests/deploy/test_compose_static.py` (перечень требований — 3Д1(а));
    - стек из локальных процессов: `deploy/local/stack.sh` и цели `up-local`, `down-local`. Свой кластер PostgreSQL на отдельном порту, `api` с 2 воркерами, `worker`, pid-файлы, команды `stop`/`start`/`kill` по сервисам. Общий кластер на 5432 не останавливать никогда;
    - в `tests/smoke` проверки, которые есть только у образа, помечаются `compose_only`.
  - Дополнительно (пакет 4, 4Д1, 4Д4, 4Д6, 4Д8):
    - по журналу CI `eb17642` (его выгружает оркестратор в `deploy/ci/runs/`) — статусы пунктов в `14_devops.md`, digest базовых образов закреплён в Dockerfile, найденное исправлено; пароли стенда в журнале маскируются (`::add-mask::`);
    - после T097 — `IMPORT_HTTPS_PROXY` передаётся `worker` через compose secret (`IMPORT_HTTPS_PROXY_FILE`), `deploy/.env.example` обновлён, `test_compose_static.py` проверяет, что значения прокси нет в отрендеренном окружении `worker`;
    - `compose_only`-тест `test_admin_spa_served_from_image` проходит после T096;
    - контейнер `backup` от root — известное ограничение v1 (в `16_release.md`), не исправляется.
  - Дополнительно (пакет 5, 5Д4, 5Д6, 5Д8): на CI `34d28d0` вся цепочка в Docker прошла (`build`, `up`, `smoke` с `compose_only`, `backup`, `restore`, `smoke`, `failover`) — это предрелизный прогон, в релиз как PASS не переносится. Релизный SHA — один коммит с T096, T097, T098, T099 и правками 4Д/5Д. `CMD` образа и `stack.sh` запускают `api` через `python -m bm.serve` (T098). Цель `load` от QA принимается в `Makefile`.
  - Цели Makefile: `up`, `down`, `smoke`, `backup`, `restore`; дополнительно `up-local`, `down-local`.
  - Готово: `make up && make smoke && make backup && make restore && make smoke`; время от `git clone` до первого решения ≤ 30 мин по `docs/admin-guide.md` (SC-009, замер QA).
  - Готово в этой среде (пакет 3, 3Д1–3Д2):
    - здесь: `docker compose config --quiet` (exit 0); `tests/deploy/test_compose_static.py`; `make up-local && make smoke && make backup && make restore && make smoke` (backup/restore — против локального кластера);
    - в CI (T092): та же цепочка в Docker с журналом прогона для того же SHA.
    - `[x]` ставится только при наличии журнала CI **для релизного SHA** (пакет 4, 4Д1: прогон `eb17642` — репетиция). Без него задача остаётся `[ ]` со статусом «проверено локально», а сборка образа и стек в Docker уходят в `16_release.md` как НЕ ПРОВЕРЕНО.
  - Зависимости: T031, T043, T096, T097, T098, T099.
- [x] **T088 [P] [—] [devops] SDL-проверки.**
  - Файлы: `deploy/ci/check.sh`, `deploy/ci/licenses-allow.txt` (MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, PostgreSQL, PSF-2.0, 0BSD, Python-2.0), цели `check` и `egress-host-check`.
  - Работа: CycloneDX SBOM для Python и npm, pip-audit, npm audit, Trivy по образу. Сборка падает на GPL, AGPL, SSPL, LGPL и на `@react-admin/ra-*`. В SBOM учитывается прямая зависимость `python-multipart` (Apache-2.0, добавлена в T041). MPL-2.0 в белый список не добавляется (пакет 2, Ф1).
  - Пакет 3 (3Д3, 3Б9, 3К, 3Ф3): MIT-0 — в белом списке; `certifi` (MPL-2.0) — узкое исключение (пакет и версия) в `licenses-exceptions.txt`, подтверждение юриста — открытый пункт `16_release.md`; `app/client/budget_client` включён в SBOM; SBOM поставки — без extras; npm-SBOM подтверждает лицензию `@tanstack/react-query`.
  - Пакет 4 (4Д2): обновление `certifi` 2026.7.22 → 2026.10.19 (PYSEC-2026-4320 по выводу `pip-audit`) подтверждено; узкое исключение перенесено на 2026.10.19.
  - Не закрыта (пакет 3, 3Т): `make check` прошёл со `SKIP` npm и Trivy. Не хватает вывода `CHECK_STRICT=1 make check` на текущем дереве с npm-веткой по `app/frontend` и пунктов выше. Trivy — только в CI (T092); без журнала CI шаг Trivy идёт в релиз как НЕ ПРОВЕРЕНО.
  - Пакет 5 (5Д3, 5Д4, 5Д5): на CI `34d28d0` `CHECK_STRICT=1 make check` упал только на Trivy CRITICAL (`certifi` 2026.6.17 в образе). Инструмент сработал правильно, задача остаётся `[x]`; дефект образа исправляется в T099. npm: 7 moderate через react-admin — приняты как известное ограничение v1.
  - Готово: `make check` с exit 0 и SBOM в `deploy/ci/out/`.
  - Зависимости: T001.
- [ ] **T090 [P] [—] [devops] Руководство администратора.**
  - Файл: `docs/admin-guide.md` (RU): установка за ≤ 30 мин; переменные окружения; резервное копирование и восстановление; включение импорта и ключи; правила денег (копейки, накопительная амортизация, сутки UTC импорта); режимы политики; условие о локальном акте перед `enforce`; админка через HTTPS на обратном прокси — `SESSION_COOKIE_SECURE=true`, срок сессии 12 ч; адрес webhook — IP, а не имя хоста (14_tech_rulings.md, Б2, Б3).
  - Раздел «Импорт через корпоративный прокси» (14_tech_rulings.md, пакет 2, Б4): по умолчанию импорт выходит к `api.anthropic.com` / `api.openai.com` напрямую; если прямого выхода нет — задать `IMPORT_HTTPS_PROXY`; прокси действует только на импорт и только при `IMPORT_ENABLED=true`, на webhook и остальное не влияет; стандартные `HTTP(S)_PROXY` игнорируются намеренно; на прокси нужно разрешить CONNECT к двум хостам провайдеров; логин и пароль прокси в URL — секрет, `.env` в репозиторий не кладётся; как проверить — кнопка «Загрузить сейчас» и экран «Состояние импорта».
  - Пакет 3: раздел «Webhook» — формат тела (T073), повторы и статусы; раздел «Проверка поставки на своём стенде» — чек-лист для пунктов, которые в релизе помечены НЕ ПРОВЕРЕНО (сборка образа, `make up/smoke`, `make failover`, `make backup/restore`, `egress-host-check`, замер SC-009) (3Д2).
  - Пакет 4 (14_tech_rulings.md, 4Д9) — правки по приёмке техлида:
    1. §9: удалить абзац «если T062 ещё не закрыта, переменная игнорируется» (T062 закрыта);
    2. §10: последний абзац — T073 закрыта, webhook в v1, оговорку про 🔻2 убрать;
    3. §10: сверить фразу «имя хоста проверяется только при старте `worker`» с кодом T073 (проверка `egress` в каждом такте, `job_run.error_class=EgressForbidden`); после T096 — «за такт отправляется не больше, чем укладывается в 30 с»;
    4. §10: `127.0.0.0/8` внутри контейнера — это сам `worker`, а не хост;
    5. §14: пункт 10 «Единицы сумм импорта» — импорт за одни сутки UTC с реальным admin-ключом, сумма за эти сутки в «Отчётах» сравнивается с консолью провайдера; расхождение в 100 раз — FAIL, сообщить поставщику (4Б1);
    6. §4 и §9: `IMPORT_HTTPS_PROXY` передаётся файлом-секретом (после T097).
  - Готово: техлид принял документ, QA прошёл его на чистой машине.
  - Статус (пакет 4, 4Д9): техлид принимает документ после правок 1–6. Прохождение на чистой машине здесь невыполнимо (реестры образов закрыты), поэтому задача остаётся `[ ]`, а в `16_release.md` пункт идёт как НЕ ПРОВЕРЕНО: его проверяет QA на стенде с доступом к образам или заказчик по §14.
  - Пакет 5 (5Д4): журнал CI эту задачу не закрывает — прогон CI не установка по руководству. Если `CMD` меняется в T098, в руководстве команды запуска сверяются (переменная `UVICORN_WORKERS` остаётся).
  - Зависимости: T087.
- [x] **T091 [P] [—] [backend] Руководство по интеграции.**
  - Файл: `docs/integration-guide.md` (RU): протокол `decisions` → вызов модели → `usage`; `http_status_hint`; эталонная схема с таймаутом 50 мс, fail-open и буфером; пример на `budget_client`; рекомендация закрыть прямой доступ к провайдерам.
  - Коды отказов (14_tech_rulings.md, пакет 2, В1-2, В1-4): решение `subject_unknown` для ключа без подразделения — `policy_mode=enforce`, `fail_mode=DEFAULT_FAIL_MODE`, `department_id=null`; 422 у отчёта без решения — `subject_unknown`, `model_unknown`, `fx_rate_missing`; `getCallerBalance` без `login` у ключа не-service — 404 `subject_unknown`.
  - Пакет 3 (3К, 3Б9): MCP (`/mcp`, `x-mcp-tools` контракта) в v1 не поставляется — интеграция только по HTTP API; права на `state_dir` клиента и то, что в буфере лежат логины субъектов (ПДн), а ключа API нет.
  - Готово: техлид принял документ.
  - Принята техлидом 2026-10-21 (14_tech_rulings.md, пакет 4, 4Т): пути, поля и коды сверены с контрактом 1.0.2 и кодом сервера; В1-2, В1-4, 3К, 3Б9 отражены.
  - Зависимости: T033.
- [x] **T092 [P] [—] [devops] CI GitHub Actions** (14_tech_rulings.md, пакет 3, 3Д1(б), 3Д4).
  - Файл: `.github/workflows/ci.yml`.
  - Работа: на push в `main` выполняются `make lint`, `make test` против сервиса `postgres:18`, `npm --prefix app/frontend run test && npm --prefix app/frontend run build`, `make build`, `make up && make smoke && make backup && make restore && make smoke`, `make failover`, `CHECK_STRICT=1 make check` (с Trivy по образу), попытка `make egress-host-check`. SBOM и сводка `make check` — артефакты прогона. Сторонних actions — только checkout, версию проверить (иначе пометка [Гипотеза]). Секретов в workflow нет.
  - Пакет 4 (4Д1, 4Д3):
    - CI запускает оркестратор: коммит `eb17642` запушен, журнал и артефакты оркестратор выгружает через API GitHub в `deploy/ci/runs/<sha>.log`; прогон `eb17642` — репетиция, по нему DevOps правит workflow и закрепляет digest образов;
    - разрешён `actions/upload-artifact` (официальный, издатель `actions/`): SBOM и сводка `make check` — артефактом, дамп base64 из журнала убрать, sha256 SBOM и сводка остаются в журнале; версию проверить, иначе [Гипотеза];
    - пароли стенда, сгенерированные в прогоне, маскируются `::add-mask::`.
  - Пакет 5 (5Д4, 5Д7): workflow принят по прогону `34d28d0` (`deploy/ci/runs/34d28d0.md`): все шаги со статусами, сводки, версии, digest, sha256 SBOM; красный только Trivy (T099). Разнесение цепочки по шагам принято; шаг с невыполненным предусловием в сводке — FAIL или «пропущен: причина», но не PASS. В разборе релизного журнала DevOps подтверждает, что `egress-host-check` действительно выполнился (иначе — НЕ ПРОВЕРЕНО). Не хватает только журнала для релизного SHA.
  - Готово: журнал прогона (`gh run view --log` или выгрузка заказчика) для SHA **релизного** коммита приложен к `14_devops.md`. Пока журнала нет, задача остаётся `[ ]`, а её пункты в релизе — НЕ ПРОВЕРЕНО.
  - Зависимости: T087, T088.
- [x] **T093 [—] [qa] Доводка тестов пакета 3 и отказные тесты на стеке из локальных процессов** (14_tech_rulings.md, пакет 3, 3Б2, 3Б5, 3Б9, 3Д1(в)).
  - Файлы: `tests/failover/{stack.py, conftest.py}`, `tests/acceptance/test_us6_reports_roles.py`, `tests/acceptance/test_us4_subscriptions.py`.
  - Работа:
    - `test_6_2_csv_injection_is_neutralized`: `bump_config(db)` после засева `user1`, `user2`, точечный `xfail` снят, проверки не меняются (3Б5);
    - тесты: `endSubscription` датой в прошлом → 422; повторное открытие закончившейся подписки → 422 (3Б2);
    - прогрев в SC-002: `decide` повторяется до ответа без `fail_open`, не больше 5 попыток (3Б9);
    - драйвер `BM_STACK=compose|local` в `tests/failover/stack.py`. В режиме `local`: `stop` = SIGTERM, до 10 с ожидания, затем SIGKILL; `kill` = SIGKILL группы процессов `api`; проверка в БД — по `DATABASE_URL` локального кластера; управление процессами — через `deploy/local/stack.sh` (T087). Если остановить PostgreSQL нельзя, эта часть SC-002 пропускается с причиной и помечается НЕ ПРОВЕРЕНО.
  - Готово: `make test` без этих двух `xfail`; `make up-local && BM_STACK=local make failover` — SC-002 и SC-003 PASS (статус гейта — PASS-замена).
  - Зависимости: T087.
- [x] **T095 [P] [US4] [frontend] Доводка пакета 3** (14_tech_rulings.md, пакет 3, 3Ф2, 3Ф3, 3Ф5).
  - Файлы: `src/resources/subscriptions/SubscriptionCreate.tsx`, тесты.
  - Работа:
    - список сотрудников подразделения в форме подписки загружается всеми страницами (`page_size=200`, пока не набран `total`). Тест: 450 сотрудников → 450 в списке, три запроса по контракту;
    - период аудита по умолчанию (7 суток МСК) лежит в общем модуле констант интерфейса;
    - вывод `npm ls @tanstack/react-query` — один экземпляр, версия та же, что требует react-admin.
  - Готово: `npm --prefix app/frontend run test && npm --prefix app/frontend run build`.
  - Зависимости: T052.
- [x] **T096 [—] [backend] Доводка пакета 4: админка из образа и бюджет такта webhook** (14_tech_rulings.md, пакет 4, 4Д6, 4Б4). Исполнитель — Backend 2.
  - Файлы: `bm/main.py`, `bm/notify/channels.py`, `tests/integration/test_admin_static.py` (новый), `tests/integration/test_webhook.py`.
  - Работа — раздача админки (4Д6):
    - в `create_app` монтируется `StaticFiles(directory=…, html=True)` на `/admin`; каталог — параметр `create_app`, по умолчанию `/app/static/admin` (константа, новой переменной окружения нет);
    - каталога нет — монтирования нет, одна строка `info` в журнале, API работает (разработка, локальный стек);
    - `/admin` → `/admin/`; ассеты по `base: '/admin/'` (`app/frontend/vite.config.ts`); маршруты SPA хешевые, подмена отсутствующих путей на `index.html` не нужна;
    - `/api/*`, `/healthz`, `/readyz`, `/metrics` не затронуты; контракт не меняется; если контрактный тест T015 сверяет маршруты приложения с контрактом в обе стороны, монтирование статики исключается из сверки явно;
    - новых зависимостей нет (`StaticFiles` — из Starlette в составе FastAPI); если нужен пакет вне `uv.lock` — стоп и вопрос техлиду;
    - тест: временный каталог с `index.html` и `assets/app.js` → `GET /admin/` 200 `text/html`, `GET /admin/assets/app.js` 200; `GET /admin/../bm/settings.py` и `GET /admin/nope.js` → 404; без каталога — `GET /admin/` 404, `GET /healthz` 200.
  - Работа — бюджет такта webhook (4Б4):
    - `deliver_pending` не берёт следующее уведомление, если с начала такта прошло ≥ 30 с (константа модуля); начатое уведомление доводится до конца; необработанные остаются `pending`;
    - HTTP-запрос не выполняется внутри открытой транзакции БД (если сейчас это не так — вынести);
    - тест: получатель отвечает 500, 5 уведомлений `pending`, паузы и монотонные часы подменены (без реального ожидания) → за такт обработано столько, сколько укладывается в 30 с, остальные `pending`; на следующих тактах все доходят до `failed`.
  - Готово: `make test` (своя БД `bm_test_b2`), сначала красная фаза новых тестов; `compose_only`-тест `test_admin_spa_served_from_image` — в журнале CI для релизного SHA (T092).
  - Backend 2, 2026-10-21: `make test` на `bm_test_b2` — 766 passed, exit 0 (`14_dev_backend.md`). Часть «`compose_only` в журнале CI для релизного SHA» подтверждается в T092.
  - Пакет 5 (5Б1): закрытие подтверждено; `compose_only`-тест прошёл в smoke CI на `34d28d0`, окончательно — журнал релизного SHA (T092).
  - Зависимости: T073, T087.
- [x] **T097 [P] [—] [backend] `IMPORT_HTTPS_PROXY_FILE`** (14_tech_rulings.md, пакет 4, 4Д4). Исполнитель — Backend 1.
  - Файлы: `bm/settings.py`, `tests/unit/test_settings.py`, `tests/security/test_no_egress.py`.
  - Работа: `import_https_proxy` добавляется в `_FILE_SECRETS` (механизм `<ИМЯ>_FILE` уже есть); `_FILE` имеет приоритет; проверка схемы `http(s)://` применяется к значению из файла; ошибка старта на русском, без значения.
  - Тесты: значение из файла даёт прокси у `anthropic` и `openai`, у `webhook` прокси нет; файл указан, но его нет → ошибка старта без значения; пароль прокси из файла не попадает в журнал.
  - Пакет 5 (5Б2): попутное исправление `SESSION_SECRET_FILE` (обязательный секрет только из файла) и перенос чтения `*_FILE` в `before`-валидатор приняты в рамках T097; в `16_release.md` — строка в «Дефекты, исправленные до релиза».
  - Готово: `make test` (своя БД `bm_test_b1`), сначала красная фаза новых тестов.
  - Зависимости: T062.
- [x] **T098 [—] [backend] Д-1: TCP_NODELAY у `api` при нескольких воркерах** (14_tech_rulings.md, пакет 5, 5Д1, 5Д2). Исполнитель — Backend 2; часть DevOps отмечена ниже.
  - Файлы: `app/backend/bm/serve.py` (новый), `tests/integration/test_keepalive_latency.py` (новый), `tests/unit/test_serve.py` (новый); DevOps — `deploy/Dockerfile` (`CMD`), `deploy/local/stack.sh`, `tests/deploy/test_compose_static.py` или отдельный статический тест Dockerfile.
  - Работа (Backend 2):
    - `bm/serve.py`: подкласс `uvicorn.protocols.http.h11_impl.H11Protocol` на уровне модуля; в `connection_made` ставит `TCP_NODELAY=1` на сокет транспорта, если сокет TCP (AF_INET/AF_INET6), на прочих ничего не делает;
    - запуск — `uvicorn.run("bm.main:create_app", factory=True, http=<класс>, workers=UVICORN_WORKERS, …)`; хост, порт и `--no-server-header` — как в текущем `CMD`; новых переменных окружения и зависимостей нет;
    - подменять `Config.bind_socket`, `Multiprocess` и патчить asyncio нельзя; если без этого не обойтись — стоп и вопрос техлиду (временная мера `UVICORN_WORKERS=1` — только решением техлида);
    - регресс-тест `test_keepalive_latency.py` (только Linux, на остальных `skipif` с причиной): запускает **ту же команду, что `CMD` образа и `stack.sh`**, с `UVICORN_WORKERS=2` на свободном порту 127.0.0.1 и своей тестовой БД; клиент — одно `http.client.HTTPConnection` без TCP_NODELAY, 1 прогревочный и 30 подряд `GET /healthz`; медиана запросов 2–31 < 10 мс. Красная фаза — тот же тест против текущей команды `uvicorn … --workers 2` (~40 мс), вывод в отчёт;
    - `test_serve.py`: `connection_made` ставит TCP_NODELAY на TCP-сокет и не падает на не-TCP.
  - Работа (DevOps): `CMD` образа и `stack.sh` запускают `api` через `python -m bm.serve`, число воркеров — из `UVICORN_WORKERS` (по умолчанию 2, как сейчас); статический тест: ни `CMD`, ни `stack.sh` не вызывают `uvicorn … --workers` напрямую.
  - Тайм-аут `budget_client` по умолчанию (50 мс) **не меняется** (FR-006, сценарий 8 US1).
  - [x] Часть Backend 2 выполнена 2026-10-21 (`14_dev_backend.md`). Красная фаза: медиана 43,95 мс; с `bm.serve` — 0,45 мс. `budget_client`, 200 решений, 2 воркера: 0 `fail_open`, p95 6,65 мс. `make test`: 797 passed, 1 skipped.
  - Готово:
    - Backend 2: вывод красной фазы регресс-теста; `make test` (своя БД `bm_test_b2`) зелёный;
    - DevOps: статический тест и `make up-local` с новым запуском (`make smoke` PASS);
    - приёмка — повтор T086 QA на локальном стеке с 2 воркерами: `make up-local && make load BM_STACK=local`, 50 rps × 600 с, без параллельных прогонов — **SC-001 PASS**: у клиента p95 ≤ 20 мс; внутри (`/metrics`) ≥ 95% решений ≤ 10 мс, p99 ≤ 25 мс; плюс 200 решений эталонным `budget_client` с тайм-аутом по умолчанию — 0 `fail_open` по тайм-ауту. Статус гейта SC-001 — PASS-замена (не эталонный стенд);
    - в CI релизного SHA регресс-тест входит в `make test`.
  - Зависимости: T087, T093.
- [x] **T099 [—] [devops] Trivy CRITICAL: `certifi` 2026.6.17 в образе** (14_tech_rulings.md, пакет 5, 5Д3, 5Д5).
  - Файлы: `deploy/Dockerfile`; при необходимости — `deploy/ci/check.sh`, статический тест Dockerfile в `tests/deploy/`.
  - Работа:
    - выяснить источник: путь файла, по которому Trivy нашёл `certifi` 2026.6.17 (`trivy image --format json` → `Results[].Target`, `Packages[].FilePath`, либо `--list-all-pkgs`), и какому пакету или стадии он принадлежит. Кандидаты [Гипотеза]: `pip` базового образа `python:3.13-slim`, который наследует стадия `python-runtime`, или другой `site-packages` базового образа. `/app/.venv` собирается `uv sync --frozen` и должен содержать 2026.10.19 — проверить. Путь и вывод — в `14_devops.md`;
    - исправить по источнику. Если это `pip` базового образа — удалить `pip` из стадии `python-runtime` (в рантайме он не нужен) и закрепить это статическим тестом. Обновление digest базового образа годится, только если проверено, что новая версия несёт исправленный `certifi`. Иной источник — исправление по месту, при сомнении — вопрос техлиду;
    - **запрещено:** `.trivyignore`, `--ignore-unfixed`, понижение порога или иное исключение для CRITICAL;
    - попутно: закрепить digest базовых образов из аннотации прогона `34d28d0` (4Д1 (2)); выписать идентификаторы advisory npm (7 moderate, `decode-uri-component` через react-admin) из `npm audit --json` в `14_devops.md` — они идут в `16_release.md` как известное ограничение (5Д5).
  - Готово: в журнале CI для релизного SHA `CHECK_STRICT=1 make check` — exit 0, Trivy `CRITICAL: 0`; в `14_devops.md` — найденный путь и причина.
  - Статус (DevOps, 2026-10-21): исправление по источнику сделано: источник — SBOM pip `pip/_vendor/bom.cdx.json` в слоях базового образа (CI `92b3414`); `python-runtime` — `FROM scratch` + копия очищенного от pip корня, guard `deploy/image_guard.py`, digest закреплены по прогону `899cf1e`, advisory npm выписаны; окончательно — по аннотациям CI релизного SHA (Trivy `CRITICAL: 0`, notice «Trivy: CRITICAL и путь certifi»). Отчёт — `14_devops.md`, раздел пакета 5.
  - Зависимости: T088, T092.
- [x] **T089 [—] [qa] Релизный прогон и гейты.**
  - Работа: `make test` (на `bm_test`, без параллельных прогонов), `make up`, `make smoke`, `make load`, `make failover`, `make check`, `make egress-host-check` (стенд QA), тест восстановления, установка по руководству. `make egress-host-check` проводится при пустом `IMPORT_HTTPS_PROXY` и `IMPORT_ENABLED=false`.
  - Статусы гейтов (14_tech_rulings.md, пакет 3, 3Д2): у каждого гейта PASS / PASS-замена (с указанием отличия) / НЕ ПРОВЕРЕНО (причина, где проверить) / FAIL. Первая строка `15_qa_report.md`: `QA: PASS` — только если все обязательные гейты конституции (приёмочные, SC-001, SC-002, SCA, SBOM) в статусе PASS; иначе `QA: CONDITIONAL` со списком; при любом FAIL — `QA: FAIL`. В этой среде `make up/smoke/load/failover` выполняются на стеке из локальных процессов (`up-local`, `BM_STACK=local`), пункты Docker — по журналу CI (T092).
  - Пакет 4 (4Р) — в список непроверенного добавляются:
    - формат Anthropic `cost_report` (единицы `amount`, `currency`, модель) — **НЕ ПРОВЕРЕНО**, нужен реальный ответ провайдера на стенде заказчика (`docs/admin-guide.md` §14, п. 10);
    - формат OpenAI `costs` (модель из `line_item`, `null`-поля) — **НЕ ПРОВЕРЕНО**, там же;
    - руководство администратора на чистой машине (T090, SC-009) — **НЕ ПРОВЕРЕНО**;
    - контейнер `backup` от root — известное ограничение v1.
  - Пакет 5 (5Р):
    - SC-001 — сейчас FAIL (Д-1); после T098 и повтора T086 — PASS-замена;
    - SCA образа (Trivy) — сейчас FAIL; PASS только по журналу CI релизного SHA после T099;
    - гейты Docker, подтверждённые на `34d28d0`, — PASS только по журналу релизного SHA; SC-002/SC-003 в Docker тогда переходят из PASS-замены в PASS;
    - `egress-host-check` — PASS только если DevOps подтвердил по релизному журналу, что проверка выполнилась;
    - npm: 7 moderate через react-admin — известное ограничение v1.
  - Результат — `15_qa_report.md` (выход QA).
  - Готово: все гейты конституции зелёные — или `QA: CONDITIONAL` с полным списком непроверенного для решения заказчика в `16_release.md`.
  - Зависимости: все задачи, не урезанные решением на контрольной точке (T080, T081 урезаны).

---

## Dependencies & Execution Order

```text
T001 ─┬─ T010,T011,T012 [P] ── T013 ─┐
      ├─ T014 ─ T015 ─┬─ T016 ─ T019 │
      │               ├─ T017 [P]    │
      │               └─ T018 [P] ───┤
      │                              ├─ T020 ─ T021 ─ T030 ─ T031 ─┬─ T032 ─ T033
      ├─ T002 [P] ─ T043 ─ T052/T064/T072 [P]                     ├─ T050 ─ T051
      │                     T052 ─ T095                            ├─ T060 ─ T061 ─ T062/T063 [P] ; T062 ─ T097
      ├─ T088 [P]                                                  ├─ ~~T080 ─ T081~~ (🔻1 урезано)
T003 [P] ─┘                                                         └─ T087 ─ T086, T090, T093, T092
T015 ─ T040 [P] ─ T041, T042 ─ T070 ─ T071 ─ T073 (🔻2, в v1) ─ T096
T096, T097 ─ T087 (журнал CI для релизного SHA) ─ T092
T093 ─ T098 ─ T086 (повтор) ; T088, T092 ─ T099 ; T098, T099 ─ релизный SHA ─ T087, T092 ─ T089
```

**Критический путь (Backend 1):** T013 → T021 → T031 → T061 → T071 → T089. Параллельно идут Backend 2 (T016–T019, T041, T042, T051, T033), frontend (T002, T043, …) и devops (T087, T088).
**После контрольной точки недели 5:** Backend 1 — T062 → T063 → T091; Backend 2 — T073; Frontend — T095; DevOps — T087 → T092 → T088 → T090; QA — T093 → T086 → T089.
**После пакета 4:** Backend 2 — T096; Backend 1 — T097, затем мутационная проверка T010–T012; DevOps — разбор журнала CI `eb17642`, правки T087/T092, правки `admin-guide.md` (T090); оркестратор — журнал `eb17642`, затем прогон CI на релизном SHA; QA — T093 → T086 → T089.
**После пакета 5:** Backend 2 — T098; DevOps — T099, затем часть T098 (`CMD`, `stack.sh`), digest образов, список advisory npm; QA — повтор T086 после T098, затем T089; оркестратор — после T098 и T099 один коммит (релизный SHA) и прогон CI на нём.

**Внутри сценария:** тест qa → реализация backend → зелёный `make test` → коммит `<название проекта>: <задача>`.

## Implementation Strategy

1. **MVP**: Phase 1–5 (US1–US3) + T033 + T087. После этого — контрольная точка недели 5, `make up && make smoke`, отказной тест.
2. **P2**: US4 → US5 (Anthropic, затем OpenAI) → US6.
3. **Stretch**: webhook, затем MCP — только по решению на контрольной точке. **Решение (2026-10-21, `14_checkpoint_week5.md`):** webhook — в v1, MCP — урезан (🔻1).
4. Каждая фаза заканчивается зелёным `make test`. Расширение объёма вне этого файла — только через техлида.
5. **Поставка в закрытой среде** (пакет 3, 3Д1–3Д2): статическая проверка compose, стек из локальных процессов и CI с журналом. Непроверенное честно помечается в релизном гейте.
