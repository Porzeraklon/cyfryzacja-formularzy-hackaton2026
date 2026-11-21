"""Backend LLM: przełącznik ENV, klient OpenAI-compatible i mapowanie błędów na 503.

Zero sieci i zero modelu: transport httpx.MockTransport przechwytuje żądania,
a lokalny backend sprawdzamy na pustym katalogu i fałszywym pliku GGUF.
"""

import json
import sys

import httpx
import pytest

from backend.app.core.config import Settings
from backend.app.core.exceptions import PipelineNotReadyError
from backend.app.modules.ai_extractor.llm_backend import (
    HttpApiBackend,
    LocalLlamaBackend,
    build_llm_backend,
)

SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}}
MESSAGES = [{"role": "user", "content": "Kwota nadpłaty: 120 zł"}]
API_KEY = "sekret-testowy-3f8a"
CONTENT = '{"title": "Wniosek o zwrot nadpłaty"}'


def chat_response(content: str = CONTENT) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


def api_backend(handler, **overrides) -> HttpApiBackend:
    kwargs = {
        "api_url": "http://llm-host:8000/v1",
        "api_key": API_KEY,
        "model": "duzy-model",
        "timeout_seconds": 30.0,
        "response_format": "json_object",
    }
    kwargs.update(overrides)
    return HttpApiBackend(transport=httpx.MockTransport(handler), **kwargs)


class TestHttpApiBackend:
    def test_request_shape_and_auth_header(self):
        seen = []

        def handler(request):
            seen.append(request)
            return chat_response()

        assert api_backend(handler).complete(MESSAGES, SCHEMA) == CONTENT
        request = seen[0]
        assert request.method == "POST"
        assert str(request.url) == "http://llm-host:8000/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        payload = json.loads(request.content)
        assert payload["model"] == "duzy-model"
        assert payload["messages"] == MESSAGES
        assert payload["temperature"] == 0.0
        assert "max_tokens" not in payload
        assert payload["stream"] is False
        assert payload["response_format"] == {"type": "json_object"}

    def test_full_endpoint_url_is_not_doubled(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return chat_response()

        api_backend(handler, api_url="http://llm-host:8000/v1/chat/completions/").complete(MESSAGES, SCHEMA)
        assert seen == ["http://llm-host:8000/v1/chat/completions"]

    def test_no_auth_header_without_key(self):
        seen = []

        def handler(request):
            seen.append(request)
            return chat_response()

        api_backend(handler, api_key="").complete(MESSAGES, SCHEMA)
        assert "authorization" not in seen[0].headers

    def test_response_format_json_schema_passes_schema(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return chat_response()

        api_backend(handler, response_format="json_schema").complete(MESSAGES, SCHEMA)
        response_format = seen[0]["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["schema"] == SCHEMA
        assert response_format["json_schema"]["strict"] is True

    def test_response_format_none_omits_field(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return chat_response()

        api_backend(handler, response_format="none").complete(MESSAGES, SCHEMA)
        assert "response_format" not in seen[0]

    def test_http_error_is_503(self):
        backend = api_backend(lambda request: httpx.Response(500, text="model overloaded"))
        with pytest.raises(PipelineNotReadyError) as exc:
            backend.complete(MESSAGES, SCHEMA)
        assert exc.value.status_code == 503
        assert exc.value.code == "PIPELINE_NOT_READY"
        assert "HTTP 500" in exc.value.message
        assert exc.value.details["reason"] == "model overloaded"

    def test_timeout_is_503(self):
        def handler(request):
            raise httpx.ReadTimeout("przekroczono czas", request=request)

        with pytest.raises(PipelineNotReadyError) as exc:
            api_backend(handler).complete(MESSAGES, SCHEMA)
        assert exc.value.status_code == 503
        assert "czas oczekiwania" in exc.value.message

    def test_connection_error_is_503(self):
        def handler(request):
            raise httpx.ConnectError("Connection refused", request=request)

        with pytest.raises(PipelineNotReadyError) as exc:
            api_backend(handler).complete(MESSAGES, SCHEMA)
        assert exc.value.status_code == 503
        assert "LLM_API_URL" in exc.value.message

    def test_non_json_body_is_503(self):
        backend = api_backend(lambda request: httpx.Response(200, text="<html>proxy</html>"))
        with pytest.raises(PipelineNotReadyError) as exc:
            backend.complete(MESSAGES, SCHEMA)
        assert exc.value.status_code == 503
        assert "inną niż JSON" in exc.value.message

    def test_missing_choices_is_503(self):
        backend = api_backend(lambda request: httpx.Response(200, json={"id": "chatcmpl-1"}))
        with pytest.raises(PipelineNotReadyError) as exc:
            backend.complete(MESSAGES, SCHEMA)
        assert exc.value.status_code == 503
        assert "bez treści czatu" in exc.value.message

    def test_empty_content_is_503(self):
        backend = api_backend(lambda request: chat_response("   "))
        with pytest.raises(PipelineNotReadyError) as exc:
            backend.complete(MESSAGES, SCHEMA)
        assert exc.value.status_code == 503
        assert "pustą odpowiedź" in exc.value.message

    def test_api_key_never_leaks_in_error_payload(self):
        backend = api_backend(lambda request: httpx.Response(401, text=f"invalid api key: {API_KEY}"))
        with pytest.raises(PipelineNotReadyError) as exc:
            backend.complete(MESSAGES, SCHEMA)
        assert API_KEY not in json.dumps(exc.value.to_payload(), ensure_ascii=False)
        assert "***" in exc.value.details["reason"]

    def test_error_body_is_truncated(self):
        backend = api_backend(lambda request: httpx.Response(502, text="x" * 5000))
        with pytest.raises(PipelineNotReadyError) as exc:
            backend.complete(MESSAGES, SCHEMA)
        assert len(exc.value.details["reason"]) == 300

    def test_ready_checks_configuration_without_network(self):
        def handler(request):
            raise AssertionError("ready() nie może wykonywać zapytań sieciowych")

        assert api_backend(handler).ready() is True

    @pytest.mark.parametrize("overrides", [{"api_url": ""}, {"model": ""}, {"api_url": "   "}])
    def test_unconfigured_is_not_ready_and_complete_is_503(self, overrides):
        backend = api_backend(lambda request: chat_response(), **overrides)
        assert backend.ready() is False
        with pytest.raises(PipelineNotReadyError) as exc:
            backend.complete(MESSAGES, SCHEMA)
        assert exc.value.status_code == 503
        assert "LLM_API_URL" in exc.value.message


class TestLocalLlamaBackend:
    def test_ready_and_model_name_with_gguf(self, tmp_path):
        (tmp_path / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf").write_bytes(b"fake")
        backend = LocalLlamaBackend(tmp_path)
        assert backend.ready() is True
        assert backend.model_name == "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

    def test_empty_models_dir_is_not_ready_and_complete_is_503(self, tmp_path):
        backend = LocalLlamaBackend(tmp_path)
        assert backend.ready() is False
        with pytest.raises(PipelineNotReadyError) as exc:
            backend.complete(MESSAGES, SCHEMA)
        assert exc.value.status_code == 503
        assert "MODELS_DIR" in exc.value.message

    def test_missing_llama_cpp_is_503(self, tmp_path, monkeypatch):
        (tmp_path / "model.gguf").write_bytes(b"fake")
        monkeypatch.setitem(sys.modules, "llama_cpp", None)
        with pytest.raises(PipelineNotReadyError) as exc:
            LocalLlamaBackend(tmp_path).complete(MESSAGES, SCHEMA)
        assert exc.value.status_code == 503
        assert "llama-cpp-python" in exc.value.message


class TestBackendFactory:
    def test_default_is_local(self, tmp_path):
        settings = Settings(_env_file=None, MODELS_DIR=tmp_path)
        assert settings.LLM_BACKEND == "local"
        assert isinstance(build_llm_backend(settings), LocalLlamaBackend)

    def test_api_mode_builds_http_backend(self):
        settings = Settings(
            _env_file=None,
            LLM_BACKEND="api",
            LLM_API_URL="http://llm-host:8000/v1",
            LLM_MODEL="duzy-model",
            LLM_API_KEY=API_KEY,
        )
        backend = build_llm_backend(settings)
        assert isinstance(backend, HttpApiBackend)
        assert backend.ready() is True
        assert backend.model_name == "duzy-model"

    def test_api_mode_misconfiguration_does_not_raise_in_factory(self):
        settings = Settings(_env_file=None, LLM_BACKEND="api")
        backend = build_llm_backend(settings)
        assert backend.ready() is False

    def test_api_key_is_secret_in_settings_repr(self):
        settings = Settings(_env_file=None, LLM_BACKEND="api", LLM_API_KEY=API_KEY)
        assert API_KEY not in repr(settings)
        assert settings.LLM_API_KEY.get_secret_value() == API_KEY
