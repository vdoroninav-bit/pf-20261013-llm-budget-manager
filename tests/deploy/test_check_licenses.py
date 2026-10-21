"""Проверка лицензий по SBOM (T088; 14_tech_rulings.md, пакет 3, 3Д3): белый список, узкие исключения.

Исключение — пакет И версия (`pypi:certifi@2026.10.19`): другая версия того же пакета снова проверяется
по белому списку. Запись без версии — ошибка конфигурации. MIT-0 — в белом списке.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / "deploy" / "ci"
SCRIPT = CI / "check_licenses.py"


def _sbom(tmp_path: Path, components: list[dict]) -> Path:
    path = tmp_path / "sbom.cdx.json"
    path.write_text(json.dumps({"bomFormat": "CycloneDX", "components": components}), encoding="utf-8")
    return path


def _comp(eco: str, name: str, version: str, license_id: str) -> dict:
    return {
        "type": "library",
        "name": name,
        "version": version,
        "purl": f"pkg:{eco}/{name}@{version}",
        "licenses": [{"license": {"id": license_id}}],
    }


def _run(sbom: Path, exceptions: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCRIPT), "--allow", str(CI / "licenses-allow.txt")]
    if exceptions is not None:
        cmd += ["--exceptions", str(exceptions)]
    # аргументы — пути теста
    return subprocess.run([*cmd, str(sbom)], capture_output=True, text=True, timeout=30, check=False)  # noqa: S603


def test_mit0_is_allowed(tmp_path: Path) -> None:
    proc = _run(_sbom(tmp_path, [_comp("pypi", "cffi", "2.0.0", "MIT-0")]))
    assert proc.returncode == 0, proc.stdout


def test_mpl_not_in_allow_list(tmp_path: Path) -> None:
    proc = _run(_sbom(tmp_path, [_comp("pypi", "certifi", "2026.10.19", "MPL-2.0")]))
    assert proc.returncode == 1 and "NOT_ALLOWED  pypi:certifi@2026.10.19" in proc.stdout, proc.stdout


def test_project_exceptions_cover_exact_certifi_version(tmp_path: Path) -> None:
    """Версия certifi в исключении совпадает с uv.lock (иначе make check упадёт на реальном SBOM)."""
    lock = (ROOT / "app" / "backend" / "uv.lock").read_text(encoding="utf-8")
    version = lock.split('name = "certifi"\nversion = "', 1)[1].split('"', 1)[0]
    sbom = _sbom(tmp_path, [_comp("pypi", "certifi", version, "MPL-2.0")])
    proc = _run(sbom, CI / "licenses-exceptions.txt")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_exception_does_not_cover_other_version(tmp_path: Path) -> None:
    sbom = _sbom(tmp_path, [_comp("pypi", "certifi", "2026.7.22", "MPL-2.0")])
    proc = _run(sbom, CI / "licenses-exceptions.txt")
    assert proc.returncode == 1 and "pypi:certifi@2026.7.22" in proc.stdout, proc.stdout


def test_versionless_exception_is_rejected(tmp_path: Path) -> None:
    exc = tmp_path / "exceptions.txt"
    exc.write_text("pypi:certifi  # без версии\n", encoding="utf-8")
    proc = _run(_sbom(tmp_path, [_comp("pypi", "certifi", "2026.10.19", "MPL-2.0")]), exc)
    assert proc.returncode == 2 and "без версии" in proc.stderr, proc.stderr


def test_project_exceptions_all_have_versions() -> None:
    lines = [
        raw.split("#", 1)[0].strip()
        for raw in (CI / "licenses-exceptions.txt").read_text(encoding="utf-8").splitlines()
    ]
    entries = [x for x in lines if x]
    assert entries, "файл исключений пуст — certifi должен быть в нём (3Д3)"
    assert all("@" in e.split(":", 1)[1] for e in entries), entries


@pytest.mark.parametrize("license_id", ["GPL-3.0-only", "AGPL-3.0-only", "LGPL-2.1-only", "SSPL-1.0"])
def test_copyleft_fails_even_if_listed_in_exceptions(tmp_path: Path, license_id: str) -> None:
    exc = tmp_path / "exceptions.txt"
    exc.write_text("pypi:x@1\n", encoding="utf-8")
    proc = _run(_sbom(tmp_path, [_comp("pypi", "x", "1", license_id)]), exc)
    assert proc.returncode == 1 and "COPYLEFT" in proc.stdout, proc.stdout


def test_enterprise_react_admin_fails(tmp_path: Path) -> None:
    comp = _comp("npm", "ra-rbac", "5.0.0", "MIT") | {"group": "@react-admin"}
    proc = _run(_sbom(tmp_path, [comp]), CI / "licenses-exceptions.txt")
    assert proc.returncode == 1 and "ENTERPRISE" in proc.stdout, proc.stdout
