import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import get_settings
from backend.app.main import create_app

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture()
def settings(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("USE_MOCK_PIPELINE", "true")
    monkeypatch.setenv("JWT_AUTH_ENABLED", "false")
    monkeypatch.setenv("MAX_FILE_SIZE_BYTES", str(1024 * 1024))
    monkeypatch.setenv("MAX_BATCH_FILES", "5")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture()
def client(settings):
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def make_app(monkeypatch):
    def _make(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()
        return create_app()
    yield _make
    get_settings.cache_clear()
