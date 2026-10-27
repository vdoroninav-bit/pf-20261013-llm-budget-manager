# Linux AppImage

Один файл `LLM_Budget_Manager-<версия>-x86_64.AppImage` (~45 МБ) запускает приложение без Docker. Для пилота, демонстрации и одного сервера. Руководство пользователя — `docs/admin-guide.md`, раздел 3а.

- Сборка: `make appimage` → `dist/` (файл и `.sha256`). CI: `.github/workflows/appimage.yml` (вручную или по тегу `v*`, артефакт прогона).
- Совместимость: Linux x86_64, glibc ≥ 2.28 (колёса зависимостей manylinux_2_28; Python и PostgreSQL — manylinux2014).
- Проверено 2026-10-27 на Ubuntu 24.04 (без FUSE, `APPIMAGE_EXTRACT_AND_RUN=1`, обычный пользователь): первый запуск, `make smoke` в режиме `BM_STACK=local` — 4 passed, 6 skipped (`compose_only`), админка `/admin/` открывается, остановка по SIGTERM, `backup`, повторный запуск на тех же данных.

## Что внутри

| Каталог AppDir | Что | Источник | Лицензия |
|---|---|---|---|
| `usr/python` | CPython 3.13.14 + пакет `bm` и зависимости из `uv.lock` (без dev) | python-build-standalone (через `uv python install`), PyPI | PSF; лицензии зависимостей — как в Docker-поставке (SBOM `make check`) |
| `usr/pg` | PostgreSQL 16.2 (initdb, postgres, pg_ctl, psql, pg_dump…) | колесо `pgserver==0.1.4` (sha256 закреплён в `build.sh`) | PostgreSQL License; обвязка pgserver — Apache 2.0 (`usr/pg/pgserver-0.1.4.dist-info/LICENSE`) |
| `usr/share/llm-budget/admin` | сборка админки (`npm run build`) | `app/frontend` | как у фронтенда |
| `usr/lib/llm-budget/bm_appimage` | обёртка запуска api: каталог админки из AppImage | этот каталог | проектная |
| `AppRun` | супервизор: секреты, initdb, postgres + api + worker, остановка | этот каталог | проектная |

Инструмент сборки — appimagetool 1.9.0 (MIT), sha256 закреплён в `build.sh`; в файл попадает только его runtime (MIT).

## Отличия от Docker-поставки

- Код приложения не менялся. Админку из каталога внутри AppImage отдаёт обёртка `bm_appimage.factory`: она передаёт `create_app(admin_dir=…)`, а константа `ADMIN_STATIC_DIR` (4Д6) осталась прежней.
- PostgreSQL 16, а не 18. На 16 проходят все локальные тесты. Миграции те же.
- Пароль БД — случайный, хранится в `config.env` (права 600). PostgreSQL слушает только 127.0.0.1, unix-сокета нет.
- Нет контейнера резервного копирования по расписанию: копия делается командой `backup`. Процессы сами не перезапускаются: если упал любой процесс, останавливается всё.
- Запуск от root запрещён (PostgreSQL не работает под root).
