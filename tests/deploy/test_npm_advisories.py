"""Перечень advisory npm audit (14_tech_rulings.md, пакет 5, 5Д5): `deploy/ci/npm_advisories.py`.

Гейт `make check` по npm — только critical (`npm audit --audit-level=critical`). Некритичные находки
(сейчас 7 moderate через react-admin → decode-uri-component) — предупреждение WARN с идентификаторами
advisory: их техлид переносит в `16_release.md` («Известные ограничения»).
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "ci" / "npm_advisories.py"

ADVISORY = {
    "source": 1147955,
    "name": "decode-uri-component",
    "dependency": "decode-uri-component",
    "title": "decode-uri-component: Denial of service via exponential decoding of malformed percent-encoded input",
    "url": "https://github.com/advisories/GHSA-vcc3-ghjq-m6fr",
    "severity": "moderate",
    "range": "<=0.4.2",
}
# Форма отчёта `npm audit --json` (auditReportVersion 2), сокращённо по прогону 2026-10-21.
REPORT = {
    "auditReportVersion": 2,
    "vulnerabilities": {
        "decode-uri-component": {"name": "decode-uri-component", "severity": "moderate", "via": [ADVISORY]},
        "query-string": {"name": "query-string", "severity": "moderate", "via": ["decode-uri-component"]},
        "ra-core": {"name": "ra-core", "severity": "moderate", "via": ["query-string"]},
        "react-admin": {"name": "react-admin", "severity": "moderate", "via": ["ra-core"]},
    },
    "metadata": {
        "vulnerabilities": {"info": 0, "low": 0, "moderate": 7, "high": 0, "critical": 0, "total": 7}
    },
}


def _run(path: Path):
    # аргументы — константы теста
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True, timeout=30, check=False
    )


def test_lists_primary_advisories_and_transitive_packages(tmp_path: Path) -> None:
    report = tmp_path / "audit.json"
    report.write_text(json.dumps(REPORT), encoding="utf-8")
    proc = _run(report)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == "npm audit: critical 0, high 0, moderate 7, low 0, info 0"
    assert any(
        "GHSA-vcc3-ghjq-m6fr" in line and "npm 1147955" in line and "decode-uri-component <=0.4.2" in line
        for line in lines
    )
    assert any(line.strip() == "транзитивно: query-string, ra-core, react-admin" for line in lines)
    # последняя строка — итог для строки WARN в summary.txt make check
    assert lines[-1] == "7 moderate; advisory: GHSA-vcc3-ghjq-m6fr (moderate, decode-uri-component <=0.4.2)"


def test_clean_report_and_unreadable_report(tmp_path: Path) -> None:
    clean = tmp_path / "clean.json"
    clean.write_text(
        json.dumps({"auditReportVersion": 2, "vulnerabilities": {}, "metadata": {"vulnerabilities": {}}}),
        encoding="utf-8",
    )
    proc = _run(clean)
    assert proc.returncode == 0 and proc.stdout.strip().splitlines()[-1] == "уязвимостей нет"
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    proc = _run(bad)
    assert proc.returncode == 2 and "не разобран" in proc.stdout
