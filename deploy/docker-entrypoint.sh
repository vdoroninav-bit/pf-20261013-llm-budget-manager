#!/bin/sh
# Entrypoint образа api/worker (T087). Подготовка окружения процесса, затем exec команды.
#
# 1. Секреты *_FILE. docker compose кладёт значения из deploy/.env файлами в /run/secrets/. Если значение в .env
#    пустое, compose файл не создаёт [проверено: Docker 29.8 / Compose 5.5]. Пустой необязательный секрет
#    (ключи провайдеров при IMPORT_ENABLED=false, прокси импорта при прямом выходе) — это «не задано»: переменную *_FILE снимаем, иначе
#    приложение упало бы на чтении несуществующего файла. Обязательные секреты (SESSION_SECRET) при этом
#    по-прежнему валят старт с понятной ошибкой настроек — так задумано.
# 2. Пароль PostgreSQL: из POSTGRES_PASSWORD_FILE в PGPASSWORD. DATABASE_URL — без пароля, драйвер asyncpg
#    берёт его из PGPASSWORD. Пароль не попадает ни в DATABASE_URL, ни в `docker inspect`.
# 3. PROMETHEUS_MULTIPROC_DIR (только api): каталог на tmpfs очищается при старте (14_tech_rulings.md, Б6).
set -eu

for var in SESSION_SECRET_FILE ADMIN_INITIAL_PASSWORD_FILE ANTHROPIC_ADMIN_KEY_FILE OPENAI_ADMIN_KEY_FILE \
           IMPORT_HTTPS_PROXY_FILE; do
  eval "path=\${$var:-}"
  if [ -n "$path" ] && [ ! -s "$path" ]; then
    unset "$var"
  fi
done

if [ -n "${POSTGRES_PASSWORD_FILE:-}" ] && [ -s "$POSTGRES_PASSWORD_FILE" ]; then
  PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")"
  export PGPASSWORD
fi

if [ -n "${PROMETHEUS_MULTIPROC_DIR:-}" ]; then
  mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
  find "$PROMETHEUS_MULTIPROC_DIR" -mindepth 1 -delete
fi

exec "$@"
