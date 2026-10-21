# Образ LLM Budget Manager (процессы api и worker). Контекст сборки — корень репозитория:
#   docker build -f deploy/Dockerfile --target python-runtime -t bm:local .
# Стадии: node-build (SPA админки) → python-deps → python-runtime (+ /app/static/admin); tests — для make test-docker.
#
# Базовые образы закреплены по digest из аннотации «Версии и digest базовых образов» прогона CI 899cf1e
# (T099, 4Д1 (2); на 34d28d0 теги python и node указывали на другие digest); тег оставлен для читаемости.
# Обновление digest — только по журналу CI: шаг digests печатает текущий digest тегов (виден дрейф).
# Для закрытого контура: те же ARG указывают на зеркало, например --build-arg PYTHON_IMAGE=registry.local/python:3.13-slim
ARG PYTHON_IMAGE=python:3.13-slim@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f
ARG NODE_IMAGE=node:24-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205
ARG UV_VERSION=0.11.32

# ---------- SPA админки: npm ci строго по package-lock.json, затем vite build + check-dist ----------
FROM ${NODE_IMAGE} AS node-build
ENV npm_config_audit=false \
    npm_config_fund=false \
    npm_config_update_notifier=false
WORKDIR /src/app/frontend
COPY app/frontend/package.json app/frontend/package-lock.json ./
RUN npm ci
COPY app/frontend/ ./
# Токены дизайна (design/tokens.json) читаются плагином сборки по пути ../../design.
COPY design/tokens.json /src/design/tokens.json
RUN npm run build

# ---------- общая база со сборочным инструментом uv ----------
FROM ${PYTHON_IMAGE} AS python-base
ARG UV_VERSION
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
WORKDIR /src

# ---------- зависимости поставки (без dev), строго по uv.lock ----------
FROM python-base AS python-deps
COPY app/backend/pyproject.toml app/backend/uv.lock app/backend/
RUN uv sync --project app/backend --frozen --no-dev --no-install-project
COPY app/backend/bm app/backend/bm
RUN uv sync --project app/backend --frozen --no-dev --no-editable

# ---------- очищенный базовый образ: без pip и его SBOM (T099, 5Д3) ----------
# pip базового образа в рантайме не нужен (зависимости приходят готовым /app/.venv, healthcheck — только python).
# pip 26.2/26.2.1 вендорит certifi 2026.6.17 и несёт pip/_vendor/bom.cdx.json (CycloneDX). Trivy читает этот SBOM
# (пакеты с пустым FilePath) и не снимает находку, если файл удалён в верхнем слое: whiteout сопоставляется по
# FilePath (CI 92b3414). Поэтому чистка — в отдельной стадии, а python-runtime собирается FROM scratch копией её
# корня: слоёв базового образа в итоговом нет. Удаляются pip, wheel ensurepip, обёртки pip*; guard
# (deploy/image_guard.py, смонтирован только на время RUN) роняет сборку, если остались certifi, SBOM-файлы или
# архивы .whl/.zip/.egg/.pyz с certifi, и печатает все архивы.
FROM ${PYTHON_IMAGE} AS python-slim-clean
RUN groupadd --system --gid 10001 bm \
 && useradd --system --uid 10001 --gid bm --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin bm
RUN set -eu; \
    python -m pip uninstall --yes pip; \
    rm -rf /usr/local/lib/python3.*/ensurepip/_bundled; \
    rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.*; \
    if python -c 'import pip' 2>/dev/null; then echo 'pip остался в образе' >&2; exit 1; fi
RUN --mount=type=bind,source=deploy/image_guard.py,target=/tmp/image_guard.py python /tmp/image_guard.py /

# ---------- рантайм: без uv, без компиляторов, без слоёв базового образа, непривилегированный пользователь ----------
FROM scratch AS python-runtime
COPY --from=python-slim-clean / /
COPY --from=python-deps /app/.venv /app/.venv
COPY --from=node-build /src/app/frontend/dist /app/static/admin
COPY deploy/docker-entrypoint.sh /usr/local/bin/bm-entrypoint
COPY deploy/healthcheck.py /app/healthcheck.py
RUN chmod 0755 /usr/local/bin/bm-entrypoint && chmod 0644 /app/healthcheck.py
# ENV базового образа после FROM scratch не наследуется: PATH — как в python:3.13-slim (/usr/local/bin первым).
ENV PATH=/app/.venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UVICORN_WORKERS=2
# TZ процесса не задаём: периоды считаются явно в Europe/Moscow через zoneinfo + пакет tzdata (FR-014).
WORKDIR /app
USER bm:bm
EXPOSE 8080
# Healthcheck по умолчанию — для api (/healthz). В compose у api — /readyz, у worker — своя проверка (healthcheck.py).
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["bm-entrypoint"]
# api — через bm.serve (T098, 5Д1): TCP_NODELAY на принятых сокетах при нескольких воркерах; число воркеров —
# UVICORN_WORKERS (по умолчанию 2), без заголовка Server. Прямой `uvicorn … --workers` не используется (Д-1).
CMD ["python", "-m", "bm.serve", "--host", "0.0.0.0", "--port", "8080"]

# ---------- прогон тестов в контейнере (make test-docker) ----------
FROM python-base AS tests
RUN groupadd --system --gid 10001 bm \
 && useradd --system --uid 10001 --gid bm --create-home --shell /usr/sbin/nologin bm
COPY app/backend/pyproject.toml app/backend/uv.lock app/backend/
RUN uv sync --project app/backend --frozen --no-install-project
COPY app/backend app/backend
RUN uv sync --project app/backend --frozen
COPY pytest.ini 12_openapi.yaml ./
COPY tests tests
# deploy целиком: tests/deploy читает compose-файлы, .env.example и Dockerfile (статические проверки T087).
COPY deploy deploy
RUN chown -R bm:bm /src /app/.venv
ENV UV_CACHE_DIR=/tmp/uv-cache \
    SKIP_FRONTEND=1
USER bm:bm
CMD ["bash", "deploy/run-tests.sh", "full"]
