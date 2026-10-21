"""Статическая проверка поставки (T087; 14_tech_rulings.md, пакет 3, 3Д1(а)).

Разбирает вывод `docker compose config --format json` (интерполяция переменных уже выполнена) и проверяет
требования T087 без запуска контейнеров: сети, порт, healthcheck, restart, секреты через *_FILE, каталог метрик,
прокси импорта. Плюс требования к Dockerfile, .env.example и урезанию MCP (3К).

Нужен docker CLI с плагином compose (демон и реестры не нужны). Без него тесты compose пропускаются с причиной;
BM_REQUIRE_COMPOSE=1 (CI, T092) превращает пропуск в ошибку.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "deploy" / "docker-compose.yml"
COMPOSE_TEST_FILE = ROOT / "deploy" / "docker-compose.test.yml"
ENV_EXAMPLE = ROOT / "deploy" / ".env.example"
DOCKERFILE = ROOT / "deploy" / "Dockerfile"

SERVICES = {"api", "worker", "postgres", "backup"}
# Метки-значения: по ним видно, куда попадает каждая переменная, и что секреты не утекают в окружение.
SENTINEL_SECRETS = {
    "POSTGRES_PASSWORD": "sentinel-pg-password-0001",
    "SESSION_SECRET": "sentinel-session-secret-0002",
    "ADMIN_INITIAL_PASSWORD": "sentinel-admin-password-0003",
    "ANTHROPIC_ADMIN_KEY": "sentinel-anthropic-key-0004",
    "OPENAI_ADMIN_KEY": "sentinel-openai-key-0005",
    # Логин и пароль прокси в URL — секрет (14_tech_rulings.md, пакет 4, 4Д4): только файлом, только у worker.
    "IMPORT_HTTPS_PROXY": "http://proxy-user:proxy-pass@10.9.8.7:3128",
}
PROXY = SENTINEL_SECRETS["IMPORT_HTTPS_PROXY"]


def _docker_compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    # docker ищется в PATH, как в Makefile
    proc = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, timeout=30)  # noqa: S607
    return proc.returncode == 0


needs_compose = pytest.mark.skipif(
    os.environ.get("BM_REQUIRE_COMPOSE") != "1" and not _docker_compose_available(),
    reason="нет docker CLI с плагином compose: статическая проверка compose пропущена (в CI обязательна)",
)


def _compose_config(
    env_file: Path, compose_file: Path = COMPOSE_FILE, *extra: str
) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose", "-f", str(compose_file), "--env-file", str(env_file), "config", *extra]
    # аргументы — константы теста
    # Окружение процесса имеет приоритет над --env-file: убираем переменные, которые интерполирует compose-файл
    # (например, DATABASE_URL тестовой БД из make test), чтобы проверялось ровно то, что даёт env-файл.
    used = set(re.findall(r"\$\{([A-Z0-9_]+)", compose_file.read_text(encoding="utf-8")))
    env = {k: v for k, v in os.environ.items() if k not in used}
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)  # noqa: S603


@pytest.fixture(scope="module")
def env_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """.env.example с заполненными секретами-метками и нестандартными адресом, портом и прокси."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    values = {**SENTINEL_SECRETS, "BIND_ADDR": "10.1.2.3", "API_PORT": "18081", "IMPORT_HTTPS_PROXY": PROXY}
    lines = []
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        lines.append(f"{key}={values[key]}" if key in values else line)
    path = tmp_path_factory.mktemp("compose") / "test.env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def raw_config(env_file: Path) -> str:
    if os.environ.get("BM_REQUIRE_COMPOSE") != "1" and not _docker_compose_available():
        pytest.skip("нет docker CLI с плагином compose")
    proc = _compose_config(env_file, COMPOSE_FILE, "--format", "json")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture(scope="module")
def config(raw_config: str) -> dict[str, Any]:
    return json.loads(raw_config)


def _env(svc: dict[str, Any]) -> dict[str, str]:
    return {k: ("" if v is None else str(v)) for k, v in (svc.get("environment") or {}).items()}


# ---------------------------------------------------------------- docker compose config --quiet


@needs_compose
@pytest.mark.parametrize("compose_file", [COMPOSE_FILE, COMPOSE_TEST_FILE], ids=["main", "test"])
def test_compose_config_quiet_with_env_example(compose_file: Path) -> None:
    """Готовность T087 (3Д1(а)): `docker compose ... config --quiet` — exit 0 на шаблоне .env.example."""
    proc = _compose_config(ENV_EXAMPLE, compose_file, "--quiet")
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------- сервисы и сети


@needs_compose
def test_four_services(config: dict[str, Any]) -> None:
    assert set(config["services"]) == SERVICES


@needs_compose
def test_core_network_is_internal(config: dict[str, Any]) -> None:
    assert config["networks"]["core"].get("internal") is True
    assert not config["networks"]["edge"].get("internal"), "edge нужна для порта api и выхода worker"


@needs_compose
@pytest.mark.parametrize("service", ["postgres", "backup"])
def test_db_and_backup_only_in_core(config: dict[str, Any], service: str) -> None:
    svc = config["services"][service]
    assert set(svc["networks"]) == {"core"}, svc["networks"]
    assert not svc.get("ports"), f"{service} не публикует портов"


@needs_compose
@pytest.mark.parametrize("service", ["api", "worker"])
def test_app_in_core_and_edge(config: dict[str, Any], service: str) -> None:
    assert set(config["services"][service]["networks"]) == {"core", "edge"}


@needs_compose
def test_api_port_from_bind_addr_and_api_port(config: dict[str, Any]) -> None:
    ports = config["services"]["api"]["ports"]
    assert len(ports) == 1, ports
    p = ports[0]
    assert (p["host_ip"], str(p["published"]), int(p["target"])) == ("10.1.2.3", "18081", 8080), p
    assert '"${BIND_ADDR}:${API_PORT}:8080"' in COMPOSE_FILE.read_text(encoding="utf-8")
    assert all(not config["services"][s].get("ports") for s in SERVICES - {"api"})


@needs_compose
@pytest.mark.parametrize("service", sorted(SERVICES))
def test_healthcheck_and_restart(config: dict[str, Any], service: str) -> None:
    svc = config["services"][service]
    hc = svc.get("healthcheck") or {}
    assert hc.get("test") and not hc.get("disable"), f"{service}: нет healthcheck"
    assert svc.get("restart") == "unless-stopped", f"{service}: restart={svc.get('restart')!r}"


@needs_compose
@pytest.mark.parametrize("service", ["api", "worker", "backup"])
def test_waits_for_healthy_postgres(config: dict[str, Any], service: str) -> None:
    dep = config["services"][service]["depends_on"]["postgres"]
    assert dep["condition"] == "service_healthy"


@needs_compose
def test_postgres_18_user_and_db_bm(config: dict[str, Any]) -> None:
    pg = config["services"]["postgres"]
    assert pg["image"] == "postgres:18"
    env = _env(pg)
    assert env["POSTGRES_USER"] == "bm" and env["POSTGRES_DB"] == "bm"


@needs_compose
@pytest.mark.parametrize("service", ["api", "worker"])
def test_app_image_built_from_runtime_stage(config: dict[str, Any], service: str) -> None:
    build = config["services"][service]["build"]
    assert build["target"] == "python-runtime"
    assert Path(build["dockerfile"]).name == "Dockerfile"


# ---------------------------------------------------------------- секреты через *_FILE


@needs_compose
def test_secret_values_never_in_rendered_config(raw_config: str) -> None:
    """Значения секретов из .env не попадают ни в одно окружение: compose отдаёт их файлами в /run/secrets."""
    leaked = [name for name, value in SENTINEL_SECRETS.items() if value in raw_config]
    assert not leaked, f"значения секретов в конфигурации: {leaked}"


@needs_compose
@pytest.mark.parametrize("service", sorted(SERVICES))
def test_secrets_passed_as_files(config: dict[str, Any], service: str) -> None:
    svc = config["services"][service]
    env = _env(svc)
    for name in SENTINEL_SECRETS:
        assert name not in env, f"{service}: секрет {name} в окружении — нужно {name}_FILE"
    mounted = {s["target"] for s in svc.get("secrets", [])}
    for key, value in env.items():
        if key.endswith("_FILE"):
            assert value.startswith("/run/secrets/"), f"{service}: {key}={value}"
            assert value in mounted, f"{service}: {key} указывает на несмонтированный секрет {value}"
    assert "POSTGRES_PASSWORD_FILE" in env, f"{service}: пароль БД — через POSTGRES_PASSWORD_FILE"


@needs_compose
def test_secret_sources_are_env_vars(config: dict[str, Any]) -> None:
    secrets = config["secrets"]
    assert {v["environment"] for v in secrets.values()} == set(SENTINEL_SECRETS)


@needs_compose
def test_database_url_has_no_password(config: dict[str, Any]) -> None:
    for service in ("api", "worker"):
        url = _env(config["services"][service])["DATABASE_URL"]
        assert re.match(r"^postgresql://bm@postgres:5432/bm$", url), url


@needs_compose
def test_least_privilege_secrets(config: dict[str, Any]) -> None:
    """Ключи провайдеров — только у worker (импорт), пароль первого администратора — только у api."""
    api = {s["source"] for s in config["services"]["api"]["secrets"]}
    worker = {s["source"] for s in config["services"]["worker"]["secrets"]}
    assert "anthropic_admin_key" not in api and "openai_admin_key" not in api
    assert {"anthropic_admin_key", "openai_admin_key"} <= worker
    assert "admin_initial_password" in api and "admin_initial_password" not in worker


# ---------------------------------------------------------------- метрики и прокси


@needs_compose
def test_prometheus_multiproc_dir_on_tmpfs_only_at_api(config: dict[str, Any]) -> None:
    api = config["services"]["api"]
    path = _env(api)["PROMETHEUS_MULTIPROC_DIR"]
    tmpfs = [t.split(":", 1)[0] for t in api.get("tmpfs", [])]
    assert path in tmpfs, f"{path} не на tmpfs: {tmpfs}"
    for service in SERVICES - {"api"}:
        svc = config["services"][service]
        assert "PROMETHEUS_MULTIPROC_DIR" not in _env(svc), f"{service}: каталог метрик только у api"
        assert not any(t.split(":", 1)[0] == path for t in svc.get("tmpfs", []) or [])
        assert not any(path in str(v) for v in svc.get("volumes", []) or [])


@needs_compose
def test_import_https_proxy_passed_to_worker_as_secret_file(config: dict[str, Any], raw_config: str) -> None:
    """4Д4: прокси — compose secret только у worker (IMPORT_HTTPS_PROXY_FILE); значения нет ни в одном окружении."""
    worker = config["services"]["worker"]
    env = _env(worker)
    assert env["IMPORT_HTTPS_PROXY_FILE"] == "/run/secrets/import_https_proxy"
    assert "IMPORT_HTTPS_PROXY" not in env
    assert PROXY not in json.dumps(env) and "proxy-pass" not in raw_config
    assert config["secrets"]["import_https_proxy"]["environment"] == "IMPORT_HTTPS_PROXY"
    # Импорт выполняет только worker (runImport в api ставит задачу в очередь): у api и остальных прокси нет.
    for service in SERVICES - {"worker"}:
        svc = config["services"][service]
        assert not any(k.startswith("IMPORT_HTTPS_PROXY") for k in _env(svc)), service
        assert "import_https_proxy" not in {s["source"] for s in svc.get("secrets", [])}, service


def test_entrypoint_drops_proxy_file_when_secret_is_empty(tmp_path: Path) -> None:
    """Пустой IMPORT_HTTPS_PROXY в .env — compose файл не создаёт; entrypoint снимает *_FILE (прямой выход)."""
    present = tmp_path / "proxy"
    present.write_text("http://10.0.0.1:3128", encoding="utf-8")
    entrypoint = ROOT / "deploy" / "docker-entrypoint.sh"
    for path, expected in ((tmp_path / "missing", ""), (present, str(present))):
        env = {"PATH": "/usr/bin:/bin", "IMPORT_HTTPS_PROXY_FILE": str(path)}
        # аргументы — константы теста
        proc = subprocess.run(  # noqa: S603
            ["sh", str(entrypoint), "sh", "-c", 'printf %s "${IMPORT_HTTPS_PROXY_FILE:-}"'],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == expected


@needs_compose
@pytest.mark.parametrize("service", ["api", "worker"])
def test_session_cookie_secure_default_false(config: dict[str, Any], service: str) -> None:
    assert _env(config["services"][service])["SESSION_COOKIE_SECURE"] == "false"


# ---------------------------------------------------------------- .env.example


def _env_example_keys() -> dict[str, str]:
    out = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def test_env_example_covers_every_compose_variable() -> None:
    used = set(re.findall(r"\$\{([A-Z0-9_]+)", COMPOSE_FILE.read_text(encoding="utf-8")))
    # Переопределения образов для зеркала закрытого контура — необязательны, описаны в admin-guide.
    optional = {"BM_IMAGE", "POSTGRES_IMAGE"}
    missing = used - optional - set(_env_example_keys())
    assert not missing, f"нет в deploy/.env.example: {sorted(missing)}"


def test_env_example_session_cookie_secure_and_proxy() -> None:
    keys = _env_example_keys()
    assert keys["SESSION_COOKIE_SECURE"] == "false"
    assert keys["IMPORT_HTTPS_PROXY"] == ""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "true — если админка открыта через HTTPS" in text
    assert "пусто — прямой выход к API провайдеров" in text
    assert "стандартные HTTP(S)_PROXY утилита игнорирует" in text
    assert "IMPORT_HTTPS_PROXY_FILE" in text, "в шаблоне сказано, что прокси передаётся worker файлом"


def test_env_example_has_no_secret_values() -> None:
    keys = _env_example_keys()
    for name in [*SENTINEL_SECRETS, "WEBHOOK_URL", "BM_ADMIN_PASSWORD"]:
        assert keys.get(name, "") == "", f"{name} в шаблоне должен быть пустым"


# ---------------------------------------------------------------- Dockerfile и урезание MCP (3К)


def test_dockerfile_builds_spa_into_static_admin() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^FROM \$\{NODE_IMAGE\} AS node-build$", text, re.M)
    assert "npm ci" in text and "npm run build" in text
    assert "COPY --from=node-build /src/app/frontend/dist /app/static/admin" in text


def test_dockerfile_runtime_non_root_with_healthcheck() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    runtime = text.split("AS python-runtime", 1)[1].split("\nFROM ", 1)[0]
    assert "--uid 10001" in runtime and "USER bm:bm" in runtime
    assert "HEALTHCHECK" in runtime
    assert 'ENTRYPOINT ["bm-entrypoint"]' in runtime


def test_mcp_extra_removed() -> None:
    """Урезание 🔻1 (14_checkpoint_week5.md): extra mcp нет ни в pyproject, ни в uv.lock, ни в Dockerfile."""
    pyproject = (ROOT / "app" / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "app" / "backend" / "uv.lock").read_text(encoding="utf-8")
    assert "optional-dependencies" not in pyproject and '"mcp' not in pyproject
    assert 'name = "mcp"' not in lock
    assert "UV_EXTRAS" not in DOCKERFILE.read_text(encoding="utf-8")
