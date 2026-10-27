"""Запуск «Бюджет-менеджера LLM» из AppImage (deploy/appimage).

Код приложения (`bm`) не меняется. Отличие от образа Docker одно: админка лежит не в `/app/static/admin`,
а внутри AppImage. Поэтому api запускается через эту обёртку: фабрика передаёт `create_app` каталог из
переменной BM_APPIMAGE_ADMIN_DIR, а точка запуска — та же `bm.serve.main` (TCP_NODELAY, UVICORN_WORKERS).
"""
