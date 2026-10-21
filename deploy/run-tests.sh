#!/usr/bin/env bash
# Единый прогон тестов: локально (make test / make test-unit) и в контейнере (make test-docker).
#   run-tests.sh unit  — pytest tests/unit tests/deploy + vitest, без БД и Docker
#   run-tests.sh full  — проверка БД, pytest (unit, contract, integration, acceptance, security, deploy),
#                        import-linter, vitest
# Переменные:
#   DATABASE_URL     строка подключения к ТЕСТОВОЙ БД (только для full). Тесты пересоздают схему,
#                    поэтому имя БД обязано содержать «test» (защита от прогона по рабочей базе).
#   SKIP_FRONTEND=1  не запускать vitest (контейнер тестов без Node — до T087)
#   PYTEST_ARGS      дополнительные аргументы pytest (например, "-k money -x")
set -euo pipefail

MODE="${1:-full}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND="app/backend"
FRONTEND_DIR="${FRONTEND_DIR:-app/frontend}"
UVR=(uv run --project "$BACKEND" --frozen)

run_pytest() {
  local rc=0
  "${UVR[@]}" pytest "$@" ${PYTEST_ARGS:-} || rc=$?
  if [[ "$rc" -eq 5 ]]; then
    echo "pytest: тесты не найдены (exit 5) — для каркаса это допустимо, считаем успехом"
    return 0
  fi
  return "$rc"
}

check_db() {
  : "${DATABASE_URL:?DATABASE_URL не задан (make test задаёт его по умолчанию)}"
  local db="${DATABASE_URL%%\?*}"; db="${db##*/}"
  if [[ "$db" != *test* && "${ALLOW_ANY_TEST_DB:-0}" != "1" ]]; then
    echo "ОТКАЗ: имя БД '$db' не содержит 'test'. Тесты пересоздают схему; укажите тестовую БД." >&2
    exit 2
  fi
  "${UVR[@]}" python - <<'PY'
import asyncio, os, sys
import asyncpg

async def main() -> None:
    try:
        conn = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=10)
    except Exception as e:  # noqa: BLE001
        print(f"БД недоступна: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
    ver = conn.get_server_version()
    full = await conn.fetchval("SHOW server_version")
    await conn.close()
    print(f"БД доступна: PostgreSQL {full}")
    if ver.major < 18:
        print(f"ВНИМАНИЕ: целевая версия PostgreSQL 18 (12_plan.md), тестовая — {ver.major}. "
              "Не используйте возможности только PG 18, пока стенд не обновлён.")

asyncio.run(main())
PY
}

run_frontend() {
  if [[ "${SKIP_FRONTEND:-0}" == "1" ]]; then
    echo "vitest: пропущен (SKIP_FRONTEND=1)"; return 0
  fi
  if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
    echo "vitest: нет $FRONTEND_DIR/package.json (фронтенд появится в T002) — пропуск"; return 0
  fi
  [[ -d "$FRONTEND_DIR/node_modules" ]] || npm --prefix "$FRONTEND_DIR" ci --no-audit --no-fund
  npm --prefix "$FRONTEND_DIR" run test
}

case "$MODE" in
  unit)
    echo "=== pytest tests/unit ==="
    run_pytest tests/unit tests/deploy
    echo "=== frontend ==="
    run_frontend
    ;;
  full)
    echo "=== БД ==="
    check_db
    echo "=== pytest ==="
    run_pytest tests/unit tests/contract tests/integration tests/acceptance tests/security tests/deploy
    echo "=== import-linter ==="
    (cd "$BACKEND" && uv run --frozen lint-imports --config importlinter.ini --no-logo)
    echo "=== frontend ==="
    run_frontend
    ;;
  *)
    echo "использование: $0 unit|full" >&2; exit 2 ;;
esac
echo "run-tests.sh $MODE: OK"
