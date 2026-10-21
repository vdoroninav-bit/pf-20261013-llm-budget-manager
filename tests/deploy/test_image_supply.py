"""Статические требования к образу и цепочке поставки (T098, T099; 14_tech_rulings.md, пакет 5, 5Д1, 5Д3).

- T099 (5Д3): в стадии python-runtime нет pip базового образа (вложенный в pip certifi 2026.6.17 —
  Trivy CRITICAL GHSA-6q48-6c7w-9gw2 на CI 34d28d0 и 899cf1e); исключений для Trivy нет; базовые образы и
  инструменты CI закреплены по digest из аннотации прогона 899cf1e (4Д1); путь найденного certifi — в ::notice.
- 5Д5: npm audit гейтует только critical; moderate — предупреждение (WARN) с перечнем advisory.
- 5Д8 (1): цель `load` в Makefile — в зоне DevOps, поведение прежнее (tests/load/run_load.sh).
- T098 (5Д1): CMD образа и deploy/local/stack.sh запускают api через `python -m bm.serve`
  (TCP_NODELAY при нескольких воркерах), а не `uvicorn … --workers` напрямую.

Только чтение файлов: Docker, реестры и сеть не нужны.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "Dockerfile"
STACK_SH = ROOT / "deploy" / "local" / "stack.sh"
CHECK_SH = ROOT / "deploy" / "ci" / "check.sh"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
MAKEFILE = ROOT / "Makefile"
COMPOSE_TEST = ROOT / "deploy" / "docker-compose.test.yml"

# Аннотация «Версии и digest базовых образов» прогона CI 899cf1e (на 34d28d0 python и node были другими:
# теги python:3.13-slim и node:24-slim сдвинулись между прогонами).
DIGEST = {
    "python": "sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f",
    "node": "sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205",
    "postgres": "sha256:1267c7e692de235fda2a50dbcdbcbca4672ea6a0298576a6bd9d07cafc319b32",
    "trivy": "sha256:b88012e2a0a309d6a8a00463d4e63e5e513377fb74eccbc8f9b0f8f81940ebeb",
}


def _code(text: str) -> str:
    """Текст без строк-комментариев (# …): требования проверяются по исполняемым строкам."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _runtime_stage() -> str:
    text = _code(DOCKERFILE.read_text(encoding="utf-8"))
    return text.split("AS python-runtime", 1)[1].split("\nFROM ", 1)[0]


def _joined(text: str) -> str:
    """Склеивает продолжения строк (`\\` + перевод строки) — одна инструкция на строку."""
    return re.sub(r"\\\n\s*", " ", text)


# ---------------------------------------------------------------- T099: pip базового образа


def test_runtime_stage_uninstalls_base_image_pip() -> None:
    runtime = _joined(_runtime_stage())
    assert re.search(r"python -m pip uninstall --yes pip\b", runtime), "pip базового образа не удалён"
    # копия pip для ensurepip (wheel с тем же вложенным certifi) и обёртки pip* тоже удаляются
    assert "/usr/local/lib/python3.*/ensurepip/_bundled" in runtime
    assert re.search(r"rm -f[^\n]*/usr/local/bin/pip\b", runtime)


def test_runtime_stage_fails_build_if_pip_or_foreign_certifi_left() -> None:
    runtime = _joined(_runtime_stage())
    assert re.search(r"if python -c ['\"]import pip['\"][^\n]*then[^\n]*exit 1", runtime), (
        "сборка должна падать, если pip остался"
    )
    assert re.search(r"find / -xdev[^\n]*-name certifi -o -name 'certifi-\*'", runtime), (
        "сборка должна падать, если certifi есть вне /app/.venv"
    )
    # проверка до копирования окружения поставки: в /app/.venv certifi из uv.lock законен
    lines = runtime.splitlines()
    guard = next(i for i, line in enumerate(lines) if "find / -xdev" in line and "certifi" in line)
    venv = next(i for i, line in enumerate(lines) if line.startswith("COPY --from=python-deps /app/.venv"))
    assert guard < venv


def test_runtime_stage_installs_nothing_with_pip() -> None:
    runtime = _joined(_runtime_stage())
    assert not re.search(r"\bpip3? install\b", runtime)
    assert "uv " not in runtime and "uv\n" not in runtime


def test_no_trivy_exceptions() -> None:
    """5Д3: исключения для CRITICAL запрещены — ни .trivyignore, ни --ignore-unfixed, порог не понижен."""
    assert not list(ROOT.glob("**/.trivyignore*")), ".trivyignore запрещён"
    check = _code(CHECK_SH.read_text(encoding="utf-8"))
    ci = _code(CI_YML.read_text(encoding="utf-8"))
    for text in (check, ci):
        assert "--ignore-unfixed" not in text
        assert "--ignorefile" not in text and "--ignore-policy" not in text
    gate = [line for line in check.splitlines() if "--exit-code 1" in line]
    assert len(gate) == 1 and "--severity CRITICAL" in gate[0], gate


def test_check_always_writes_trivy_json_for_ci_diagnostics() -> None:
    """Путь certifi (Target, FilePath) — из JSON Trivy при любом исходе гейта: и при находке (источник),
    и без неё (подтверждение, что осталась только копия из /app/.venv)."""
    check = _joined(_code(CHECK_SH.read_text(encoding="utf-8")))
    json_lines = [
        line
        for line in check.splitlines()
        if re.search(r"image --quiet --format json --list-all-pkgs --output [^\n]*trivy-critical\.json", line)
    ]
    assert len(json_lines) == 1, json_lines
    # вне ветки FAIL: строка идёт до гейта --exit-code 1
    lines = check.splitlines()
    assert lines.index(json_lines[0]) < next(i for i, line in enumerate(lines) if "--exit-code 1" in line)


def test_ci_emits_certifi_path_notice_for_image_and_base_image() -> None:
    ci = _joined(CI_YML.read_text(encoding="utf-8"))
    step = ci.split("name: Trivy — путь certifi", 1)[1].split("\n      - name:", 1)[0]
    # не только при падении make check: подтверждение источника нужно и при зелёном Trivy
    assert "steps.check.outcome == 'failure'" not in step
    assert "steps.build.outcome == 'success'" in step
    # базовый образ — тот же, что ARG PYTHON_IMAGE в Dockerfile (по digest)
    assert "ARG PYTHON_IMAGE=" in step and "trivy-base.json" in step
    assert re.search(
        r"deploy/ci/gha\.py trivy --pkg certifi --json deploy/ci/out/trivy-critical\.json"
        r"\s+--json deploy/ci/out/trivy-base\.json",
        step,
    )


# ---------------------------------------------------------------- 5Д5: npm audit — гейт только critical


def test_npm_audit_gates_only_critical_and_warns_with_advisories() -> None:
    check = _joined(_code(CHECK_SH.read_text(encoding="utf-8")))
    gate = [line for line in check.splitlines() if "audit --omit=dev --audit-level=" in line]
    assert len(gate) == 1 and "--audit-level=critical" in gate[0], gate
    # перечень advisory — из npm audit --json, результат WARN (не FAIL)
    assert re.search(r"audit --omit=dev --json\b", check)
    assert "deploy/ci/npm_advisories.py" in check or '"$CI/npm_advisories.py"' in check
    warn = re.search(r"^warn\(\) \{([^}]*)\}", check, re.M)
    assert warn and "FAILED" not in warn.group(1), "WARN не должен ронять make check"
    assert re.search(r"\bwarn \"npm: audit", check)


# ---------------------------------------------------------------- 5Д8 (1): цель load


def test_makefile_load_target_owned_by_devops_unchanged() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert re.search(r"^\.PHONY:[^\n]*(?:\\\n[^\n]*)*\bload\b", text, re.M)
    target = re.search(r"^load: sync ## [^\n]*\n((?:\t[^\n]*\n)+)", text, re.M)
    assert target, "цель load"
    assert target.group(1).strip() == "$(STACK_ENV) bash tests/load/run_load.sh"


# ---------------------------------------------------------------- T099: digest базовых образов


def test_dockerfile_base_images_pinned_by_digest() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(rf"^ARG PYTHON_IMAGE=python:3\.13-slim@{DIGEST['python']}$", text, re.M)
    assert re.search(rf"^ARG NODE_IMAGE=node:24-slim@{DIGEST['node']}$", text, re.M)


def test_ci_images_pinned_by_digest() -> None:
    ci = CI_YML.read_text(encoding="utf-8")
    assert re.search(rf'^\s+NODE_IMAGE: "node:24-slim@{DIGEST["node"]}"$', ci, re.M)
    assert re.search(rf'^\s+TRIVY_IMAGE: "aquasec/trivy:0\.58\.0@{DIGEST["trivy"]}"$', ci, re.M)
    assert re.search(rf"^\s+image: postgres:18@{DIGEST['postgres']}$", ci, re.M)
    test_compose = COMPOSE_TEST.read_text(encoding="utf-8")
    assert re.search(rf"^\s+image: postgres:18@{DIGEST['postgres']}$", test_compose, re.M)


# ---------------------------------------------------------------- T098: запуск api через bm.serve


def _cmd() -> str:
    cmds = [line for line in _runtime_stage().splitlines() if line.startswith("CMD ")]
    assert len(cmds) == 1, cmds
    return cmds[0]


def test_image_cmd_runs_bm_serve() -> None:
    cmd = _cmd()
    assert re.fullmatch(r'CMD \["python", "-m", "bm\.serve"(, "[^"]+")*\]', cmd), cmd
    assert "uvicorn" not in cmd and "--workers" not in cmd
    # число воркеров — из UVICORN_WORKERS (по умолчанию 2), его читает bm.serve
    assert re.search(r"^\s*UVICORN_WORKERS=2$", _runtime_stage(), re.M)


def test_stack_sh_runs_bm_serve_not_uvicorn_workers() -> None:
    code = _joined(_code(STACK_SH.read_text(encoding="utf-8")))
    assert re.search(r'spawn api [^\n]*"\$VENV/bin/python" -m bm\.serve\b', code)
    assert not re.search(r"\buvicorn\b[^\n]*--workers", code)
    assert "UVICORN_WORKERS=$WORKERS" in code


def test_no_direct_uvicorn_workers_in_dockerfile() -> None:
    code = _joined(_code(DOCKERFILE.read_text(encoding="utf-8")))
    assert not re.search(r"\buvicorn\b[^\n]*--workers", code)
