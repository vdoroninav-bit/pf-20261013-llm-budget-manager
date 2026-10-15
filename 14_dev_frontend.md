# 14. Отчёт frontend-разработчика

## 2026-10-14 — T002 «Каркас админки»: доводка

**Итог.** Тесты: 101 из 101 зелёные три прогона подряд (8 файлов). `typecheck` и `build` дают exit 0. `check-dist` не находит внешних адресов. Отметку `[x]` у T002 **не ставлю**: дословная часть критерия «`grep -rE "https?://" app/frontend/dist` пуст» невыполнима для сборки с React. Нужно решение техлида (вопрос 1).

### 1. Два падающих теста в `App.test.tsx`: ошибка в коде, а не в тесте и не нестабильность

- **Симптом.** Оба теста (форма входа и панель «Вы вошли как admin (Администратор).») падали по таймауту `findBy*`. В DOM вместо приложения была заглушка «Welcome to React-admin».
- **Причина [Факт].** `ra-core` 5.15.4 (`useConfigureAdminRouterFromChildren.getStatus`) ставит статус `empty`, если у `<Admin>` нет ни `<Resource>`, ни `<CustomRoutes>`. Тогда `CoreAdminRoutes` рисует `Ready`, не вызывает `checkAuth` и не показывает ни `loginPage`, ни `dashboard`. У `App` детей не было.
- **Почему это не нестабильность.** Падение детерминированное (3 из 3). Порядок `fetch` и таймауты ни при чём: до запроса `auth/me` дело не доходило.
- **Вторая ошибка с той же причиной.** `authProvider.login` перенаправляет на `PASSWORD_ROUTE` (`/password`, screens.md §1.2) при `must_change_password`, но маршрут не был зарегистрирован. Пользователь с временным паролем попал бы на 404.
- **Исправление.** В `App.tsx` маршрут `/password` зарегистрирован через `<CustomRoutes>`. Страница `src/pages/PasswordPage.tsx` пока содержит только заголовок «Смена пароля». Форму (текущий/новый/повтор, ошибки 422 и 401) делает T043: в её содержании указан «вход». Ресурсы меню (§4 13_design.md) добавляются в T043/T052/T064/T072.
- **TDD.** Сначала добавлен тест «маршрут смены пароля … зарегистрирован». Он был красным вместе с двумя исходными, после исправления зелёные все три.

### 2. Сборка не проходила (раньше это не было видно из-за падающих тестов)

| Проблема | Причина | Исправление |
|---|---|---|
| `tsc --noEmit`: 4 × TS2322 в `src/dataProvider.ts` | методы `DataProvider` обобщены по `RecordType`, а хелперы возвращали `RaRecord` | `fetchList`/`fetchOne`/`fetchMany` стали обобщёнными. Приведение `as R` одно, в месте, где JSON из сети становится записью. Поведение не менялось, тесты `dataProvider.test.ts` зелёные |
| `node scripts/check-dist.mjs`: `MODULE_NOT_FOUND` | шаг объявлен в `package.json`, а файл не написан (папка `scripts/` была пустой) | написан `scripts/check-dist.mjs` и тест к нему `src/__tests__/checkDist.test.ts` (5 случаев) |

### 3. «Никаких внешних URL в сборке»: что на самом деле в `dist`

`grep` находит в `dist/assets/index-*.js` 10 разных адресов [Факт]. В `index.html` и CSS адресов нет.

| Адрес | Откуда | Загружает что-то? |
|---|---|---|
| `http://www.w3.org/2000/svg`, `/1999/xlink`, `/1998/Math/MathML`, `/XML/1998/namespace` | react-dom, `createElementNS` | нет, это namespace |
| `http://localhost` | react-router, база для `new URL()` | нет |
| `https://react.dev/errors/`, `https://mui.com/production-error/?code=`, `https://mui.com/system/display/…`, `https://reactrouter.com/en/main/routers/picking-a-router` | тексты ошибок | нет |
| `https://react-admin-telemetry.marmelab.com/react-admin-telemetry?domain=` | ra-core, `new Image().src` | **да**, если не передан `disableTelemetry` |

Что сделано:
- `check-dist.mjs` разрешает в JS только эти адреса. Сравнение точное или по префиксу, другой хост с тем же началом не пройдёт. В `index.html` и CSS не разрешено ничего, включая протокол-относительные `//host`. Мутационная проверка: подложенный `fetch("https://cdn.example.com/x.js")` → exit 1.
- Тест «не отправляет телеметрию react-admin» в `App.test.tsx`. ra-core шлёт телеметрию только при `NODE_ENV=production`, поэтому тест подменяет `NODE_ENV` и глобальный `Image`. Мутационная проверка: с `disableTelemetry={false}` тест падает с адресом marmelab, с `disableTelemetry` проходит. Первая версия теста (подмена `window.Image` без `NODE_ENV`) этот мутант пропускала, она исправлена.

### Команды и вывод (сокращённо)

```
$ cd app/frontend && npx vitest run      # × 3
 Test Files  8 passed (8)
      Tests  101 passed (101)            # exit 0 во всех трёх прогонах
$ npm --prefix app/frontend run typecheck            # exit 0
$ npm --prefix app/frontend run test && npm --prefix app/frontend run build
dist/assets/index-VCqOGB1Y.js   852.89 kB │ gzip: 272.45 kB
✓ built in 913ms
check-dist: внешних адресов нет (разрешённые namespace и тексты ошибок — см. scripts/check-dist.mjs).
                                                     # exit 0
$ grep -rcE "https?://" app/frontend/dist
assets/index-*.js:5  assets/index-*.css:0  index.html:0   # строки с адресами из таблицы выше
```

Линтер не настроен: eslint/biome не установлены, скрипта `lint` нет. Тайпчек входит в `build`.

### Изменённые файлы (только `app/frontend/`)

- `src/App.tsx` — `<CustomRoutes>` с маршрутом `/password`.
- `src/pages/PasswordPage.tsx` — новый, каркас экрана 1.2.
- `src/i18n/ru.ts` — ключ `bm.password.title` («Смена пароля»).
- `src/dataProvider.ts` — типизация хелперов.
- `scripts/check-dist.mjs` — новый.
- `src/__tests__/App.test.tsx` — +2 теста (маршрут пароля, телеметрия).
- `src/__tests__/checkDist.test.ts` — новый.

### Отклонения

- Дословный критерий `grep … пуст` заменён фактической проверкой `check-dist` (шаг `build`). До решения техлида T002 не отмечен.
- `PasswordPage` — пока только заголовок, форма будет в T043. Это осознанно: без маршрута пользователь после входа с временным паролем получал бы 404.
- Размер бандла 853 кБ (272 кБ gzip), один чанк. Предупреждение подавлено `chunkSizeWarningLimit: 2000` в `vite.config.ts` (было до меня). Для интранет-админки [Вывод] это некритично. Разделение на чанки — после T043.

### Вопросы техлиду

1. Принять для T002 критерий «`npm run build` exit 0, включая `scripts/check-dist.mjs`» вместо «`grep -rE "https?://" dist` пуст»? Пустой grep недостижим без правки кода react-dom. Если принимаете — поправьте строку «Готово» в 12_tasks.md и отметьте T002, либо разрешите сделать это мне.
2. Устраивает ли, что код телеметрии остаётся в бандле и отключён флагом с тестом? Альтернатива — вырезать его на этапе сборки плагином Vite. Это правка чужого кода, хрупкая при обновлении ra-core, я бы не стал.

---

## 2026-10-15 — T043 «Экраны настройки» (US3)

**Итог:** T043 закрыта и отмечена `[x]` в `12_tasks.md`. Результат проверки:
- `npx vitest run`: 18 файлов, **189/189** тестов (было 101). Три прогона подряд зелёные.
- `tsc --noEmit`: exit 0.
- `npm run build` вместе с `check-dist`: exit 0.

Backend операций T041/T042 ещё не готов. Все экраны проверены против фейкового API, который сверяет каждый запрос и каждый ответ с `12_openapi.yaml` 1.0.2.

### Первый шаг (14_tech_rulings.md, Б1)

- Выполнен `npm run gen:api` по контракту 1.0.2. В `resources.contract.test.ts` ожидаемая версия заменена на `1.0.2`.
- Добавлен текст для нового кода `password_change_required`. Без него падал тест «у каждого кода админки из контракта есть русский текст».
- Ответ `403 password_change_required` на любой запрос ведёт на `#/password` без выхода из админки: `authProvider.checkError` бросает `{redirectTo: '/password', logoutUser: false}`. Есть юнит-тест и экранный тест.

### Что сделано

| Экран (screens.md) | Файлы | Операции |
|---|---|---|
| 1.1 Вход | `src/pages/LoginPage.tsx` | `login`, `me` |
| 1.2 Смена пароля (принудительная и из меню пользователя) | `src/pages/PasswordPage.tsx`, `authProvider.changePassword` | `changePassword` |
| 3 Подразделения: список, создание, карточка «Настройки» с лимитами, чтение для В/Ф | `src/resources/departments/` | `listDepartments`, `getDepartment`, `createDepartment`, `updateDepartment`, `putDepartmentLimits`, `listModels` |
| 3.5 Сотрудники: вкладка подразделения, карточка, личные лимиты, «Кто это?» | `src/resources/employees/` | `listEmployees`, `getEmployee`, `putEmployeeLimits`, `revealPseudonym` |
| 4 Модели: список с сортировкой, добавление с ценой, карточка, история цен, «Новая цена» | `src/resources/models/` | `listModels`, `getModel`, `createModel`, `updateModel`, `listModelPrices`, `createModelPrice` |
| 6 Загрузка файла AD: проверка → сводка → применение | `src/pages/OrgImport.tsx` | `importOrg` (multipart, `dry_run`) |
| 9 Ключи систем: список, выдача ключа с диалогом одноразового показа, изменение, отзыв | `src/resources/callers/` | `listCallers`, `createCaller`, `updateCaller`, `revokeCaller`, `listDepartments`, `listProviderSources` |
| 10 Пользователи админки, защита последнего администратора | `src/resources/adminUsers/` | `listAdminUsers`, `createAdminUser`, `updateAdminUser` |
| Меню по ролям (13_design.md §4), меню пользователя «Сменить пароль»/«Выйти» | `src/layout/BmMenu.tsx`, `BmLayout.tsx`, `App.tsx` (ресурсы по роли) | — |

Общие части:
- `src/access/permissions.ts` — права по `operationId`, копия `x-roles`. Тест сверяет таблицу с контрактом. Меню, кнопки и ресурсы решают видимость только через `can()`.
- `src/format/money.ts` — деньги и цены хранятся только строками (принцип VI). Сравнение идёт в копейках через `BigInt`, форматирование — `12 345,67 ₽` с U+202F и минусом U+2212.
- `src/format/date.ts` — даты по МСК. Ввод `datetime-local` переводится в формат `+03:00`.
- `src/components/` — `EmptyState`/`Forbidden`, `ProblemAlert` с кнопкой «Повторить», `ConfirmDialog` (у варианта danger фокус на «Отмена»), `OneTimeSecretDialog`, `LimitSetFields`, `RadioWithConfirm`/`SwitchWithConfirm`, `PriceInput`, `SlowHint` (через 10 с, константа `SLOW_REQUEST_MS`).
- `dataProvider.ts` — операции вне CRUD: `putDepartmentLimits`, `putEmployeeLimits`, `revealPseudonym`, `listModelPrices`, `createModelPrice`, `revokeCaller`, `importOrg`. Экраны вызывают их через `useDataProvider<BmDataProvider>()`, поэтому 401/403 обрабатываются так же, как в CRUD.
- `api/http.ts` — поддержка тела `FormData` для multipart: `Content-Type` выставляет сам fetch, CSRF уходит в заголовке.

Состояния на каждом экране:
- **Загрузка:** скелет react-admin и «Загрузка дольше обычного…» через 10 с.
- **Пусто:** тексты из screens.md, у администратора — с действиями.
- **Ошибка:** текст Problem и «Повторить». 404 не уводит в список, на месте показывается «Запись не найдена…».
- **Нет прав (403):** «Этот раздел недоступен для вашей роли…».
- **422:** ошибки показываются под полями, а `409`, `fx_rate_missing`, `last_admin`, `file_too_large` — своими текстами.

Доступность:
- у полей есть подписи, у переключателей — `fieldset`/`legend`;
- `aria-live="polite"` у сводки AD-файла и у раскрытого псевдонима;
- машинные идентификаторы выводятся моноширинным шрифтом с `lang="en" translate="no"`;
- после отказа при входе фокус переходит в поле «Пароль», после ошибки валидации — в первое ошибочное поле.

Изменения формы в карточках уходят PATCH-запросом, только изменённые поля (`changedFields`), `mutationMode="pessimistic"`. Лимиты отправляются PUT-запросом всеми тремя полями: пустое поле — `null`.

### Как проверяются тесты «отправляет запрос по контракту»

`src/__tests__/support/contractApi.ts` — фейковый `fetch`, ответы задаются по `operationId`. На каждый запрос он проверяет:
- путь и метод — это операция контракта, роль сессии входит в её `x-roles`;
- query-параметры только объявленные, обязательные присутствуют;
- тело JSON соответствует схеме `requestBody` (мини-валидатор `schemaCheck.ts`: `$ref`, `type`, `enum`, `pattern`, `required`, `additionalProperties: false`, `oneOf`, `allOf`…);
- в multipart только объявленные поля;
- на изменяющих запросах есть `X-CSRF-Token`;
- **ответ фейка** тоже проверяется: статус объявлен, тело соответствует схеме. Так моки не расходятся с API.

Каждый экранный тест заканчивается проверкой `expect(api.violations).toEqual([])`. Сам фейк проверен тестом `contractApi.test.ts`: лишнее поле, неверный `Money`, отсутствие CSRF, роль вне `x-roles`, необъявленный статус ответа.

Мутационная проверка (вручную, изменения откатаны):
- `parseMoneyInput` без дополнения до 2 знаков: ввод `"30000.5"` → 1 тест красный (`putDepartmentLimits запрос: $.day … не подходит под pattern`);
- убран заголовок CSRF в `http.ts` → 3 теста callers красные.

### Команды и вывод (сокращённо)

```
$ cd app/frontend && npm run gen:api
🚀 ../../12_openapi.yaml → src/api/schema.d.ts
$ npx vitest run            # до реализации: 9 новых файлов красные (нет модулей), 1 тест i18n красный (password_change_required)
$ npx tsc -p tsconfig.json --noEmit
tsc exit 0
$ npm --prefix app/frontend run test
 Test Files  18 passed (18)
      Tests  189 passed (189)
exit 0
$ npm run build
dist/assets/index-*.js   1,172.02 kB │ gzip: 361.85 kB
check-dist: внешних адресов нет (разрешённые namespace и тексты ошибок — см. scripts/check-dist.mjs).
build exit 0
```

### Отклонения

1. **MSW не используется.** В T043 написано «API мокается через MSW». Вместо него — `contractApi`, как и поручено оркестратором (fakeFetch в тестах). Для MSW нужна новая зависимость, лицензию которой надо проверять (T088), а он не проверял бы контракт. `contractApi` сверяет и запросы, и ответы с `12_openapi.yaml`.
2. **Проверки axe нет.** 13_design.md §8 упоминает axe в тестах T043/T072, но пакета в зависимостях нет, а ставить новые зависимости без проверки лицензий я не стал. Доступность проверена запросами по ролям и подписям (`getByRole`/`getByLabelText`), фокусом и `aria-live` в тестах. Если axe нужен — прошу решение техлида по зависимости `axe-core` (MPL-2.0).
3. **Имя ресурса `adminUsers` → `admin-users`** в `api/resources.ts`: имя ресурса react-admin совпадает с маршрутом, а маршрут по screens.md §10 — `/admin/#/admin-users`. Папка `src/resources/adminUsers/` осталась, как в T043. [Вывод] Для T052 то же нужно с `fxRates` → `fx-rates`.
4. **Ресурс `providerSources`** зарегистрирован без экранов только у администратора. Он нужен для выбора «Источник для сверки» в форме ключа. Экраны источников — T064.
5. **Ссылка «Ввести курс»** (`fx_rate_missing`) ведёт на `#/fx-rates`. Маршрута пока нет, он появится в T052.
6. **`getPermissions` без сессии возвращает `null`, а не ошибку.** Иначе react-admin с ресурсами по роли ждал три повтора запроса прав (около 7 с), прежде чем показать вход. `App.test.tsx`: фейку в двух тестах дано 10 ответов 401 вместо 3.
7. **Неверный текущий пароль** распознаётся по коду `invalid_credentials` при любом статусе. Сервер отвечает `422` (отклонение backend 5, принято), в screens.md §1.2 указан `401`.
8. Новое подразделение создаётся без поля `subs_warn_pct` (его нет в `DepartmentCreate`). Порог меняется в карточке после создания.
9. Бандл вырос до 1 172 кБ (362 кБ gzip), чанк один. [Вывод] Для интранет-админки некритично. Разделение по экранам (`React.lazy`) — после T052/T064/T072, когда экраны будут все.

### Изменённые и новые файлы (только `app/frontend/`)

- Новые:
  - `src/access/permissions.ts`
  - `src/format/{money,date}.ts`
  - `src/components/{common,inputs,records,LimitSetFields,OneTimeSecretDialog}.tsx`
  - `src/resources/{departments,employees,models,callers,adminUsers}/index.tsx`
  - `src/pages/OrgImport.tsx`
  - `src/layout/BmMenu.tsx`
- Тесты:
  - `src/__tests__/{access,auth.screens,departments,employees,models,callers,adminUsers,orgImport}.test.tsx`
  - `src/__tests__/{money,contractApi}.test.ts`
  - `src/__tests__/support/{contractApi,schemaCheck,fixtures,renderApp}.ts(x)`
- Изменены:
  - `src/App.tsx`, `src/authProvider.ts`, `src/dataProvider.ts`, `src/api/http.ts`, `src/api/resources.ts`, `src/api/schema.d.ts` (gen:api)
  - `src/i18n/ru.ts`
  - `src/pages/{LoginPage,PasswordPage}.tsx`, `src/layout/BmLayout.tsx`
  - тесты `App.test.tsx`, `authProvider.test.ts`, `resources.contract.test.ts`

### Вопросы техлиду

1. Нужен ли axe (`axe-core`, MPL-2.0 — в белом списке) как dev-зависимость для автоматической проверки контраста и ARIA? 13_design.md §8 на него ссылается. Если да, добавлю в T072 вместе с проверкой панели.
2. Подтвердите переименование ресурса `adminUsers` → `admin-users` (имя ресурса = маршрут из screens.md). Тогда в T052 так же сделаю `fxRates` → `fx-rates`.
3. Для backend (T042), к сведению: экраны рассчитывают на `413 file_too_large` и `422` без `detail` у `importOrg`, на `409 last_admin` у `updateAdminUser`, на `422 invalid_credentials` у `changePassword`, а также на то, что `revealPseudonym` отвечает `EmployeeIdentity` с `display_name` или `null`. Всё это есть в контракте 1.0.2, правок контракта не требуется.
