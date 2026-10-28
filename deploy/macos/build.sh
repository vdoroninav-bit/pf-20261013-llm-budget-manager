#!/usr/bin/env bash
# Сборка Mac-приложения для Apple Silicon (arm64): dist/LLM_Budget_Manager-<версия>-macos-arm64.zip
# Внутри zip — «LLM Budget Manager.app». Двойной клик — запуск без Docker. Собирается на Linux или macOS (кросс-сборка:
# готовые бинарники под macOS, ничего не компилируется).
#
#   Contents/MacOS/LLM Budget Manager    sh: exec встроенного Python с launcher.py
#   Contents/Resources/python            CPython 3.13 (python-build-standalone, aarch64-apple-darwin, подписан издателем ad-hoc)
#   Contents/Resources/site-packages     bm и зависимости из uv.lock (колёса macosx arm64)
#   Contents/Resources/pg                PostgreSQL 16 из колеса pgserver 0.1.4 (macosx_11_0_arm64, перемещаемые, подписаны)
#   Contents/Resources/admin             сборка админки
#   Contents/Resources/lib/bm_appimage   обёртка api из deploy/appimage (каталог админки внутри приложения)
#   Contents/Resources/launcher.py       супервизор с окном «работает / остановить»
#
# Приложение не нотаризовано (нет Apple Developer ID). Первый запуск скачанного приложения: Finder → правый клик →
# «Открыть» или «Системные настройки → Конфиденциальность и безопасность → Всё равно открыть».
# Нужно на машине сборки: uv, node/npm, curl, zip, pip. Сеть: PyPI, npm, GitHub (релизы).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$ROOT/deploy/macos"
OUT="${OUT:-$ROOT/dist}"
WORK="${WORK:-$ROOT/dist/macos-build}"
NAME="LLM Budget Manager"
APP="$WORK/$NAME.app"
RES="$APP/Contents/Resources"

PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260718/cpython-3.13.14%2B20260718-aarch64-apple-darwin-install_only_stripped.tar.gz"
PY_SHA256="06469835e1b0f73bcdb6c498a1d60ce579cc43a980754490a6f1e30062f43850"
PGSERVER_WHEEL="pgserver-0.1.4-cp312-cp312-macosx_11_0_arm64.whl"
PGSERVER_SHA256="780fa89f26a960cca0215caf471e70848dd8597bd8ceaeba7faf42170278980c"

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/app/backend/pyproject.toml" | head -n1)"
SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)"

log() { echo "build-macos: $*"; }
check_sha() {
  local got; got="$(sha256sum "$1" | cut -d' ' -f1)"
  [[ "$got" == "$2" ]] || { echo "sha256 не совпал: $1 ($got, ждали $2)" >&2; exit 1; }
}

for t in uv npm curl zip pip; do command -v "$t" >/dev/null || { echo "нет $t" >&2; exit 1; }; done

rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$RES" "$WORK/cache" "$OUT"

log "1/6 Python 3.13 (aarch64-apple-darwin)"
[[ -f "$WORK/cache/python.tgz" ]] || curl -fsSL -o "$WORK/cache/python.tgz" "$PY_URL"
check_sha "$WORK/cache/python.tgz" "$PY_SHA256"
tar xzf "$WORK/cache/python.tgz" -C "$RES"          # → $RES/python
P="$RES/python/lib/python3.13"
rm -rf "$P/test" "$P/idlelib" "$P/tkinter" "$P/turtledemo" "$P/ensurepip" "$P/lib2to3" "$P/pydoc_data" \
       "$P"/lib-dynload/_tkinter* "$RES/python/lib"/{tcl,tk,itcl,thread}* "$RES/python/lib/libtcl"* "$RES/python/lib/libtk"* \
       "$RES/python/include" "$RES/python/share" "$P"/config-3.13-darwin/*.a
# bin/python3.13 собран статически: libpython3.13.dylib нужна только для встраивания; pip в поставке не нужен
rm -rf "$RES/python/lib/libpython3.13.dylib" "$RES/python/lib/pkgconfig" "$RES/python/bin/"{pip,idle,pydoc,python3-config,python3.13-config}* \
       "$P/site-packages/pip" "$P"/site-packages/pip-*.dist-info
find "$RES/python" -name '__pycache__' -type d -prune -exec rm -rf {} +

log "2/6 bm и зависимости (колёса macosx arm64)"
REQ="$WORK/requirements.txt"
(cd "$ROOT/app/backend" && uv export --frozen --no-dev --no-emit-project --no-hashes -o "$REQ" >/dev/null 2>&1)
uv pip install --target "$RES/site-packages" --python-platform aarch64-apple-darwin --python-version 3.13 \
  --only-binary :all: --no-cache -r "$REQ" >/dev/null 2>&1
uv pip install --target "$RES/site-packages" --python-platform aarch64-apple-darwin --python-version 3.13 \
  --no-deps --no-cache "$ROOT/app/backend" >/dev/null 2>&1
rm -rf "$RES/site-packages/bin"
find "$RES/site-packages" -name '__pycache__' -type d -prune -exec rm -rf {} +
mkdir -p "$RES/lib"; cp -a "$ROOT/deploy/appimage/bm_appimage" "$RES/lib/"
find "$RES/lib" -name '__pycache__' -type d -prune -exec rm -rf {} +

log "3/6 PostgreSQL 16 (pgserver 0.1.4, macosx arm64)"
mkdir -p "$WORK/cache/pg"
[[ -f "$WORK/cache/pg/$PGSERVER_WHEEL" ]] || pip download --no-deps --only-binary=:all: --python-version 3.12 \
  --platform macosx_11_0_arm64 -d "$WORK/cache/pg" pgserver==0.1.4 >/dev/null
check_sha "$WORK/cache/pg/$PGSERVER_WHEEL" "$PGSERVER_SHA256"
mkdir -p "$RES/pg"
python3 - "$WORK/cache/pg/$PGSERVER_WHEEL" "$RES/pg" <<'EOF'
import sys, zipfile, os
whl, dest = sys.argv[1], sys.argv[2]
keep = ("pgserver/pginstall/", "pgserver/.dylibs/", "pgserver-0.1.4.dist-info/LICENSE")
with zipfile.ZipFile(whl) as z:
    for i in z.infolist():
        if i.filename.startswith(keep):
            z.extract(i, dest)
            mode = (i.external_attr >> 16) & 0o777
            p = os.path.join(dest, i.filename)
            if mode and not i.is_dir():
                os.chmod(p, mode)
EOF
PGI="$RES/pg/pgserver/pginstall"
chmod 755 "$PGI/bin/"*
# Нужны только сервер и утилиты обслуживания; остальное (ecpg, pgbench, pg_upgrade…) и заголовки не поставляем
for b in "$PGI"/bin/*; do
  case "$(basename "$b")" in postgres|initdb|pg_ctl|psql|createdb|pg_dump|pg_restore|pg_isready) ;; *) rm -f "$b" ;; esac
done
rm -rf "$PGI/include" "$PGI"/lib/*.a "$PGI/lib/pkgconfig"

log "4/6 Админка"
(cd "$ROOT/app/frontend" && { [[ -d node_modules ]] || npm ci --no-audit --no-fund; } && npm run build >/dev/null)
cp -a "$ROOT/app/frontend/dist" "$RES/admin"

log "5/6 Пакет приложения"
cp "$HERE/launcher.py" "$RES/"
cp "$HERE/AppIcon.icns" "$RES/"
echo "$VERSION ($SHA)" >"$RES/VERSION"
cat >"$APP/Contents/MacOS/$NAME" <<'EOF'
#!/bin/sh
# Встроенный Python, изолированный режим (-I): переменные PYTHON* и пакеты пользователя не влияют.
RES="$(cd "$(dirname "$0")/../Resources" && pwd -P)"
exec "$RES/python/bin/python3.13" -I "$RES/launcher.py" "$@"
EOF
chmod 755 "$APP/Contents/MacOS/$NAME"
sed -e "s/@VERSION@/$VERSION/g" "$HERE/Info.plist" >"$APP/Contents/Info.plist"
printf 'APPL????' >"$APP/Contents/PkgInfo"

log "6/6 zip"
TARGET="$OUT/LLM_Budget_Manager-$VERSION-macos-arm64.zip"
rm -f "$TARGET"
(cd "$WORK" && zip -qry -9 "$TARGET" "$NAME.app")
(cd "$OUT" && sha256sum "$(basename "$TARGET")" >"$(basename "$TARGET").sha256")
log "готово: $TARGET ($(du -h "$TARGET" | cut -f1); приложение $(du -sh "$APP" | cut -f1))"
