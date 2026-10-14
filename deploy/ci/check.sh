#!/usr/bin/env bash
# SDL-проверки (T088, принцип V, ГОСТ Р 56939): SBOM CycloneDX, лицензии, уязвимости, import-linter.
# Запуск: make check  (из корня репозитория). Результаты — deploy/ci/out/.
#
# Переменные:
#   CHECK_STRICT=1   пропуск любого шага (нет фронтенда, trivy, образа) считается ошибкой — для CI и релиза
#   IMAGE=bm:local   образ для Trivy (собирается make build, T087)
#   FRONTEND_DIR     каталог фронтенда (по умолчанию app/frontend)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

OUT="deploy/ci/out"
CI="deploy/ci"
BACKEND="app/backend"
FRONTEND_DIR="${FRONTEND_DIR:-app/frontend}"
IMAGE="${IMAGE:-bm:local}"
CHECK_STRICT="${CHECK_STRICT:-0}"

# Версии инструментов закреплены (воспроизводимость). В закрытом контуре — из локального кэша uv/npm.
CYCLONEDX_PY="cyclonedx-bom==7.5.0"
PIP_AUDIT="pip-audit==2.10.1"
CYCLONEDX_NPM="@cyclonedx/cyclonedx-npm@6.0.1"

mkdir -p "$OUT"
declare -a RESULTS=()
FAILED=0

ok()   { RESULTS+=("OK    $1"); }
fail() { RESULTS+=("FAIL  $1"); FAILED=1; }
skip() {
  if [[ "$CHECK_STRICT" == "1" ]]; then RESULTS+=("FAIL  $1 (пропуск запрещён: CHECK_STRICT=1)"); FAILED=1
  else RESULTS+=("SKIP  $1"); fi
}
step() { echo; echo "=== $1 ==="; }

# ---------- Python ----------
step "Python: окружение поставки (без dev-зависимостей, все extras)"
PROD_VENV="$OUT/venv-prod"
if UV_PROJECT_ENVIRONMENT="$ROOT/$PROD_VENV" uv sync --project "$BACKEND" --frozen --no-dev --all-extras --no-editable --quiet; then
  ok "python: uv sync --frozen (lock-файл согласован)"
else
  fail "python: uv sync --frozen"
fi

step "Python: SBOM CycloneDX"
if uvx --quiet --from "$CYCLONEDX_PY" cyclonedx-py environment \
     --pyproject "$BACKEND/pyproject.toml" --output-reproducible --of JSON \
     -o "$OUT/sbom-python.cdx.json" "$PROD_VENV/bin/python"; then
  ok "python: SBOM $OUT/sbom-python.cdx.json"
else
  fail "python: SBOM"
fi

step "Python: лицензии"
if python3 "$CI/check_licenses.py" --allow "$CI/licenses-allow.txt" --exceptions "$CI/licenses-exceptions.txt" \
     "$OUT/sbom-python.cdx.json" | tee "$OUT/licenses-python.txt"; then
  ok "python: лицензии по белому списку"
else
  fail "python: лицензии (см. $OUT/licenses-python.txt)"
fi

step "Python: уязвимости (pip-audit)"
uv export --project "$BACKEND" --frozen --no-dev --all-extras --format requirements-txt --no-emit-project \
  --quiet -o "$OUT/requirements-prod.txt"
IGNORE_ARGS=()
if [[ -f "$CI/vuln-ignore.txt" ]]; then
  while read -r id _; do [[ -n "$id" && "$id" != \#* ]] && IGNORE_ARGS+=(--ignore-vuln "$id"); done < "$CI/vuln-ignore.txt"
fi
uvx --quiet "$PIP_AUDIT" -r "$OUT/requirements-prod.txt" --disable-pip --strict --progress-spinner off \
  "${IGNORE_ARGS[@]}" --format json -o "$OUT/pip-audit.json"
AUDIT_RC=$?
python3 - "$OUT/pip-audit.json" <<'PY' || true
import json, sys
try:
    deps = json.load(open(sys.argv[1]))["dependencies"]
except Exception as e:  # noqa: BLE001
    print(f"pip-audit: отчёт не прочитан ({e})"); sys.exit(0)
vulns = [(d["name"], d.get("version"), v["id"]) for d in deps for v in d.get("vulns", [])]
print(f"pip-audit: пакетов {len(deps)}, уязвимостей {len(vulns)}")
for name, ver, vid in vulns:
    print(f"  {vid}  {name}=={ver}")
PY
if [[ "$AUDIT_RC" -eq 0 ]]; then
  ok "python: pip-audit — известных уязвимостей нет"
else
  fail "python: pip-audit (см. $OUT/pip-audit.json)"
fi

step "Python: архитектурные контракты (import-linter)"
if (cd "$BACKEND" && uv run --frozen --all-extras lint-imports --config importlinter.ini --no-logo); then
  ok "python: import-linter"
else
  fail "python: import-linter"
fi

# ---------- npm ----------
step "npm: фронтенд ($FRONTEND_DIR)"
if [[ -f "$FRONTEND_DIR/package.json" ]]; then
  if [[ ! -f "$FRONTEND_DIR/package-lock.json" ]]; then
    fail "npm: нет package-lock.json (версии не закреплены)"
  else
    if grep -q '"node_modules/@react-admin/ra-' "$FRONTEND_DIR/package-lock.json"; then
      fail "npm: в package-lock.json есть @react-admin/ra-* (enterprise-часть react-admin)"
    else
      ok "npm: нет @react-admin/ra-*"
    fi
    [[ -d "$FRONTEND_DIR/node_modules" ]] || npm --prefix "$FRONTEND_DIR" ci --no-audit --no-fund
    if (cd "$FRONTEND_DIR" && npx --yes "$CYCLONEDX_NPM" --omit dev --output-reproducible \
          --output-format JSON --output-file "$ROOT/$OUT/sbom-npm.cdx.json"); then
      ok "npm: SBOM $OUT/sbom-npm.cdx.json"
      if python3 "$CI/check_licenses.py" --allow "$CI/licenses-allow.txt" --exceptions "$CI/licenses-exceptions.txt" \
           "$OUT/sbom-npm.cdx.json" | tee "$OUT/licenses-npm.txt"; then
        ok "npm: лицензии по белому списку"
      else
        fail "npm: лицензии (см. $OUT/licenses-npm.txt)"
      fi
    else
      fail "npm: SBOM"
    fi
    if npm --prefix "$FRONTEND_DIR" audit --omit=dev --audit-level=critical; then
      ok "npm: audit — критичных уязвимостей нет"
    else
      fail "npm: audit (critical)"
    fi
  fi
else
  skip "npm: нет $FRONTEND_DIR/package.json (появится в T002)"
fi

# ---------- образ ----------
step "Образ: Trivy ($IMAGE)"
if ! command -v trivy >/dev/null 2>&1; then
  skip "trivy: не установлен"
elif ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  skip "trivy: нет образа $IMAGE (make build, T087)"
else
  trivy image --quiet --format cyclonedx --output "$OUT/sbom-image.cdx.json" "$IMAGE" \
    && ok "trivy: SBOM образа $OUT/sbom-image.cdx.json" || fail "trivy: SBOM образа"
  trivy image --quiet --severity CRITICAL --exit-code 1 "$IMAGE" \
    && ok "trivy: 0 critical" || fail "trivy: есть critical"
fi

# ---------- итог ----------
echo
echo "=== Итог make check ==="
printf '%s\n' "${RESULTS[@]}" | tee "$OUT/summary.txt"
if [[ "$FAILED" -ne 0 ]]; then
  echo "make check: FAIL"
  exit 1
fi
echo "make check: OK (SBOM и отчёты — $OUT/)"
