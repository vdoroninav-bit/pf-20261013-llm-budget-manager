# Mac-приложение (Apple Silicon)

`LLM_Budget_Manager-<версия>-macos-arm64.zip` (~23 МБ) содержит «LLM Budget Manager.app». Приложение запускается без Docker, для пилота и демонстрации на Mac с чипом M1 и новее, macOS 11+. Руководство пользователя — `docs/admin-guide.md`, раздел 3б.

- Сборка: `make macos-app` → `dist/` (zip и `.sha256`). Собирается на Linux: используются готовые бинарники под macOS, ничего не компилируется.
- CI: `.github/workflows/macos-app.yml` (вручную или по тегу `v*`). Сборка идёт на Ubuntu, проверка — на настоящем Mac arm64 (`macos-14`): распаковка через `ditto`, `codesign --verify` всех встроенных бинарников, запуск без окна, smoke, остановка.

## Как устроено

| Где в .app | Что | Источник | Лицензия |
|---|---|---|---|
| `Contents/MacOS/LLM Budget Manager` | sh: запуск встроенного Python с `launcher.py` (`-I`) | этот каталог | проектная |
| `Resources/launcher.py` | супервизор: секреты, initdb, postgres + api + worker, окно «работает / остановить» (osascript), открытие админки | этот каталог | проектная |
| `Resources/python` | CPython 3.13.14, aarch64-apple-darwin, install_only_stripped, подпись ad-hoc от издателя; без tkinter, test, pip и libpython.dylib | python-build-standalone 20260718 (sha256 в `build.sh`) | PSF |
| `Resources/site-packages` | `bm` и зависимости из `uv.lock` (колёса macosx arm64) | PyPI | как в Docker-поставке |
| `Resources/pg` | PostgreSQL 16.2: postgres, initdb, pg_ctl, psql, createdb, pg_dump, pg_restore, pg_isready | колесо `pgserver==0.1.4` macosx_11_0_arm64 (sha256 в `build.sh`) | PostgreSQL License; pgserver — Apache 2.0 |
| `Resources/admin` | сборка админки | `app/frontend` | как у фронтенда |
| `Resources/lib/bm_appimage` | обёртка api с каталогом админки (общая с AppImage) | `deploy/appimage` | проектная |

Данные хранятся в `~/Library/Application Support/LLM Budget Manager`: `config.env` (права 600), `pgdata`, `logs`. Другой каталог задаётся переменной `LLM_BUDGET_HOME`. PostgreSQL слушает только 127.0.0.1 (порт `PG_PORT`, по умолчанию 55432), unix-сокета нет.

## Ограничения

- **Приложение не нотаризовано**: нет Apple Developer ID. Если zip скачан из браузера, при первом запуске нужно нажать на приложение в Finder правой кнопкой → «Открыть». Если macOS всё равно не пускает: «Системные настройки → Конфиденциальность и безопасность → Всё равно открыть». Для массовой раздачи нужны подпись Developer ID и нотаризация, это задача на будущее.
- Отличия от Docker-поставки — те же, что у AppImage: PostgreSQL 16, резервная копия не по расписанию, при падении процесса останавливается всё.
