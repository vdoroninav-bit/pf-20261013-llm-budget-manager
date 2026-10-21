"""Аннотации CI (T092; 14_tech_rulings.md, пакет 4, 4Д1, 4Д3): `deploy/ci/gha.py`.

Журнал прогона GitHub Actions из среды оркестратора недоступен (blob-хранилище закрыто прокси), доступны
только статусы шагов и аннотации check-run. Поэтому каждый гейт CI запускается через `gha.py run`: при
падении в журнал уходит `::error title=<шаг>::<хвост вывода>` — переводы строк экранированы (%0A), не больше
~4 КБ на аннотацию и не больше 10 аннотаций на задачу, значения секретов стенда заменены на `***`.
Сводка гейтов — одна аннотация `::notice`.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "ci" / "gha.py"


def _gha(tmp_path: Path, *args: str, env: dict[str, str] | None = None, stdin: str | None = None):
    base = {
        "PATH": "/usr/bin:/bin",
        "GHA_STATE_DIR": str(tmp_path / "state"),
        "GHA_SECRETS_FILE": str(tmp_path / "secrets.env"),
        # без стека: диагностика compose подменяется командой, которая печатает фиксированный текст
        "BM_COMPOSE": f"{sys.executable} {tmp_path / 'fake_compose.py'}",
    }
    # аргументы — константы теста
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**base, **(env or {})},
        input=stdin,
        cwd=tmp_path,
    )


def _commands(stdout: str, kind: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith(f"::{kind} ")]


def _unescape(data: str) -> str:
    return data.replace("%0A", "\n").replace("%0D", "\r").replace("%25", "%")


def test_success_passes_output_and_records_gate(tmp_path: Path) -> None:
    proc = _gha(tmp_path, "run", "--title", "make lint", "--", "sh", "-c", "echo все хорошо")
    assert proc.returncode == 0, proc.stderr
    assert "все хорошо" in proc.stdout
    assert not _commands(proc.stdout, "error")
    rows = (tmp_path / "state" / "gates.tsv").read_text(encoding="utf-8").splitlines()
    assert rows[0].split("\t")[:3] == ["make lint", "PASS", "0"]


def test_failure_emits_error_with_escaped_tail_and_same_exit_code(tmp_path: Path) -> None:
    proc = _gha(
        tmp_path, "run", "--title", "make smoke", "--", "sh", "-c", "echo 'строка 1'; echo '50%: x'; exit 3"
    )
    assert proc.returncode == 3
    errors = _commands(proc.stdout, "error")
    assert len(errors) == 1
    head, data = errors[0].split("::", 2)[1:]
    assert head == "error title=make smoke (exit 3)"
    assert "\n" not in data and "%0A" in data
    assert "строка 1\n50%: x" in _unescape(data)
    assert "FAIL\t3" in (tmp_path / "state" / "gates.tsv").read_text(encoding="utf-8")


def test_title_properties_are_escaped(tmp_path: Path) -> None:
    proc = _gha(tmp_path, "run", "--title", "a: b, c", "--", "sh", "-c", "exit 1")
    assert _commands(proc.stdout, "error")[0].startswith("::error title=a%3A b%2C c (exit 1)::")


def test_long_output_truncated_to_limit_utf8_safe(tmp_path: Path) -> None:
    script = 'i=0; while [ $i -lt 3000 ]; do echo "ж-строка-$i"; i=$((i+1)); done; exit 1'
    proc = _gha(tmp_path, "run", "--title", "t", "--", "sh", "-c", script)
    data = _commands(proc.stdout, "error")[0].split("::", 2)[2]
    text = _unescape(data)
    assert len(text.encode("utf-8")) <= 4096
    assert text.startswith("…") and text.rstrip().endswith("ж-строка-2999")


def test_pytest_short_summary_is_preferred(tmp_path: Path) -> None:
    out = "\\n".join(
        ["x" * 200] * 40
        + [
            "=========================== short test summary info ============================",
            "FAILED tests/smoke/test_smoke.py::test_admin_spa_served_from_image - AssertionError: /admin/: 404",
            "==================== 1 failed, 9 passed in 3.10s ====================",
        ]
    )
    proc = _gha(tmp_path, "run", "--title", "make smoke", "--", "sh", "-c", f"printf '{out}\\n'; exit 1")
    text = _unescape(_commands(proc.stdout, "error")[0].split("::", 2)[2])
    assert text.startswith("=== short test summary info")
    assert "test_admin_spa_served_from_image - AssertionError: /admin/: 404" in text
    assert "x" * 200 not in text


def test_secrets_from_env_file_and_urls_are_masked(tmp_path: Path) -> None:
    (tmp_path / "secrets.env").write_text(
        "POSTGRES_PASSWORD=pg-secret-123\nBM_ADMIN_PASSWORD=adm-secret-456\nAPI_PORT=8080\n", encoding="utf-8"
    )
    script = "echo pg-secret-123 adm-secret-456 env-secret-789 http://u:p4ss@proxy:3128 port 8080; exit 1"
    proc = _gha(
        tmp_path, "run", "--title", "t", "--", "sh", "-c", script, env={"SESSION_SECRET": "env-secret-789"}
    )
    lines = _commands(proc.stdout, "error")
    data = lines[0]
    for secret in ("pg-secret-123", "adm-secret-456", "env-secret-789", "p4ss"):
        assert secret not in data
    assert "http://u:***@proxy:3128" in data and "port 8080" in data
    # маски для раннера печатаются до аннотации (::add-mask::), значения — только в этой команде
    masks = [line for line in proc.stdout.splitlines() if line.startswith("::add-mask::")]
    assert {m.split("::", 2)[2] for m in masks} >= {"pg-secret-123", "adm-secret-456", "env-secret-789"}
    assert proc.stdout.index("::add-mask::") < proc.stdout.index("::error ")


def test_annotation_budget_ten_per_job_with_room_for_notices(tmp_path: Path) -> None:
    for i in range(12):
        _gha(tmp_path, "run", "--title", f"gate {i}", "--", "sh", "-c", "exit 1")
    state = tmp_path / "state"
    proc = _gha(tmp_path, "summary", env={"STEPS_JSON": "{}"})
    proc2 = _gha(tmp_path, "notice", "--title", "digest", stdin="python@sha256:abc\n")
    proc3 = _gha(tmp_path, "notice", "--title", "лишняя", stdin="x\n")
    used = int((state / "annotations").read_text(encoding="utf-8"))
    assert used == 10
    assert _commands(proc.stdout, "notice") and _commands(proc2.stdout, "notice")
    assert not _commands(proc3.stdout, "notice"), "11-я аннотация не печатается"
    assert "лимит аннотаций" in proc3.stdout


def test_stack_failure_adds_compose_state_annotation(tmp_path: Path) -> None:
    (tmp_path / "fake_compose.py").write_text(
        "import sys, json\n"
        "if 'ps' in sys.argv:\n"
        "    print(json.dumps({'Service': 'api', 'State': 'running', 'Health': 'unhealthy', 'ExitCode': 0}))\n"
        "    print(json.dumps({'Service': 'postgres', 'State': 'running', 'Health': 'healthy', 'ExitCode': 0}))\n"
        "elif 'logs' in sys.argv:\n"
        "    print('api-1  | readyz: fail ' + ' '.join(a for a in sys.argv if a in ('api', 'postgres')))\n",
        encoding="utf-8",
    )
    proc = _gha(tmp_path, "run", "--title", "make up", "--stack", "--", "sh", "-c", "exit 2")
    errors = _commands(proc.stdout, "error")
    assert len(errors) == 2, proc.stdout
    title, data = errors[1].split("::", 2)[1:]
    assert title == "error title=make up%3A состояние стека"
    text = _unescape(data)
    assert "api running unhealthy" in text and "postgres running healthy" in text
    assert "readyz: fail api" in text and "readyz: fail api postgres" not in text


def test_summary_lists_steps_gates_and_sbom_hashes(tmp_path: Path) -> None:
    _gha(tmp_path, "run", "--title", "make up", "--", "sh", "-c", "exit 0")
    _gha(tmp_path, "run", "--title", "make smoke", "--", "sh", "-c", "exit 1")
    out = tmp_path / "out"
    out.mkdir()
    (out / "sbom-python.cdx.json").write_text("{}", encoding="utf-8")
    (out / "summary.txt").write_text("OK    a\nFAIL  trivy: есть critical\n", encoding="utf-8")
    steps = {
        "up": {"outcome": "success"},
        "smoke": {"outcome": "failure"},
        "failover": {"outcome": "skipped"},
    }
    proc = _gha(tmp_path, "summary", "--out", str(out), env={"STEPS_JSON": json.dumps(steps)})
    notices = _commands(proc.stdout, "notice")
    assert len(notices) == 1
    text = _unescape(notices[0].split("::", 2)[2])
    assert "make up: PASS" in text and "make smoke: FAIL (exit 1" in text
    assert "failover: skipped" in text
    assert (
        "sbom-python.cdx.json sha256=44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a" in text
    )
    assert "FAIL  trivy: есть critical" in text


@pytest.mark.parametrize("bad", [["run"], ["run", "--title", "t"], ["nope"]])
def test_usage_errors(tmp_path: Path, bad: list[str]) -> None:
    assert _gha(tmp_path, *bad).returncode == 2


# ---------------------------------------------------------------- источник находки Trivy (T099, 5Д3)

TRIVY_JSON = {
    "SchemaVersion": 2,
    "ArtifactName": "bm:local",
    "Results": [
        {"Target": "bm:local (debian 13.7)", "Class": "os-pkgs", "Type": "debian", "Packages": []},
        {
            "Target": "Python",
            "Class": "lang-pkgs",
            "Type": "python-pkg",
            "Packages": [
                {
                    "Name": "certifi",
                    "Version": "2026.6.17",
                    "FilePath": "usr/local/lib/python3.13/site-packages/pip/_vendor/certifi/x",
                },
                {
                    "Name": "certifi",
                    "Version": "2026.10.19",
                    "FilePath": "app/.venv/lib/python3.13/site-packages/certifi-2026.10.19.dist-info/METADATA",
                },
                {"Name": "h11", "Version": "0.16.0", "FilePath": "app/.venv/lib/h11/METADATA"},
            ],
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "GHSA-6q48-6c7w-9gw2",
                    "PkgName": "certifi",
                    "InstalledVersion": "2026.6.17",
                    "FixedVersion": "2026.10.19",
                    "Severity": "CRITICAL",
                }
            ],
        },
    ],
}


BASE_JSON = {
    "SchemaVersion": 2,
    "ArtifactName": "python:3.13-slim@sha256:cf50",
    "Results": [
        {
            "Target": "Python",
            "Class": "lang-pkgs",
            "Type": "python-pkg",
            "Packages": [
                {
                    "Name": "pip",
                    "Version": "26.2.1",
                    "FilePath": "usr/local/lib/python3.13/site-packages/pip-26.2.1",
                },
            ],
        }
    ],
}


def _notice(tmp_path: Path, *reports: dict, pkg: str | None = "certifi") -> tuple[str, str]:
    args = ["trivy"] + (["--pkg", pkg] if pkg else [])
    for i, data in enumerate(reports):
        path = tmp_path / f"r{i}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        args += ["--json", str(path)]
    proc = _gha(tmp_path, *args)
    assert proc.returncode == 0, proc.stderr
    assert not _commands(proc.stdout, "error"), "диагностика — notice, не error"
    notices = _commands(proc.stdout, "notice")
    assert len(notices) == 1, proc.stdout
    title, data = notices[0][2:].split("::", 1)
    return title, _unescape(data)


def test_trivy_notice_with_target_pkgpath_filepath_and_all_installs(tmp_path: Path) -> None:
    title, text = _notice(tmp_path, TRIVY_JSON, BASE_JSON)
    assert title == "notice title=Trivy: CRITICAL и путь certifi".replace(":", "%3A")
    assert "bm:local: CRITICAL 1" in text
    assert "Target=Python Class=lang-pkgs Type=python-pkg" in text
    assert "GHSA-6q48-6c7w-9gw2 certifi 2026.6.17 -> 2026.10.19" in text
    # PkgPath нет в находке — FilePath берётся из Packages по имени и версии
    assert "PkgPath=- FilePath=usr/local/lib/python3.13/site-packages/pip/_vendor/certifi/x" in text
    # все установки certifi в образе — чтобы отличить копию pip от /app/.venv
    assert (
        "certifi 2026.10.19 Target=Python FilePath=app/.venv/lib/python3.13/site-packages/certifi-2026.10.19"
        in text
    )
    assert "h11" not in text
    # второй отчёт — базовый образ: находок нет, certifi не найден (pip вендорит без метаданных)
    assert "python:3.13-slim@sha256:cf50: CRITICAL 0" in text
    assert "certifi: не найден" in text


def test_trivy_notice_prefers_pkgpath_and_reports_clean_image(tmp_path: Path) -> None:
    data = json.loads(json.dumps(TRIVY_JSON))
    data["Results"][1]["Vulnerabilities"][0]["PkgPath"] = "usr/local/lib/python3.13/site-packages/pip"
    _, text = _notice(tmp_path, data)
    assert "PkgPath=usr/local/lib/python3.13/site-packages/pip FilePath=" in text

    # исправленный образ: CRITICAL 0, но путь certifi всё равно в notice — подтверждение источника
    del data["Results"][1]["Vulnerabilities"]
    del data["Results"][1]["Packages"][0]
    (tmp_path / "clean").mkdir()
    _, text = _notice(tmp_path / "clean", data)
    assert "bm:local: CRITICAL 0" in text
    assert "certifi 2026.10.19 Target=Python FilePath=app/.venv/" in text
    assert "2026.6.17" not in text


def test_trivy_vulnerable_package_listed_without_pkg_option(tmp_path: Path) -> None:
    _, text = _notice(tmp_path, TRIVY_JSON, pkg=None)
    assert (
        "certifi 2026.6.17 Target=Python FilePath=usr/local/lib/python3.13/site-packages/pip/_vendor" in text
    )


def test_trivy_missing_or_broken_report_is_reported_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    proc = _gha(
        tmp_path, "trivy", "--json", str(tmp_path / "nope.json"), "--json", str(tmp_path / "bad.json")
    )
    assert proc.returncode == 0
    notices = _commands(proc.stdout, "notice")
    assert len(notices) == 1 and not _commands(proc.stdout, "error")
    text = _unescape(notices[0])
    assert "nope.json: нет отчёта" in text and "bad.json: отчёт не читается" in text


def test_summary_marks_skipped_steps_not_pass(tmp_path: Path) -> None:
    """5Д7: шаг с невыполненным предусловием — «пропущен», а не PASS."""
    steps = {"backup": {"outcome": "failure"}, "restore": {"outcome": "skipped"}}
    proc = _gha(tmp_path, "summary", "--out", str(tmp_path), env={"STEPS_JSON": json.dumps(steps)})
    text = _unescape(_commands(proc.stdout, "notice")[0].split("::", 2)[2])
    assert "Пропущены (предусловие не выполнено): restore" in text
    assert "restore: PASS" not in text
