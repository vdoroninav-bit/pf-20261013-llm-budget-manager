#!/usr/bin/env bash
# SDL-проверки (T088, принцип V, ГОСТ Р 56939): SBOM CycloneDX, лицензии, уязвимости, import-linter.
# Запуск: make check  (из корня репозитория). Результаты — deploy/ci/out/.
#
# Переменные:
#   CHECK_STRICT=1   пропуск любого шага (нет фронтенда, trivy, образа) считается ошибкой — для CI и релиза
#   IMAGE=bm:local   образ для Trivy (собирается make build, T087)
#   FRONTEND_DIR     каталог фронтенда (по умолчанию app/frontend)
#   TRIVY            команда Trivy (по умолчанию `trivy` из PATH). В CI (T092) — контейнер Trivy с сокетом Docker,
#                    см. .github/workflows/ci.yml. Каталог проекта должен быть виден команде по тому же пути.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 2

OUT="deploy/ci/out"
CI="deploy/ci"
BACKEND="app/backend"
FRONTEND_DIR="${FRONTEND_DIR:-app/frontend}"
IMAGE="${IMAGE:-bm:local}"
CHECK_STRICT="${CHECK_STRICT:-0}"
CLIENT_DIR="app/client/budget_client"
read -r -a TRIVY_CMD <<<"${TRIVY:-trivy}"

# Версии инструментов закреплены (воспроизводимость). В закрытом контуре — из локального кэша uv/npm.
CYCLONEDX_PY="cyclonedx-bom==7.5.0"
PIP_AUDIT="pip-audit==2.10.1"
CYCLONEDX_NPM="@cyclonedx/cyclonedx-npm@6.0.1"

mkdir -p "$OUT"
declare -a RESULTS=()
FAILED=0

ok()   { RESULTS+=("OK    $1"); }
fail() { RESULTS+=("FAIL  $1"); FAILED=1; }
# Предупреждение: в итоге и в сводке CI видно, но make check не роняет (принятые ограничения, 5Д5).
warn() { RESULTS+=("WARN  $1"); }
skip() {
  if [[ "$CHECK_STRICT" == "1" ]]; then RESULTS+=("FAIL  $1 (пропуск запрещён: CHECK_STRICT=1)"); FAILED=1
  else RESULTS+=("SKIP  $1"); fi
}
step() { echo; echo "=== $1 ==="; }

# ---------- Python ----------
step "Python: окружение поставки (без dev-зависимостей и без extras) + эталонный клиент"
PROD_VENV="$OUT/venv-prod"
rm -rf "$PROD_VENV"
if UV_PROJECT_ENVIRONMENT="$ROOT/$PROD_VENV" uv sync --project "$BACKEND" --frozen --no-dev --no-editable --quiet; then
  ok "python: uv sync --frozen (lock-файл согласован)"
else
  fail "python: uv sync --frozen"
fi
# budget_client поставляется заказчику вместе с сервисом (3Б9): входит в SBOM. Своих зависимостей у него нет.
if uv pip install --quiet --python "$PROD_VENV/bin/python" --no-deps "$CLIENT_DIR"; then
  ok "python: budget_client в окружении поставки (для SBOM)"
else
  fail "python: установка budget_client"
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
uv export --project "$BACKEND" --frozen --no-dev --format requirements-txt --no-emit-project \
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
if (cd "$BACKEND" && uv run --frozen lint-imports --config importlinter.ini --no-logo); then
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
      # Явная зависимость @tanstack/react-query (14_tech_rulings.md, пакет 3, 3Ф3): лицензия по SBOM.
      python3 - "$OUT/sbom-npm.cdx.json" <<'PY' | tee -a "$OUT/licenses-npm.txt"
import json, sys
comps = json.load(open(sys.argv[1])).get("components", [])
for c in comps:
    if c.get("group") == "@tanstack" and c.get("name") == "react-query":
        lic = ", ".join((x.get("license") or {}).get("id") or x.get("expression", "?") for x in c.get("licenses", []))
        print(f"npm SBOM: @tanstack/react-query@{c.get('version')} — {lic or 'нет лицензии'}")
PY
    else
      fail "npm: SBOM"
    fi
    # Гейт — только critical (конституция). Некритичные находки — WARN с перечнем advisory (5Д5: 7 moderate
    # через react-admin → decode-uri-component приняты как известное ограничение v1).
    if npm --prefix "$FRONTEND_DIR" audit --omit=dev --audit-level=critical; then
      ok "npm: audit — критичных уязвимостей нет"
    else
      fail "npm: audit (critical)"
    fi
    npm --prefix "$FRONTEND_DIR" audit --omit=dev --json >"$OUT/npm-audit.json" 2>/dev/null || true
    if python3 "$CI/npm_advisories.py" "$OUT/npm-audit.json" | tee "$OUT/npm-advisories.txt"; then
      npm_line="$(tail -n 1 "$OUT/npm-advisories.txt")"
      [[ "$npm_line" == "уязвимостей нет" ]] || warn "npm: audit — $npm_line (не critical, 5Д5)"
    else
      warn "npm: audit --json не разобран (см. $OUT/npm-advisories.txt); гейт critical — строкой выше"
    fi
  fi
else
  skip "npm: нет $FRONTEND_DIR/package.json"
fi

# ---------- образ ----------
step "Образ: Trivy ($IMAGE)"
if [[ "${TRIVY_CMD[0]}" == "trivy" ]] && ! command -v trivy >/dev/null 2>&1; then
  skip "trivy: не установлен (в CI — контейнер Trivy, T092)"
elif ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  skip "trivy: нет образа $IMAGE (make build)"
else
  if "${TRIVY_CMD[@]}" image --quiet --format cyclonedx --output "$ROOT/$OUT/sbom-image.cdx.json" "$IMAGE"; then
    ok "trivy: SBOM образа $OUT/sbom-image.cdx.json"
  else
    fail "trivy: SBOM образа"
  fi
  # Источник находок и путь каждого пакета (T099, 5Д3): Target, PkgPath, FilePath — в JSON при любом исходе гейта;
  # в CI его разбирает `gha.py trivy` (::notice). Только диагностика: на гейт не влияет, порог тот же.
  rm -f "$OUT/trivy-critical.json"
  "${TRIVY_CMD[@]}" image --quiet --format json --list-all-pkgs --output "$ROOT/$OUT/trivy-critical.json" \
    --severity CRITICAL "$IMAGE" || echo "trivy: JSON-отчёт не получен (диагностика, на гейт не влияет)"
  "${TRIVY_CMD[@]}" image --quiet --severity CRITICAL --exit-code 1 "$IMAGE" | tee "$OUT/trivy-critical.txt"
  if [[ "${PIPESTATUS[0]}" -eq 0 ]]; then
    ok "trivy: 0 critical"
  else
    fail "trivy: есть critical (см. $OUT/trivy-critical.txt)"
  fi
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
