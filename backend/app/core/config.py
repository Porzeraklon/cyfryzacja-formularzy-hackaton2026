"""Konfiguracja aplikacji - wyłącznie zmienne środowiskowe (runtime, nie build)."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".doc", ".docx", ".ods", ".xlsx", ".jpg", ".jpeg", ".jpe", ".png"}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=True
    )

    ENV: Literal["development", "test", "production"] = "development"
    APP_NAME: str = "formularzex"
    APP_VERSION: str = "0.2.0"
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

    # local = llama.cpp + wagi GGUF w MODELS_DIR; api = endpoint OpenAI-compatible.
    LLM_BACKEND: Literal["local", "api"] = "local"
    LLM_API_URL: str = ""
    LLM_API_KEY: SecretStr = SecretStr("")
    LLM_MODEL: str = ""
    LLM_TIMEOUT_SECONDS: float = Field(default=600.0, gt=0)
    LLM_RESPONSE_FORMAT: Literal["json_schema", "json_object", "none"] = "json_object"
    # Tryb local (CPU): liczba wątków llama.cpp (0 = wszystkie rdzenie logiczne; na CPU
    # z hyperthreadingiem zwykle szybciej = liczba rdzeni fizycznych) i rozmiar kontekstu.
    LLM_THREADS: int = Field(default=0, ge=0)
    LLM_CTX: int = Field(default=8192, ge=2048)

    # NoDecode: akceptujemy zarówno tablicę JSON, jak i listę po przecinku (dekodowanie w walidatorze).
    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:8000", "http://127.0.0.1:8000"]

    FRONTEND_DIR: Path = REPO_ROOT / "frontend"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [origin.strip() for origin in text.split(",") if origin.strip()]
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
