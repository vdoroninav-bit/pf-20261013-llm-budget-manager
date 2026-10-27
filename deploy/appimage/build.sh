#!/usr/bin/env bash
# Сборка Linux AppImage (x86_64): dist/LLM_Budget_Manager-<версия>-x86_64.AppImage
#
# Внутри — без Docker и без зависимостей на машине пользователя (нужен только glibc ≥ 2.28 и libz: Ubuntu 20.04+, Debian 10+, RHEL/Astra/РЕД ОС на glibc 2.28+):
#   usr/python   Python 3.13 (python-build-standalone через uv) с пакетом bm и его зависимостями из uv.lock
#   usr/pg       PostgreSQL 16 из колеса pgserver (manylinux2014, перемещаемые бинарники)
#   usr/share/llm-budget/admin   сборка админки (app/frontend, npm run build)
#   usr/lib/llm-budget/bm_appimage   обёртка запуска api (каталог админки внутри AppImage)
#   AppRun       супервизор: initdb при первом запуске, секреты, postgres + api + worker, Ctrl+C — остановка
#
# Нужно на машине сборки: uv, node/npm, curl, unzip, desktop-file-validate (пакет desktop-file-utils). Сеть: PyPI, npm, GitHub (релизы).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$ROOT/deploy/appimage"
OUT="${OUT:-$ROOT/dist}"
WORK="${WORK:-$ROOT/dist/appimage-build}"
APPDIR="$WORK/AppDir"

PYTHON_VERSION="3.13.14"
PGSERVER_WHEEL="pgserver-0.1.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
PGSERVER_SHA256="1a5d07c61d51f2abfef4ef61e2ef5cd014b994f7e09de8d3c140d2cf370e84a8"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.0/appimagetool-x86_64.AppImage"
APPIMAGETOOL_SHA256="46fdd785094c7f6e545b61afcfb0f3d98d8eab243f644b4b17698c01d06083d1"

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/app/backend/pyproject.toml" | head -n1)"
SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)"

log() { echo "build-appimage: $*"; }
check_sha() { echo "$2  $1" | sha256sum -c --quiet - || { echo "sha256 не совпал: $1" >&2; exit 1; }; }

for t in uv npm curl unzip desktop-file-validate; do
  command -v "$t" >/dev/null || { echo "нет $t (см. шапку build.sh)" >&2; exit 1; }
done

rm -rf "$APPDIR"; mkdir -p "$APPDIR/usr/lib/llm-budget" "$APPDIR/usr/share/llm-budget" "$WORK/cache" "$OUT"

log "1/6 Python $PYTHON_VERSION"
UV_PYTHON_INSTALL_DIR="$WORK/cache/python" uv python install --no-bin "$PYTHON_VERSION" >/dev/null 2>&1
PYSRC="$(UV_PYTHON_INSTALL_DIR="$WORK/cache/python" uv python find "$PYTHON_VERSION")"
PYSRC="$(cd "$(dirname "$PYSRC")/.." && pwd -P)"
cp -a "$PYSRC" "$APPDIR/usr/python"
rm -f "$APPDIR/usr/python/lib/python3.13/EXTERNALLY-MANAGED"
PY="$APPDIR/usr/python/bin/python3.13"

log "2/6 Пакет bm и зависимости (uv.lock, без dev)"
REQ="$WORK/requirements.txt"
(cd "$ROOT/app/backend" && uv export --frozen --no-dev --no-emit-project --no-hashes -o "$REQ" >/dev/null)
uv pip install --python "$PY" --python-platform x86_64-manylinux_2_28 --only-binary :all: --no-cache \
  -r "$REQ" >/dev/null 2>&1
uv pip install --python "$PY" --no-deps --no-cache "$ROOT/app/backend" >/dev/null 2>&1
# Тесты, кэши и заголовки в поставку не нужны
find "$APPDIR/usr/python" -name '__pycache__' -type d -prune -exec rm -rf {} +
rm -rf "$APPDIR/usr/python/lib/python3.13/test" "$APPDIR/usr/python/lib/python3.13/idlelib" \
       "$APPDIR/usr/python/lib/python3.13/tkinter" "$APPDIR/usr/python/include"
cp -a "$HERE/bm_appimage" "$APPDIR/usr/lib/llm-budget/"
find "$APPDIR/usr/lib/llm-budget" -name '__pycache__' -type d -prune -exec rm -rf {} +

log "3/6 PostgreSQL 16 (pgserver 0.1.4)"
mkdir -p "$WORK/cache/pg"
if [[ ! -f "$WORK/cache/pg/$PGSERVER_WHEEL" ]]; then
  pip download --no-deps --only-binary=:all: --python-version 3.12 --platform manylinux_2_17_x86_64 \
    -d "$WORK/cache/pg" pgserver==0.1.4 >/dev/null
fi
check_sha "$WORK/cache/pg/$PGSERVER_WHEEL" "$PGSERVER_SHA256"
mkdir -p "$APPDIR/usr/pg"
unzip -q "$WORK/cache/pg/$PGSERVER_WHEEL" 'pgserver/pginstall/*' 'pgserver.libs/*' 'pgserver-0.1.4.dist-info/LICENSE' \
  -d "$APPDIR/usr/pg"
chmod +x "$APPDIR/usr/pg/pgserver/pginstall/bin/"*
rm -rf "$APPDIR/usr/pg/pgserver/pginstall/include" "$APPDIR/usr/pg/pgserver/pginstall/lib/"*.a
"$APPDIR/usr/pg/pgserver/pginstall/bin/postgres" --version

log "4/6 Админка"
(cd "$ROOT/app/frontend" && { [[ -d node_modules ]] || npm ci --no-audit --no-fund; } && npm run build >/dev/null)
cp -a "$ROOT/app/frontend/dist" "$APPDIR/usr/share/llm-budget/admin"

log "5/6 AppDir"
install -m 0755 "$HERE/AppRun" "$APPDIR/AppRun"
cp "$HERE/llm-budget.desktop" "$APPDIR/"
cp "$HERE/llm-budget.svg" "$APPDIR/"
ln -sf llm-budget.svg "$APPDIR/.DirIcon"
echo "$VERSION ($SHA)" >"$APPDIR/usr/share/llm-budget/VERSION"
cp "$ROOT/docs/admin-guide.md" "$ROOT/docs/integration-guide.md" "$APPDIR/usr/share/llm-budget/" 2>/dev/null || true

log "6/6 appimagetool"
TOOL="$WORK/cache/appimagetool-1.9.0"
[[ -f "$TOOL" ]] || curl -fsSL -o "$TOOL" "$APPIMAGETOOL_URL"
check_sha "$TOOL" "$APPIMAGETOOL_SHA256"
chmod +x "$TOOL"
TARGET="$OUT/LLM_Budget_Manager-$VERSION-x86_64.AppImage"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$TARGET" >"$WORK/appimagetool.log" 2>&1 \
  || { cat "$WORK/appimagetool.log" >&2; exit 1; }
(cd "$OUT" && sha256sum "$(basename "$TARGET")" >"$(basename "$TARGET").sha256")
log "готово: $TARGET ($(du -h "$TARGET" | cut -f1))"
