#!/usr/bin/env bash
# Стек из локальных процессов (14_tech_rulings.md, пакет 3, 3Д1(в); T087) — замена docker compose там, где
# образы собрать нельзя. Те же процессы, что в контейнерах: свой кластер PostgreSQL, api (python -m bm.serve, 2 воркера),
# worker. Используется make up-local / down-local, make smoke|backup|restore|failover|load с BM_STACK=local.
#
#   stack.sh up                  поднять всё (initdb при первом запуске) и дождаться /readyz
#   stack.sh down                остановить всё (данные кластера сохраняются)
#   stack.sh destroy             остановить и удалить каталог состояния (кластер, копии, журналы)
#   stack.sh status              состояние сервисов
#   stack.sh start|stop|kill <postgres|api|worker>
#        stop  = SIGTERM главному процессу, ожидание до 10 с, затем SIGKILL всей группе (как docker stop)
#        kill  = SIGKILL всей группе процессов сервиса (как docker kill)
#   stack.sh wait                дождаться /readyz (до 120 с)
#   stack.sh env                 строки export для тестов: BASE_URL, DATABASE_URL, BM_ADMIN_LOGIN/PASSWORD, ...
#   stack.sh backup              копия pg_dump -Fc (deploy/backup/backup.sh) в каталог состояния
#   stack.sh restore [файл]      остановить api/worker, восстановить в чистую БД, запустить, дождаться /readyz
#   stack.sh logs <сервис>       хвост журнала сервиса
#
# ВАЖНО: общий кластер PostgreSQL на 5432 (bm_test_*) этот скрипт не трогает никогда. Свой кластер — на
# BM_LOCAL_PG_PORT (по умолчанию 55432), данные — в BM_LOCAL_STATE (по умолчанию deploy/local/.state, в .gitignore).
#
# Переменные: BM_LOCAL_STATE, BM_LOCAL_PG_PORT, BM_LOCAL_API_PORT (18080), BM_LOCAL_PG_BIN (каталог initdb/pg_ctl),
#             UVICORN_WORKERS (2). Отличия от compose — в smoke помечены compose_only (SPA из образа, uid 10001,
#             HEALTHCHECK, сети compose).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE="${BM_LOCAL_STATE:-$ROOT/deploy/local/.state}"
PG_PORT="${BM_LOCAL_PG_PORT:-55432}"
API_PORT="${BM_LOCAL_API_PORT:-18080}"
WORKERS="${UVICORN_WORKERS:-2}"
PGDATA="$STATE/pgdata"
RUN="$STATE/run"
LOGS="$STATE/logs"
ENV_FILE="$STATE/stack.env"
BACKUP_DIR="$STATE/backups"
VENV="$ROOT/app/backend/.venv"
SERVICES=(postgres api worker)

die() { echo "stack.sh: $*" >&2; exit 2; }
log() { echo "stack.sh: $*"; }

if [[ "$PG_PORT" == "5432" ]]; then
  die "BM_LOCAL_PG_PORT=5432 запрещён: это общий кластер других исполнителей"
fi

pg_bin() {
  if [[ -n "${BM_LOCAL_PG_BIN:-}" ]]; then echo "$BM_LOCAL_PG_BIN"; return; fi
  if command -v initdb >/dev/null 2>&1; then dirname "$(command -v initdb)"; return; fi
  local d
  d="$(find /usr/lib/postgresql -maxdepth 2 -name bin -type d 2>/dev/null | sort -V | tail -n 1)"
  [[ -n "$d" && -x "$d/initdb" ]] || die "нет initdb/pg_ctl (задайте BM_LOCAL_PG_BIN)"
  echo "$d"
}

# postgres не запускается от root: под root команды кластера идут от пользователя postgres.
# setpriv делает exec без fork — pid процесса остаётся тем же (важно для pid-файла и сигналов).
as_pg() {
  if [[ "$(id -u)" == "0" ]]; then
    id postgres >/dev/null 2>&1 || die "запуск от root, а пользователя postgres нет"
    setpriv --reuid=postgres --regid=postgres --init-groups --reset-env -- env PATH="$PATH" "$@"
  else
    "$@"
  fi
}

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

gen_secret() { python3 -c 'import secrets,sys; print(secrets.token_hex(int(sys.argv[1])))' "$1"; }

ensure_env() {
  mkdir -p "$STATE" "$RUN" "$LOGS" "$BACKUP_DIR"
  [[ -f "$ENV_FILE" ]] && return
  umask 077
  cat >"$ENV_FILE" <<EOF
# Сгенерировано deploy/local/stack.sh $(date -u +%Y-%m-%dT%H:%M:%SZ). Только для локального стека; в git не попадает.
DATABASE_URL=postgresql://bm:bm@127.0.0.1:$PG_PORT/bm
SESSION_SECRET=$(gen_secret 32)
SESSION_COOKIE_SECURE=false
ADMIN_INITIAL_LOGIN=admin
ADMIN_INITIAL_PASSWORD=init-$(gen_secret 12)
# Пароль, на который make smoke сменит временный при первом входе (дальше вход только с ним).
BM_ADMIN_PASSWORD=smoke-$(gen_secret 12)
UVICORN_WORKERS=$WORKERS
IMPORT_ENABLED=false
IMPORT_HTTPS_PROXY=
WEBHOOK_URL=
LOG_LEVEL=INFO
PROMETHEUS_MULTIPROC_DIR=$STATE/metrics
BASE_URL=http://127.0.0.1:$API_PORT
EOF
  umask 022
  log "создан $ENV_FILE"
}

load_env() { set -a; # shellcheck disable=SC1090
  source "$ENV_FILE"; set +a; }

pidfile() { echo "$RUN/$1.pid"; }

alive() {
  local f; f="$(pidfile "$1")"
  [[ -f "$f" ]] && kill -0 "$(<"$f")" 2>/dev/null
}

# Запуск в отдельной сессии: pid = pgid, kill -- -pid попадает во все процессы сервиса (мастер uvicorn и воркеры).
spawn() {
  local name="$1"; shift
  local pf; pf="$(pidfile "$name")"
  setsid bash -c 'echo $$ >"$0"; exec "$@"' "$pf" "$@" >>"$LOGS/$name.log" 2>&1 </dev/null &
  disown $! 2>/dev/null || true
  for _ in $(seq 50); do [[ -s "$pf" ]] && break; sleep 0.1; done
  [[ -s "$pf" ]] || die "$name не запустился (см. $LOGS/$name.log)"
}

# ---------------------------------------------------------------- postgres

pg_init() {
  [[ -f "$PGDATA/PG_VERSION" ]] && return
  local bin; bin="$(pg_bin)"
  mkdir -p "$PGDATA"
  local pw="$STATE/.pwfile"; printf 'bm\n' >"$pw"
  if [[ "$(id -u)" == "0" ]]; then chown postgres: "$PGDATA" "$pw"; chmod 0700 "$PGDATA"; fi
  # Суперпользователь кластера — bm/bm (как POSTGRES_USER в compose, пакет 2, QA6). Только 127.0.0.1.
  as_pg "$bin/initdb" -D "$PGDATA" -U bm --pwfile="$pw" --auth-local=trust --auth-host=scram-sha-256 \
    --encoding=UTF8 --locale=C.UTF-8 >"$LOGS/initdb.log" 2>&1 || { cat "$LOGS/initdb.log" >&2; die "initdb"; }
  rm -f "$pw"
  cat >>"$PGDATA/postgresql.conf" <<EOF
# deploy/local/stack.sh
listen_addresses = '127.0.0.1'
port = $PG_PORT
# Без unix-сокета: путь в каталоге проекта длиннее 107 байт; соединения только по TCP 127.0.0.1.
unix_socket_directories = ''
timezone = 'UTC'
max_connections = 200
EOF
  log "initdb: кластер $PGDATA (порт $PG_PORT)"
}

pg_start() {
  alive postgres && return
  pg_init
  port_busy "$PG_PORT" && die "порт $PG_PORT занят: задайте BM_LOCAL_PG_PORT"
  local bin; bin="$(pg_bin)"
  if [[ "$(id -u)" == "0" ]]; then touch "$LOGS/postgres.log"; chown postgres: "$LOGS/postgres.log"; fi
  # postgres (не pg_ctl) в своей сессии: pid мастера = pgid, stop/kill работают как у остальных сервисов.
  local pf; pf="$(pidfile postgres)"
  if [[ "$(id -u)" == "0" ]]; then
    spawn postgres setpriv --reuid=postgres --regid=postgres --init-groups -- "$bin/postgres" -D "$PGDATA"
  else
    spawn postgres "$bin/postgres" -D "$PGDATA"
  fi
  for _ in $(seq 100); do
    "$bin/pg_isready" -q -h 127.0.0.1 -p "$PG_PORT" && break
    sleep 0.2
  done
  "$bin/pg_isready" -q -h 127.0.0.1 -p "$PG_PORT" || die "postgres не готов (см. $LOGS/postgres.log)"
  if ! PGPASSWORD=bm "$bin/psql" -X -h 127.0.0.1 -p "$PG_PORT" -U bm -d postgres -Atc \
       "SELECT 1 FROM pg_database WHERE datname='bm'" | grep -q 1; then
    PGPASSWORD=bm "$bin/createdb" -h 127.0.0.1 -p "$PG_PORT" -U bm -O bm bm
    log "создана БД bm"
  fi
  log "postgres: 127.0.0.1:$PG_PORT (pid $(<"$pf"))"
}

# ---------------------------------------------------------------- api и worker

app_start() {
  local name="$1"
  alive "$name" && return
  load_env
  [[ -x "$VENV/bin/python" ]] || die "нет $VENV (make sync)"
  case "$name" in
    api)
      port_busy "$API_PORT" && die "порт $API_PORT занят: задайте BM_LOCAL_API_PORT"
      # Как entrypoint образа: каталог метрик нескольких процессов очищается при старте api.
      rm -rf "$PROMETHEUS_MULTIPROC_DIR"; mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
      # Та же точка запуска, что CMD образа (T098, 5Д1): воркеры — из UVICORN_WORKERS, TCP_NODELAY — в bm.serve.
      spawn api "$VENV/bin/python" -m bm.serve --host 127.0.0.1 --port "$API_PORT" ;;
    worker)
      # Каталог метрик только у api (пакет 2, В1-6).
      spawn worker env -u PROMETHEUS_MULTIPROC_DIR "$VENV/bin/python" -m bm.worker ;;
  esac
  log "$name: pid $(<"$(pidfile "$name")")"
}

svc_start() {
  case "$1" in
    postgres) pg_start ;;
    api|worker) app_start "$1" ;;
    *) die "неизвестный сервис $1 (postgres|api|worker)" ;;
  esac
}

# Сигнал остановки главному процессу, до 10 с ожидания, затем SIGKILL группы — как `docker stop` по умолчанию.
# Сигнал — как STOPSIGNAL образа: у postgres SIGINT (fast shutdown, так в официальном образе), у api/worker SIGTERM.
svc_stop() {
  local name="$1" f pid sig=TERM
  [[ "$name" == postgres ]] && sig=INT
  f="$(pidfile "$name")"
  if ! alive "$name"; then rm -f "$f"; return 0; fi
  pid="$(<"$f")"
  kill "-$sig" "$pid" 2>/dev/null || true
  for _ in $(seq 100); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
  if kill -0 "$pid" 2>/dev/null; then
    log "$name: не остановился за 10 с — SIGKILL"
    kill -KILL -- "-$pid" 2>/dev/null || true
  fi
  # Дочерние процессы, пережившие мастер, — тоже (группа = сессия сервиса).
  kill -KILL -- "-$pid" 2>/dev/null || true
  rm -f "$f"
  log "$name: остановлен"
}

# SIGKILL всей группе процессов — как `docker kill`.
svc_kill() {
  local name="$1" f pid
  f="$(pidfile "$name")"
  alive "$name" || { rm -f "$f"; log "$name: не запущен"; return 0; }
  pid="$(<"$f")"
  kill -KILL -- "-$pid" 2>/dev/null || true
  for _ in $(seq 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
  rm -f "$f"
  log "$name: SIGKILL (группа $pid)"
}

wait_ready() {
  local url="http://127.0.0.1:$API_PORT/readyz" deadline=$((SECONDS + ${1:-120}))
  while (( SECONDS < deadline )); do
    if python3 - "$url" <<'PY' 2>/dev/null; then log "готов: $url"; return 0; fi
import sys, urllib.request
sys.exit(0 if urllib.request.urlopen(sys.argv[1], timeout=2).status == 200 else 1)
PY
    alive api || die "api завершился (см. $LOGS/api.log)"
    sleep 1
  done
  die "нет ответа 200 от $url за ${1:-120} с (см. $LOGS/api.log)"
}

status() {
  for s in "${SERVICES[@]}"; do
    if alive "$s"; then echo "$s: running (pid $(<"$(pidfile "$s")"))"; else echo "$s: stopped"; fi
  done
}

pg_env() {
  export PGHOST=127.0.0.1 PGPORT="$PG_PORT" PGUSER=bm PGPASSWORD=bm PGDATABASE=bm BACKUP_DIR="$BACKUP_DIR"
  BACKUP_KEEP="${BACKUP_KEEP:-14}"; export BACKUP_KEEP
  PATH="$(pg_bin):$PATH"; export PATH
}

cmd="${1:-}"; shift || true
case "$cmd" in
  up)
    ensure_env
    (cd "$ROOT" && uv sync --project app/backend --frozen --quiet)
    pg_start; app_start api; wait_ready 120; app_start worker; status ;;
  down)
    for s in worker api postgres; do svc_stop "$s"; done ;;
  destroy)
    for s in worker api postgres; do svc_stop "$s"; done
    rm -rf "$STATE"; log "удалён $STATE" ;;
  status) status ;;
  start) ensure_env; svc_start "${1:?сервис}" ;;
  stop) svc_stop "${1:?сервис}" ;;
  kill) svc_kill "${1:?сервис}" ;;
  wait) wait_ready "${1:-120}" ;;
  env)
    [[ -f "$ENV_FILE" ]] || die "стек не создан: make up-local"
    load_env
    printf 'export BM_STACK=local\nexport BASE_URL=%q\nexport DATABASE_URL=%q\n' "$BASE_URL" "$DATABASE_URL"
    printf 'export BM_ADMIN_LOGIN=%q\nexport BM_ADMIN_PASSWORD=%q\nexport BM_ENV_FILE=%q\n' \
      "$ADMIN_INITIAL_LOGIN" "$BM_ADMIN_PASSWORD" "$ENV_FILE"
    printf 'export BM_LOCAL_STACK=%q\n' "$ROOT/deploy/local/stack.sh" ;;
  backup)
    alive postgres || die "postgres не запущен (make up-local)"
    pg_env; bash "$ROOT/deploy/backup/backup.sh" ;;
  restore)
    alive postgres || die "postgres не запущен (make up-local)"
    svc_stop worker; svc_stop api
    ( pg_env; bash "$ROOT/deploy/backup/restore.sh" "$@" )
    app_start api; wait_ready 120; app_start worker; status ;;
  logs) tail -n "${2:-100}" "$LOGS/${1:?сервис}.log" ;;
  *) sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
