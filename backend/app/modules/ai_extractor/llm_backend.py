"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Backend LLM wybierany przez ENV (LLM_BACKEND):
- local: llama.cpp + wagi GGUF w MODELS_DIR (100% offline, zero wywołań sieciowych),
- api: endpoint OpenAI-compatible, np. duży model postawiony na serwerach urzędu.

Backend zwraca surowy tekst odpowiedzi czatu; walidacja JSON należy do extractora.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Protocol

from backend.app.core.config import Settings
from backend.app.core.exceptions import PipelineNotReadyError

N_CTX = 8192
_CONNECT_TIMEOUT_SECONDS = 10.0
_ERROR_BODY_LIMIT = 300

# llama.cpp nie jest thread-safe: jedna instancja na plik GGUF, inference serializowane.
_LLM_CACHE: dict[str, Any] = {}
_LLM_LOCK = threading.Lock()


class LlmBackend(Protocol):
    @property
    def model_name(self) -> str: ...

    def ready(self) -> bool: ...

    def complete(self, messages: list[dict[str, str]], json_schema: dict[str, Any]) -> str: ...


def _find_gguf(models_dir: Path) -> Path | None:
    if not models_dir.is_dir():
        return None
    files = sorted(models_dir.glob("*.gguf"))
    if not files:
        return None
    preferred = [f for f in files if "qwen3-4b" in f.name.lower()]
    return (preferred or files)[0]


def _load_llm(model_path: Path, n_threads: int = 0, n_ctx: int = N_CTX) -> Any:
    """Model ładowany raz na proces (cache) - kolejne żądania nie płacą za wczytanie wag."""
    key = f"{model_path.resolve()}|{n_threads}|{n_ctx}"
    with _LLM_LOCK:
        if key in _LLM_CACHE:
            return _LLM_CACHE[key]
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise PipelineNotReadyError(
                "Brak biblioteki llama-cpp-python. Zainstaluj extras: pip install -e '.[llm]'.",
                {"reason": str(exc)},
            ) from exc
        try:
            llm = Llama(
                model_path=str(model_path.resolve()),
                n_ctx=n_ctx,
                n_threads=n_threads or os.cpu_count() or 4,
                n_gpu_layers=0,
                verbose=False,
            )
        except Exception as exc:
            raise PipelineNotReadyError(
                f"Nie udało się wczytać modelu GGUF: {model_path.name}.",
                {"reason": str(exc)},
            ) from exc
        _LLM_CACHE[key] = llm
        return llm


def _content_from_response(response: Any, model: str) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PipelineNotReadyError(
            "Model LLM zwrócił odpowiedź bez treści czatu.",
            {"reason": str(exc), "model": model},
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise PipelineNotReadyError("Model LLM zwrócił pustą odpowiedź.", {"model": model})
    return content


def _chat_completions_url(api_url: str) -> str:
    base = api_url.strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


class LocalLlamaBackend:
    """Lokalny model GGUF przez llama-cpp-python (gramatyka GBNF z response_format)."""

    def __init__(self, models_dir: Path, *, n_threads: int = 0, n_ctx: int = N_CTX) -> None:
        self._models_dir = Path(models_dir)
        self._n_threads = n_threads
        self._n_ctx = n_ctx

    @property
    def model_name(self) -> str:
        model_path = _find_gguf(self._models_dir)
        return model_path.name if model_path else "local (brak pliku *.gguf)"

    def ready(self) -> bool:
        return _find_gguf(self._models_dir) is not None

    def complete(self, messages: list[dict[str, str]], json_schema: dict[str, Any]) -> str:
        model_path = _find_gguf(self._models_dir)
        if model_path is None:
            raise PipelineNotReadyError(
                "Brak modelu LLM (plik *.gguf) w MODELS_DIR. Pobierz wagi ręcznie, np. "
                "Qwen3-4B-Instruct-2507-Q4_K_M.gguf z unsloth/Qwen3-4B-Instruct-2507-GGUF.",
                {"models_dir": str(self._models_dir)},
            )
        llm = _load_llm(model_path, self._n_threads, self._n_ctx)
        with _LLM_LOCK:
            response = llm.create_chat_completion(
                messages=messages,
                response_format={"type": "json_object", "schema": json_schema},
                temperature=0.0,
                # llama-cpp-python domyślnie ucina do 16 tokenów — pełny budżet kontekstu.
                max_tokens=self._n_ctx,
            )
        return _content_from_response(response, model_path.name)


class HttpApiBackend:
    """Klient endpointu OpenAI-compatible (vLLM, llama.cpp server, serwer urzędu)."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        response_format: str,
        transport: Any = None,
    ) -> None:
        self._url = _chat_completions_url(api_url)
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._response_format = response_format
        self._transport = transport

    @property
    def model_name(self) -> str:
        return self._model or "api (brak LLM_MODEL)"

    def ready(self) -> bool:
        # Wyłącznie poprawność konfiguracji: /health jest odpytywane co 30 s i nie może
        # wykonywać zapytań sieciowych (timeout HEALTHCHECK to 5 s).
        return bool(self._url and self._model)

    def _redacted(self, text: str) -> str:
        snippet = text[:_ERROR_BODY_LIMIT]
        return snippet.replace(self._api_key, "***") if self._api_key else snippet

    def _response_format_payload(self, json_schema: dict[str, Any]) -> dict[str, Any] | None:
        if self._response_format == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {"name": "form_document", "strict": True, "schema": json_schema},
            }
        if self._response_format == "json_object":
            return {"type": "json_object"}
        return None

    def complete(self, messages: list[dict[str, str]], json_schema: dict[str, Any]) -> str:
        try:
            import httpx
        except ImportError as exc:
            raise PipelineNotReadyError(
                "Brak biblioteki httpx. Zainstaluj extras: pip install -e '.[llm-api]'.",
                {"reason": str(exc)},
            ) from exc
        if not self.ready():
            raise PipelineNotReadyError(
                "Tryb LLM_BACKEND=api wymaga ustawienia LLM_API_URL oraz LLM_MODEL.",
                {"model": self.model_name},
            )

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.0,
            "stream": False,
        }
        response_format = self._response_format_payload(json_schema)
        if response_format is not None:
            payload["response_format"] = response_format
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            # trust_env=False ignoruje HTTP(S)_PROXY — ruch nie wychodzi poza wskazany endpoint.
            # follow_redirects jest domyślnie wyłączone: przekierowanie nie zamieni POST na GET.
            with httpx.Client(
                timeout=httpx.Timeout(self._timeout, connect=_CONNECT_TIMEOUT_SECONDS),
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = client.post(self._url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise PipelineNotReadyError(
                f"Przekroczono czas oczekiwania na endpoint LLM ({self._timeout:g} s).",
                {"reason": f"{type(exc).__name__}: {exc}"[:_ERROR_BODY_LIMIT], "model": self.model_name},
            ) from exc
        except httpx.HTTPError as exc:
            raise PipelineNotReadyError(
                "Nie udało się połączyć z endpointem LLM (LLM_API_URL).",
                {"reason": f"{type(exc).__name__}: {exc}"[:_ERROR_BODY_LIMIT], "model": self.model_name},
            ) from exc

        if response.status_code >= 400:
            raise PipelineNotReadyError(
                f"Endpoint LLM zwrócił HTTP {response.status_code}.",
                {"reason": self._redacted(response.text), "model": self.model_name},
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise PipelineNotReadyError(
                "Endpoint LLM zwrócił odpowiedź inną niż JSON.",
                {"reason": self._redacted(response.text), "model": self.model_name},
            ) from exc
        return _content_from_response(body, self.model_name)


def build_llm_backend(settings: Settings) -> LlmBackend:
    """Nie rzuca wyjątków przy błędnej konfiguracji — o gotowości decyduje ready()/complete()."""
    if settings.LLM_BACKEND == "api":
        return HttpApiBackend(
            api_url=settings.LLM_API_URL,
            api_key=settings.LLM_API_KEY.get_secret_value(),
            model=settings.LLM_MODEL,
            timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
            response_format=settings.LLM_RESPONSE_FORMAT,
        )
    return LocalLlamaBackend(
        models_dir=settings.MODELS_DIR, n_threads=settings.LLM_THREADS, n_ctx=settings.LLM_CTX
    )
