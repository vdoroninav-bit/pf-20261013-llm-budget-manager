#!/usr/bin/env python3
"""Проверка корня образа перед копированием в python-runtime (T099; 14_tech_rulings.md, пакет 5, 5Д3).

Запуск — в стадии python-slim-clean Dockerfile, скрипт смонтирован на время RUN и в образ не попадает:
  RUN --mount=type=bind,source=deploy/image_guard.py,target=/tmp/image_guard.py python /tmp/image_guard.py /

Код выхода 1, если в корне (без /proc, /sys, /dev) есть:
  - каталог `certifi` или `certifi-*` (пакет, dist-info, вендоринг вроде pip/_vendor/certifi);
  - SBOM-файл (*.cdx.json, *.spdx, *.spdx.json): Trivy берёт из него пакеты с пустым FilePath, и находка не
    снимается удалением файла в верхнем слое (CI 92b3414: pip/_vendor/bom.cdx.json с certifi 2026.6.17);
  - архив .whl/.zip/.egg/.pyz, внутри которого есть путь с компонентом certifi.
Все найденные архивы печатаются всегда (диагностика). Только stdlib.
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

SKIP = ("proc", "sys", "dev")
SBOM_SUFFIXES = (".cdx.json", ".spdx", ".spdx.json")
ARCHIVE_SUFFIXES = (".whl", ".zip", ".egg", ".pyz")


def _is_certifi(name: str) -> bool:
    return name == "certifi" or name.startswith("certifi-")


def _archive_has_certifi(path: Path) -> bool | None:
    try:
        with zipfile.ZipFile(path) as z:
            return any(_is_certifi(part) for entry in z.namelist() for part in entry.split("/"))
    except (OSError, zipfile.BadZipFile):
        return None


def scan(root: Path) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    archives: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        if here == root:
            dirnames[:] = [d for d in dirnames if d not in SKIP]
        for d in dirnames:
            if _is_certifi(d):
                problems.append(f"certifi: {here / d}")
        for f in filenames:
            path = here / f
            if _is_certifi(f):
                problems.append(f"certifi: {path}")
            if f.endswith(SBOM_SUFFIXES):
                problems.append(f"SBOM: {path}")
            if f.endswith(ARCHIVE_SUFFIXES):
                has = _archive_has_certifi(path)
                archives.append(f"{path}" + (" (не читается как zip)" if has is None else ""))
                if has:
                    problems.append(f"архив с certifi: {path}")
    return problems, archives


def main(argv: list[str]) -> int:
    root = Path(argv[0] if argv else "/")
    problems, archives = scan(root)
    print(f"image_guard: архивы: {len(archives)}")
    for a in archives:
        print(f"  {a}")
    if problems:
        print("image_guard: FAIL — в корне образа остались источники находок Trivy:")
        for p in problems:
            print(f"  {p}")
        return 1
    print("image_guard: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
