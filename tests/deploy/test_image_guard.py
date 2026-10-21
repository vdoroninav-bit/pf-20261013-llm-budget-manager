"""Проверка корня образа перед копированием в python-runtime (T099, 5Д3): `deploy/image_guard.py`.

Trivy находит пакеты не только по `*.dist-info`, но и по SBOM-файлам внутри образа (`pip/_vendor/bom.cdx.json`
у pip 26.x — certifi 2026.6.17, FilePath пуст). Guard роняет сборку, если в корне остались: каталог certifi или
certifi-*.dist-info, SBOM-файлы (*.cdx.json, *.spdx, *.spdx.json), архивы .whl/.zip/.egg/.pyz с certifi внутри.
Найденные архивы перечисляются всегда (диагностика).
"""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "image_guard.py"


def _guard(root: Path):
    # аргументы — константы теста
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, timeout=60, check=False
    )


def _site(root: Path) -> Path:
    site = root / "usr/local/lib/python3.13/site-packages"
    site.mkdir(parents=True)
    (root / "etc/ssl/certs").mkdir(parents=True)
    (root / "usr/share/ca-certificates").mkdir(parents=True)  # не certifi: маска не *certifi*
    return site


def test_clean_root_passes_and_lists_archives(tmp_path: Path) -> None:
    site = _site(tmp_path)
    with zipfile.ZipFile(site / "other.zip", "w") as z:
        z.writestr("x/readme.txt", "ok")
    proc = _guard(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "архивы: 1" in proc.stdout and "other.zip" in proc.stdout
    assert "image_guard: OK" in proc.stdout


def test_vendored_certifi_and_dist_info_fail(tmp_path: Path) -> None:
    site = _site(tmp_path)
    (site / "pip/_vendor/certifi").mkdir(parents=True)
    (site / "certifi-2026.6.17.dist-info").mkdir()
    proc = _guard(tmp_path)
    assert proc.returncode == 1
    assert "pip/_vendor/certifi" in proc.stdout and "certifi-2026.6.17.dist-info" in proc.stdout


def test_sbom_file_fails(tmp_path: Path) -> None:
    site = _site(tmp_path)
    (site / "pip/_vendor").mkdir(parents=True)
    bom = {"bomFormat": "CycloneDX", "components": [{"name": "certifi", "version": "2026.6.17"}]}
    (site / "pip/_vendor/bom.cdx.json").write_text(json.dumps(bom), encoding="utf-8")
    (tmp_path / "opt").mkdir()
    (tmp_path / "opt/.spdx-x.spdx").write_text("SPDXVersion: SPDX-2.3", encoding="utf-8")
    proc = _guard(tmp_path)
    assert proc.returncode == 1
    assert "SBOM: " in proc.stdout and "bom.cdx.json" in proc.stdout and ".spdx-x.spdx" in proc.stdout


def test_wheel_with_certifi_fails(tmp_path: Path) -> None:
    site = _site(tmp_path)
    bundled = tmp_path / "usr/local/lib/python3.13/ensurepip/_bundled"
    bundled.mkdir(parents=True)
    with zipfile.ZipFile(bundled / "pip-26.2.1-py3-none-any.whl", "w") as z:
        z.writestr("pip/_vendor/certifi/__init__.py", '__version__ = "2026.06.17"')
    (site / "broken.whl").write_bytes(b"not a zip")
    proc = _guard(tmp_path)
    assert proc.returncode == 1
    assert "архив с certifi: " in proc.stdout and "pip-26.2.1-py3-none-any.whl" in proc.stdout
    assert "broken.whl" in proc.stdout  # нечитаемый архив — в перечне, но не падение
