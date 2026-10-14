# 14. Отчёт backend-разработчика

## 2026-10-14 — Phase 2: T015, T016, T017, T018, T019

### Итог

| Задача | Статус | Ключевые файлы |
|---|---|---|
| T015 Каркас приложения и контрактный тест | готово | `bm/settings.py`, `bm/main.py`, `bm/http/errors.py`, `bm/http/deps.py`, `bm/http/routers/system.py`, `bm/metrics.py`, `bm/db/pool.py`, `tests/contract/test_openapi_contract.py`, `tests/integration/test_system.py`, `tests/unit/test_settings.py` |
| T016 Вход, роли, аудит | готово | `bm/access/{auth,permissions,authorize,audit}.py`, `bm/http/routers/auth.py`, `bm/http/schemas.py`, `tests/unit/test_permissions.py`, `tests/acceptance/test_auth.py` |
| T017 Egress и «0 исходящих» | готово | `bm/egress/client.py`, `app/backend/importlinter.ini`, `tests/security/test_no_egress.py` (страж `socket.connect` в `tests/conftest.py` уже был) |
| T018 Снимок конфигурации | готово | `bm/budget/snapshot.py`, `bm/config/service.py`, `tests/integration/test_snapshot.py` |
| T019 Ядро уведомлений | готово | `bm/notify/{service,channels}.py`, `bm/http/routers/notifications.py`, `tests/integration/test_notify_once.py` |

Порядок работы: T015 → T016 → T019 → T017 → T018. По каждой задаче сначала писался тест и подтверждалось его падение (ImportError или отсутствие маршрута), затем код.

### Проверка (команды готовности)

```
$ DATABASE_URL=postgresql://bm:bm@127.0.0.1:5432/bm_test SKIP_FRONTEND=1 make test
БД доступна: PostgreSQL 16.x  (предупреждение: целевая версия — 18)
======================= 240 passed, 50 xfailed in 35.05s =======================
сетевые библиотеки (httpx, urllib, http, socket) — только в bm.egress KEPT (3 ignored imports)
bm.domain не зависит от ввода-вывода и других модулей bm KEPT
ядро не импортирует адаптеры (http, mcp, worker) KEPT
Contracts: 3 kept, 0 broken.
run-tests.sh full: OK                                   → exit 0

$ make lint
Contracts: 3 kept, 0 broken.
ruff check: All checks passed!
ruff format --check: 62 files already formatted         → exit 0
```

Было 136 тестов, стало 240 (+104). 50 xfailed — это операции контракта, роутеры которых появятся позже (`PENDING` в контрактном тесте, `xfail(strict=True)`).

Контракт import-linter проверен на нарушение: временный `import urllib.request` в `bm.access.audit` дал BROKEN с цепочками `bm.http → bm.access.audit → urllib` и `bm.main → …`. Импорт `bm.egress` из `bm.notify` проходит, как и задумано.

### Что сделано и как устроено

**T015.**
- `create_app(settings=None, clock=None)`: без аргументов настройки читаются из окружения. Это совместимо с `uvicorn bm.main:create_app --factory` в `deploy/Dockerfile`.
- Lifespan создаёт пул asyncpg, применяет миграции (под advisory-блокировкой из T014) и создаёт первого администратора.
- Swagger UI, ReDoc и `/openapi.json` выключены: UI грузит скрипты с CDN (принцип IV). Источник истины — `12_openapi.yaml`.
- Ошибки отдаются как `application/problem+json` с `detail` на русском:
  - 422 перечисляет только имена полей, тело запроса не повторяется (принцип I);
  - 500 отдаётся без подробностей;
  - 404 и 405 тоже идут в формате Problem.
- `/readyz` отвечает 503, если БД недоступна или не применена последняя миграция из образа.
- `/metrics` использует `text/plain; version=0.0.4` по контракту. В prometheus-client 0.26 тип по умолчанию — 1.0.0, поэтому `CONTENT_TYPE_PLAIN_0_0_4` задан явно. В выводе есть все 6 метрик из описания операции.
- Контрактный тест:
  - (а) спецификация валидна;
  - (б) хелпер ловит необъявленный статус, тело вне схемы и чужую операцию;
  - (в) каждая `operationId` сверяется по методу и пути, а в приложении нет операций сверх контракта.
- Операции берутся из `app.openapi()`. В FastAPI 0.143 `include_router` ленивый, и `app.routes` вложенных маршрутов не видит.
- Исправлен `tests/contract/helpers.py`: openapi-core не разбирал `application/problem+json`, и проверка любого ответа-ошибки падала. Добавлен десериализатор через `Config(extra_media_type_deserializers=…)`.

**T016.**
- Пароли хранятся в argon2id с перехэшированием при входе, если изменились параметры.
- Сессия:
  - токен случайный, в cookie `bm_session` с флагами `HttpOnly; SameSite=Strict; Path=/`, срок жизни 12 ч;
  - в БД лежит HMAC-SHA256(SESSION_SECRET, токен), а не сам токен;
  - CSRF-токен возвращают `login` и `me`, он сверяется на POST/PUT/PATCH/DELETE.
- Блокировка входа:
  - 10 неудач подряд закрывают вход на 15 минут (`429 login_locked` с `Retry-After`), даже с верным паролем;
  - успешный вход сбрасывает счётчик;
  - счётчик хранится в той же транзакции под `FOR UPDATE`;
  - для неизвестного логина выполняется фиктивная проверка хэша, чтобы время ответа не выдавало, есть ли такой логин.
- Первый администратор создаётся из `ADMIN_INITIAL_*` с `must_change_password=true`, только если пользователей нет. Создание идёт под advisory-блокировкой, потому что воркеров uvicorn два. Если пользователей нет и пароль не задан, сервис не стартует.
- `PERMISSIONS` — единственная таблица прав. Тест сверяет её с `x-roles` всех операций контракта.
- Общая зависимость `AdminSession` берёт `operationId` маршрута и проверяет:
  1. сессию;
  2. CSRF;
  3. `must_change_password` (до смены пароля доступны только `me`, `logout`, `changePassword`);
  4. роль.
- Отказ по роли — 403 и запись в аудит `forbidden_access` (actor, `operation`, operationId, метод и путь). Для объектов чужого подразделения есть `authorize_object(...)`.
- Аудит пишется для `login`, `login_locked`, `change_password` и создания первого администратора.

**T017.**
- `get_client(purpose, settings, *, transport=None)`:
  - `anthropic` и `openai` работают только при `IMPORT_ENABLED=true`, иначе `EgressDisabled`; только HTTPS и только свой хост;
  - `webhook` разрешён только на 10/8, 172.16/12, 192.168/16, 127/8 (имя хоста резолвится, все адреса должны быть частными) и только на свой хост и порт;
  - редиректы выключены, прокси из окружения игнорируются (`trust_env=False`);
  - параметр `transport` нужен для тестов коннекторов (T062, T063): MockTransport с фикстурами.
- В import-linter запрещены `httpx`, `urllib`, `http`, `socket` во всех пакетах `bm`, кроме `bm.egress`, включая `bm.main`, `bm.settings`, `bm.metrics`, `bm.clock`. Косвенные цепочки тоже проверяются, исключены только импорты внутри `bm.egress.client`.

**T018.**
- `ConfigSnapshot` содержит:
  - подразделения с разрешёнными моделями;
  - сотрудников с назначением на сегодня по МСК и всю историю назначений (для подписок по отрезкам);
  - лимиты;
  - каталог с текущей ценой (модели без действующей цены в каталог не попадают);
  - курсы;
  - ключи по SHA-256, включая отозванные с `active=False` — чтобы T021 отвечал 401;
  - неудалённые подписки.
- `load(conn, now)` читает снимок одной транзакцией REPEATABLE READ (если транзакции ещё нет), версию — первой.
- `SnapshotCache.ensure_fresh(conn, now)` делает ровно один запрос к `config_version` (тест считает запросы). Снимок перечитывается, если:
  - сменилась версия;
  - наступили новые сутки МСК;
  - вступила в силу цена с будущим `valid_from` (`valid_until`).
- `config.apply(conn, Change(action, object_type, run), actor)`: изменение, аудит и `config_version += 1` выполняются в одной транзакции. Исключение откатывает всё, вложенный вызов откатывается вместе с внешней транзакцией.

**T019.**
- `create_once(conn, kind, department_id, period, period_start, message) -> bool` работает через `ON CONFLICT ON CONSTRAINT notification_once DO NOTHING`. Тест: 8 параллельных соединений, создаёт ровно одно.
- `InApp` — канал по умолчанию: уведомление и есть строка в ленте.
- `listNotifications`:
  - admin видит всё, budget_owner — только своё подразделение (уведомления без подразделения ему не видны);
  - finance_viewer получает 403 с записью в аудит;
  - работают фильтр `unread` и пагинация до 200.
- `markNotificationRead`: «прочитано» хранится отдельно для каждого пользователя (`read_by`), повтор тоже отвечает 204. Чужое уведомление — 403 и аудит, несуществующее — 404.

### Отклонения от плана

1. **`get_client(purpose, settings, *, transport=None)`** вместо `get_client(purpose)` из `12_plan.md` §6. Настройки передаются явно, а не читаются из глобального состояния. `transport` нужен для тестов без сети.
2. **import-linter запрещает `urllib` и `http` целиком.** Подмодули внешних пакетов (`http.client`) import-linter запретить не может (ошибка «subpackages of external packages are not valid»). Следствие: в `bm` вне `egress` нельзя `urllib.parse` и `http.HTTPStatus`. Заголовки Problem взяты из своей таблицы в `errors.py`.
3. **`app/backend/pyproject.toml`: в per-file-ignores для `tests/**` добавлены `E501` и `S107`.** До моих задач `make lint` падал на 24 ошибках в уже существующих файлах T010–T014: длинные SQL и assert в тестах, пароль по умолчанию в `seed.admin_user`. Код приложения правился вручную, E501 для него остаётся в силе. Также прогнан `ruff format`: переформатированы 25 файлов, в том числе тесты T010–T014. Логика не менялась.
4. **Код ошибки `password_change_required`** (403) вне списка примеров `Problem.code`. Список в контракте открытый (`examples`), но фронтенду стоит о нём знать: на этот код показывать экран смены пароля.
5. **Неверный старый пароль в `changePassword` возвращает `422 invalid_credentials`, а не 401.** На 401 SPA посчитала бы, что сессия истекла, и выкинула пользователя.

### Вопросы техлиду

1. **Пробелы в контракте (менять его может только архитектор).** Сервер отдаёт статусы, которые в контракте не объявлены:
   - `logout`: 401 (нет сессии) и 403 (CSRF);
   - `changePassword`: 403 (CSRF);
   - `listNotifications`: 401 и 403 (finance_viewer по `x-roles`);
   - `markNotificationRead`: 401.
   Предлагаю объявить у всех операций с `security: session` ответы `401 Unauthorized` и `403 Forbidden`. Тогда `assert_matches_contract` можно применять и к отказным ответам. Сейчас в тестах такие ответы проверяются без хелпера.
2. **Флаг `Secure` у cookie.** Сейчас он не ставится: по умолчанию API слушает `127.0.0.1:8080` по HTTP. Если у заказчика будет HTTPS на обратном прокси, нужна переменная вроде `SESSION_COOKIE_SECURE=true`. Добавить её в `.env.example` (зона DevOps)?
3. **Срок сессии 12 ч** зашит константой (`SESSION_TTL`). В спецификации значения нет. Нужна ли переменная окружения?
4. **Прокси для коннекторов.** `trust_env=False` не даёт трафику уйти через прокси из окружения. Если в контуре заказчика к `api.anthropic.com` можно выйти только через корпоративный прокси, понадобится явная настройка `IMPORT_HTTPS_PROXY`. Решение за техлидом и архитектором.
5. **Ключ уникальности уведомлений с NULL** (`NULLS NOT DISTINCT`). Для уведомлений без периода (`import_failure`, `fx_missing`) вызывающий код T061/T071 должен передавать `period_start` (например, дату сбоя), иначе такое уведомление придёт один раз за всё время. Это описано в docstring `bm/notify/service.py`.
6. **Метрики при `UVICORN_WORKERS=2`** считаются в каждом процессе отдельно. Для `make load` (T086) понадобится multiprocess-режим prometheus-client или сбор по воркерам.

### Известные ограничения

- Хост webhook проверяется при создании клиента. Повторный резолв при соединении (DNS rebinding) не контролируется. Рекомендация для `admin-guide`: указывать IP-адрес.
- JSON-логи пока не настроены. Обработчик 500 пишет в журнал только класс ошибки, путь и метод. Общая настройка логирования — вместе с worker (T031).
