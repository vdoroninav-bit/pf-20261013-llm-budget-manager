#!/usr/bin/env bash
# Нагрузочный тест SC-001 и SC-004 (T086): `make up && make load` или `make up-local && BM_STACK=local make load`.
# Нагрузка — vegeta, если он есть (PATH или VEGETA=путь), иначе генератор на httpx с фиксированной частотой.
# Параметры: LOAD_RATE (50), LOAD_DURATION_S (600), LOAD_OUT (каталог результатов). Подробно — tests/load/targets.py.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [[ "${BM_STACK:-compose}" == "local" && -z "${BM_ENV_FILE:-}" ]]; then
  eval "$(bash deploy/local/stack.sh env)"
fi
export BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"

if [[ -n "${VEGETA:-}" ]]; then
  echo "run_load.sh: vegeta — $VEGETA"
elif command -v vegeta >/dev/null 2>&1; then
  echo "run_load.sh: vegeta — $(command -v vegeta)"
else
  echo "run_load.sh: vegeta не найден — нагрузку даёт генератор на httpx (vegeta не использован)"
fi

exec uv run --project app/backend --frozen python -m tests.load.targets
