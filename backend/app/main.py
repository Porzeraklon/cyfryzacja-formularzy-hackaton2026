"""Inicjalizacja FastAPI: CORS, handlery błędów, API v1, statyczny frontend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.api.v1.router import api_router
from backend.app.core.config import get_settings
from backend.app.core.exceptions import register_exception_handlers


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "Cyfryzacja formularzy (Wyzwanie COI). 100% offline, lokalne modele AI/OCR, "
            "wyłącznie biblioteki na licencjach permisywnych."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    if settings.FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=settings.FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()
