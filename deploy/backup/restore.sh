#!/usr/bin/env bash
# Восстановление БД из копии pg_dump -Fc (FR-037, T087) — в ЧИСТУЮ БД: база PGDATABASE удаляется и создаётся заново.
#
#   restore.sh                    последняя копия из BACKUP_DIR
#   restore.sh <файл.dump>        указанная копия (имя в BACKUP_DIR или путь)
#
# Перед запуском api и worker должны быть остановлены (make restore делает это сам): иначе DROP DATABASE
# завершит их соединения принудительно. Подключение — PGHOST, PGPORT, PGUSER (владелец БД с правом CREATEDB),
# пароль — PGPASSWORD или POSTGRES_PASSWORD_FILE.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
: "${PGDATABASE:=bm}"
DB="$PGDATABASE"
OWNER="${PGUSER:-bm}"

if [[ -z "${PGPASSWORD:-}" && -n "${POSTGRES_PASSWORD_FILE:-}" && -s "${POSTGRES_PASSWORD_FILE}" ]]; then
  PGPASSWORD="$(<"$POSTGRES_PASSWORD_FILE")"
  export PGPASSWORD
fi

log() { printf '%s restore: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

src="${1:-}"
if [[ -z "$src" ]]; then
  src="$(find "$BACKUP_DIR" -maxdepth 1 -name 'bm-*.dump' -printf '%f\n' | sort | tail -n 1)"
  [[ -n "$src" ]] || { log "ОШИБКА: в $BACKUP_DIR нет копий bm-*.dump (make backup)"; exit 2; }
fi
[[ -f "$src" ]] || src="$BACKUP_DIR/$src"
[[ -f "$src" ]] || { log "ОШИБКА: нет файла $src"; exit 2; }

pg_restore --list "$src" >/dev/null || { log "ОШИБКА: $src не читается pg_restore"; exit 1; }
log "копия $src"

# Служебная БД для DROP/CREATE: postgres (сама восстанавливаемая БД будет удалена).
psql -X -v ON_ERROR_STOP=1 --no-password -d postgres -q <<SQL
DROP DATABASE IF EXISTS "$DB" WITH (FORCE);
CREATE DATABASE "$DB" OWNER "$OWNER" TEMPLATE template0 ENCODING 'UTF8';
SQL
log "БД $DB пересоздана (чистая)"

pg_restore --no-password --exit-on-error --no-owner --role="$OWNER" -d "$DB" "$src"
tables="$(psql -X -At --no-password -d "$DB" -c "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"
log "OK: восстановлено в $DB, таблиц в public: $tables"
