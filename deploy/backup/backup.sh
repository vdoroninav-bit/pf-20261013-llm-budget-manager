#!/usr/bin/env bash
# Резервная копия БД (FR-037, T087): pg_dump -Fc в BACKUP_DIR, хранится BACKUP_KEEP последних копий.
#
#   backup.sh               одна копия сейчас (make backup)
#   backup.sh --loop        копия при старте и далее раз в сутки (контейнер backup в compose)
#   backup.sh --healthcheck exit 0, если последняя успешная копия не старше 26 ч и БД отвечает
#
# Подключение — стандартные переменные libpq: PGHOST, PGPORT, PGUSER, PGDATABASE; пароль — PGPASSWORD или файл
# POSTGRES_PASSWORD_FILE (секрет compose). Одни и те же скрипты работают в контейнере backup и на стеке из
# локальных процессов (deploy/local/stack.sh).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
INTERVAL_S=86400
STALE_MIN=$((26 * 60))
: "${PGDATABASE:=bm}"
export PGDATABASE

if [[ -z "${PGPASSWORD:-}" && -n "${POSTGRES_PASSWORD_FILE:-}" && -s "${POSTGRES_PASSWORD_FILE}" ]]; then
  PGPASSWORD="$(<"$POSTGRES_PASSWORD_FILE")"
  export PGPASSWORD
fi

log() { printf '%s backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

once() {
  mkdir -p "$BACKUP_DIR"
  local name tmp
  name="bm-$(date -u +%Y%m%dT%H%M%SZ).dump"
  tmp="$BACKUP_DIR/.${name}.part"
  # Пишем во временный файл и переименовываем: в каталоге не бывает недописанных копий.
  pg_dump -Fc --no-password -f "$tmp" "$PGDATABASE"
  pg_restore --list "$tmp" >/dev/null   # копия читается — иначе ошибка
  mv "$tmp" "$BACKUP_DIR/$name"
  touch "$BACKUP_DIR/.last_ok"
  log "OK $BACKUP_DIR/$name ($(stat -c %s "$BACKUP_DIR/$name") байт)"
  # Ротация: оставляем BACKUP_KEEP последних (имена сортируются по времени).
  local -a all
  mapfile -t all < <(find "$BACKUP_DIR" -maxdepth 1 -name 'bm-*.dump' -printf '%f\n' | sort)
  local extra=$(( ${#all[@]} - BACKUP_KEEP ))
  if (( extra > 0 )); then
    for f in "${all[@]:0:extra}"; do rm -f -- "$BACKUP_DIR/$f"; log "удалена старая копия $f"; done
  fi
}

case "${1:-}" in
  "")
    once ;;
  --loop)
    trap 'log "остановка"; exit 0' TERM INT
    while true; do
      once || log "ОШИБКА: копия не создана, повтор через час"
      # sleep в фоне + wait: SIGTERM от docker stop обрабатывается сразу.
      if [[ -f "$BACKUP_DIR/.last_ok" ]] && [[ -n "$(find "$BACKUP_DIR/.last_ok" -mmin -60)" ]]; then
        sleep "$INTERVAL_S" & wait $!
      else
        sleep 3600 & wait $!
      fi
    done ;;
  --healthcheck)
    pg_isready -q
    [[ -n "$(find "$BACKUP_DIR/.last_ok" -mmin -"$STALE_MIN" 2>/dev/null)" ]] ;;
  *)
    echo "использование: $0 [--loop|--healthcheck]" >&2; exit 2 ;;
esac
