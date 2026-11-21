"""Tryb LLM_BACKEND=api przez realne API: /health, konwersja i twarde 503 bez fallbacku."""

import io
import json

import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient

from backend.app.core.security import MIME_DOCX
from backend.app.modules.ai_extractor import llm_backend

API_ENV = {
    "ENV": "development",
    "JWT_AUTH_ENABLED": "false",
    "USE_MOCK_PIPELINE": "false",
    "LLM_BACKEND": "api",
    "LLM_API_URL": "http://llm-host:8000/v1",
    "LLM_MODEL": "duzy-model",
    "LLM_API_KEY": "sekret-testowy",
}

DOCUMENT = {
    "title": "Wniosek o zwrot nadpłaty podatku",
    "pages": [
        {
            "title": "Przedmiot wniosku",
            "sections": [
                {
                    "title": "Dane wniosku",
                    "fields": [
                        {"name": "kwota_nadplaty", "label": "Kwota nadpłaty", "field_type": "number"},
                        {"name": "rok_podatkowy", "label": "Rok podatkowy", "field_type": "number"},
                    ],
                }
            ],
        },
        {
            "title": "Uzasadnienie",
            "sections": [
                {
                    "title": "Opis sprawy",
                    "fields": [{"name": "uzasadnienie", "label": "Uzasadnienie", "field_type": "textarea"}],
                }
            ],
        },
    ],
}


def docx_bytes() -> bytes:
    buffer = io.BytesIO()
    document = Document()
    document.add_paragraph("Wniosek o zwrot nadpłaty podatku")
    document.add_paragraph("Kwota nadpłaty: 120 zł. Rok podatkowy: 2025.")
    document.save(buffer)
    return buffer.getvalue()


def upload():
    return {"file": ("wniosek.docx", io.BytesIO(docx_bytes()), MIME_DOCX)}


@pytest.fixture()
def endpoint(monkeypatch):
    """Podmienia transport backendu zbudowanego przez fabrykę — bez socketów."""
    seen = []

    def ok_handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(DOCUMENT, ensure_ascii=False)}}]}
        )

    def install(handler=ok_handler):
        real_factory = llm_backend.build_llm_backend

        def factory(settings):
            backend = real_factory(settings)
            backend._transport = httpx.MockTransport(handler)
            return backend

        monkeypatch.setattr(llm_backend, "build_llm_backend", factory)
        return seen

    return install


def test_health_reports_llm_ready_in_api_mode(make_app, tmp_path):
    (tmp_path / "ocr.onnx").write_bytes(b"fake")
    app = make_app(MODELS_DIR=str(tmp_path), **API_ENV)
    with TestClient(app) as client:
        body = client.get("/api/v1/health").json()
    assert body["pipeline_mode"] == "real"
    assert body["models"]["llm"] == "ready"
    assert body["models"]["ocr"] == "ready"
    assert body["models"]["ready"] is True


def test_health_reports_llm_missing_when_api_unconfigured(make_app, tmp_path):
    (tmp_path / "ocr.onnx").write_bytes(b"fake")
    app = make_app(
        ENV="development",
        JWT_AUTH_ENABLED="false",
        USE_MOCK_PIPELINE="false",
        LLM_BACKEND="api",
        MODELS_DIR=str(tmp_path),
    )
    with TestClient(app) as client:
        body = client.get("/api/v1/health").json()
    assert body["models"]["llm"] == "missing"
    assert body["models"]["ready"] is False
    assert body["status"] == "degraded"


def test_convert_in_api_mode_calls_endpoint(endpoint, make_app, tmp_path):
    (tmp_path / "ocr.onnx").write_bytes(b"fake")
    seen = endpoint()
    app = make_app(MODELS_DIR=str(tmp_path), **API_ENV)
    with TestClient(app) as client:
        response = client.post("/api/v1/forms/convert", files=upload())
    assert response.status_code == 200
    body = response.json()
    assert body["processing"]["pipeline_mode"] == "real"
    assert body["form"]["metadata"]["title"] == "Wniosek o zwrot nadpłaty podatku"
    assert body["form_definition"]["tabs"]
    assert seen[0]["model"] == "duzy-model"
    assert "Kwota nadpłaty" in seen[0]["messages"][1]["content"]


def test_convert_returns_503_when_endpoint_unreachable(endpoint, make_app, tmp_path):
    (tmp_path / "ocr.onnx").write_bytes(b"fake")

    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    endpoint(handler)
    app = make_app(MODELS_DIR=str(tmp_path), **API_ENV)
    with TestClient(app) as client:
        response = client.post("/api/v1/forms/convert", files=upload())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PIPELINE_NOT_READY"
