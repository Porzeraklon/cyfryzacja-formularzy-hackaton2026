"""Konfiguracja aplikacji - wyłącznie zmienne środowiskowe (runtime, nie build)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".doc", ".docx", ".ods", ".xlsx", ".jpg", ".jpeg", ".png"}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=True
    )

    ENV: Literal["development", "test", "production"] = "development"
    APP_NAME: str = "COI Form Digitizer"
    APP_VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"

    MAX_FILE_SIZE_BYTES: int = Field(default=20 * 1024 * 1024, gt=0)
    MAX_BATCH_FILES: int = Field(default=20, gt=0)

    JWT_AUTH_ENABLED: bool = False
    JWT_SECRET_KEY: str = "change-me-to-a-long-random-secret-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = Field(default=60, gt=0)

    USE_MOCK_PIPELINE: bool = True
    MODELS_DIR: Path = Path("models")
    DICTIONARIES_DIR: Path = Path("data/dictionaries")

    CORS_ORIGINS: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]

    FRONTEND_DIR: Path = REPO_ROOT / "frontend"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def pipeline_mode(self) -> str:
        return "mock" if self.USE_MOCK_PIPELINE else "real"

    @property
    def allowed_extensions(self) -> list[str]:
        return sorted(ALLOWED_EXTENSIONS)


@lru_cache
def get_settings() -> Settings:
    return Settings()
