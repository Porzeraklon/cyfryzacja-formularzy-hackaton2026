"""Gotowość realnego pipeline: biblioteki, nie same pliki wag; brak extras = 503, nie 500."""

import io

from fastapi.testclient import TestClient

from backend.app.core.security import MIME_PDF
from backend.app.modules.ai_extractor import llm_backend
from backend.app.modules.ingestion import parsers
from backend.app.services import pipeline_service
from backend.tests.conftest import PDF_BYTES

REAL_ENV = {
    "ENV": "development",
    "JWT_AUTH_ENABLED": "false",
    "USE_MOCK_PIPELINE": "false",
    "LLM_BACKEND": "local",
}


def upload():
    return {"file": ("wniosek.pdf", io.BytesIO(PDF_BYTES), MIME_PDF)}


def test_health_ocr_missing_without_library_even_with_weights(make_app, tmp_path, monkeypatch):
    (tmp_path / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf").write_bytes(b"fake")
    monkeypatch.setattr(pipeline_service, "find_spec", lambda name: None)
    app = make_app(MODELS_DIR=str(tmp_path), **REAL_ENV)
    with TestClient(app) as client:
        body = client.get("/api/v1/health").json()
    assert body["models"]["ocr"] == "missing"
    assert body["models"]["ready"] is False
    assert body["status"] == "degraded"


def test_health_llm_missing_without_library_even_with_gguf(make_app, tmp_path, monkeypatch):
    (tmp_path / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf").write_bytes(b"fake")
    monkeypatch.setattr(llm_backend, "find_spec", lambda name: None)
    app = make_app(MODELS_DIR=str(tmp_path), **REAL_ENV)
    with TestClient(app) as client:
        body = client.get("/api/v1/health").json()
    assert body["models"]["llm"] == "missing"
    assert body["models"]["ready"] is False


def test_convert_missing_parser_library_is_503(make_app, tmp_path, monkeypatch):
    def missing_library(self, **kwargs):
        raise ModuleNotFoundError("No module named 'pdfplumber'")

    monkeypatch.setattr(parsers.MultiFormatParser, "parse", missing_library)
    app = make_app(MODELS_DIR=str(tmp_path), **REAL_ENV)
    with TestClient(app) as client:
        response = client.post("/api/v1/forms/convert", files=upload())
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "PIPELINE_NOT_READY"
    assert "extras" in error["message"]
    assert "pdfplumber" in error["details"]["reason"]


def test_installed_libraries_and_gguf_report_ready(make_app, tmp_path):
    (tmp_path / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf").write_bytes(b"fake")
    app = make_app(MODELS_DIR=str(tmp_path), **REAL_ENV)
    with TestClient(app) as client:
        body = client.get("/api/v1/health").json()
    assert body["models"] == {
        "ocr": "ready",
        "llm": "ready",
        "models_dir": str(tmp_path),
        "ready": True,
    }
    assert body["status"] == "ok"
