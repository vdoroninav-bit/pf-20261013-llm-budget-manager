import os
from pathlib import Path

from bm import main
from fastapi import FastAPI


def create_app() -> FastAPI:
    admin_dir = os.environ.get("BM_APPIMAGE_ADMIN_DIR", "").strip()
    return main.create_app(
        admin_dir=Path(admin_dir) if admin_dir else main.ADMIN_STATIC_DIR
    )
