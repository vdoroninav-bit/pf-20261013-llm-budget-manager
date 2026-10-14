# Образ LLM Budget Manager (процессы api и worker). Контекст сборки — корень репозитория:
#   docker build -f deploy/Dockerfile --target python-runtime -t bm:local .
# T001: стадии python-deps, python-runtime, tests. T087 добавит стадию node (сборка SPA → /app/static/admin).
#
# Базовый образ закреплён по тегу; закрепление по digest (@sha256:...) — в T087, когда будет доступ к реестру.
ARG PYTHON_IMAGE=python:3.13-slim
ARG UV_VERSION=0.11.32

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
# Пример: --build-arg UV_EXTRAS="--extra mcp" включает MCP-сервер (P3).
ARG UV_EXTRAS=""
COPY app/backend/pyproject.toml app/backend/uv.lock app/backend/
RUN uv sync --project app/backend --frozen --no-dev --no-install-project ${UV_EXTRAS}
COPY app/backend/bm app/backend/bm
RUN uv sync --project app/backend --frozen --no-dev --no-editable ${UV_EXTRAS}

# ---------- рантайм: без uv, без компиляторов, непривилегированный пользователь ----------
FROM ${PYTHON_IMAGE} AS python-runtime
RUN groupadd --system --gid 10001 bm \
 && useradd --system --uid 10001 --gid bm --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin bm
COPY --from=python-deps /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UVICORN_WORKERS=2
# TZ процесса не задаём: периоды считаются явно в Europe/Moscow через zoneinfo + пакет tzdata (FR-014).
WORKDIR /app
USER bm:bm
EXPOSE 8080
# /healthz реализуется в T015 (bm/http/routers/system.py). Для worker healthcheck переопределяется в compose (T087).
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"]
CMD ["sh", "-c", "exec uvicorn bm.main:create_app --factory --host 0.0.0.0 --port 8080 --workers ${UVICORN_WORKERS} --no-server-header"]

# ---------- прогон тестов в контейнере (make test-docker) ----------
FROM python-base AS tests
RUN groupadd --system --gid 10001 bm \
 && useradd --system --uid 10001 --gid bm --create-home --shell /usr/sbin/nologin bm
COPY app/backend/pyproject.toml app/backend/uv.lock app/backend/
RUN uv sync --project app/backend --frozen --all-extras --no-install-project
COPY app/backend app/backend
RUN uv sync --project app/backend --frozen --all-extras
COPY pytest.ini 12_openapi.yaml ./
COPY tests tests
COPY deploy/run-tests.sh deploy/run-tests.sh
RUN chown -R bm:bm /src /app/.venv
ENV UV_CACHE_DIR=/tmp/uv-cache \
    SKIP_FRONTEND=1
USER bm:bm
CMD ["bash", "deploy/run-tests.sh", "full"]
