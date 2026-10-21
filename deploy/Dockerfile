# Образ LLM Budget Manager (процессы api и worker). Контекст сборки — корень репозитория:
#   docker build -f deploy/Dockerfile --target python-runtime -t bm:local .
# Стадии: node-build (SPA админки) → python-deps → python-runtime (+ /app/static/admin); tests — для make test-docker.
#
# Базовые образы закреплены по тегу. [Гипотеза] Digest (@sha256:...) пока не закреплён: реестры из среды
# разработки закрыты (403), а журнал прогона CI eb17642 недоступен (14_tech_rulings.md, пакет 4, 4Д1).
# CI печатает digest каждого базового образа аннотацией ::notice «Digest базовых образов»; после прогона
# значения переносятся сюда в виде python:3.13-slim@sha256:<digest> (тег оставляется для читаемости).
# Для закрытого контура: те же ARG указывают на зеркало, например --build-arg PYTHON_IMAGE=registry.local/python:3.13-slim
ARG PYTHON_IMAGE=python:3.13-slim
ARG NODE_IMAGE=node:24-slim
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

# ---------- рантайм: без uv, без компиляторов, непривилегированный пользователь ----------
FROM ${PYTHON_IMAGE} AS python-runtime
RUN groupadd --system --gid 10001 bm \
 && useradd --system --uid 10001 --gid bm --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin bm
COPY --from=python-deps /app/.venv /app/.venv
COPY --from=node-build /src/app/frontend/dist /app/static/admin
COPY deploy/docker-entrypoint.sh /usr/local/bin/bm-entrypoint
COPY deploy/healthcheck.py /app/healthcheck.py
RUN chmod 0755 /usr/local/bin/bm-entrypoint && chmod 0644 /app/healthcheck.py
ENV PATH=/app/.venv/bin:$PATH \
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
CMD ["sh", "-c", "exec uvicorn bm.main:create_app --factory --host 0.0.0.0 --port 8080 --workers ${UVICORN_WORKERS} --no-server-header"]

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
