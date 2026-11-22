# Oficjalny, lekki obraz Python 3.14 — jawny tag wersji (ZAKAZ :latest).
ARG PYTHON_TAG=3.14-slim

# --- Etap build: venv z zależnościami (llama-cpp-python kompiluje się ze źródeł) ---
FROM python:${PYTHON_TAG} AS builder

# EXTRAS="" = core (GUI + API + mock); "ai,llm,llm-api" = realny pipeline (OCR + GGUF + tryb api).
ARG EXTRAS=""

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY frontend ./frontend
COPY data ./data

# GGML_NATIVE=OFF: przenośny obraz — dispatch AVX/AVX2 wybierany w runtime, nie na maszynie build.
ENV CMAKE_ARGS="-DGGML_NATIVE=OFF"
RUN python -m venv /opt/venv \
    && if [ -n "$EXTRAS" ]; then \
        /opt/venv/bin/pip install --no-cache-dir ".[${EXTRAS}]"; \
    else \
        /opt/venv/bin/pip install --no-cache-dir .; \
    fi

# --- Etap final: bez narzędzi buildowych, non-root ---
FROM python:${PYTHON_TAG}

ARG EXTRAS=""

# Wagi LLM (Qwen3, Apache-2.0) pobierane WYŁĄCZNIE na etapie build i wypiekane w obrazie -ai:
# runtime pozostaje offline, a obraz z GHCR jest gotowy do pracy bez montowania models/.
# Rewizja repo HF + sha256 przypięte — ten sam plik co ręcznie pobierana waga do MODELS_DIR.
ARG LLM_GGUF_REPO=unsloth/Qwen3-4B-Instruct-2507-GGUF
ARG LLM_GGUF_REVISION=a06e946bb6b655725eafa393f4a9745d460374c9
ARG LLM_GGUF_FILE=Qwen3-4B-Instruct-2507-Q4_K_M.gguf
ARG LLM_GGUF_SHA256=3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Biblioteki runtime wyłącznie dla wariantu AI: libGL/glib (opencv z RapidOCR), libgomp (llama.cpp).
RUN if [ -n "$EXTRAS" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends libgl1 libglib2.0-0t64 libgomp1 \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# Dedykowany użytkownik non-root (uid=10001).
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid appuser --create-home appuser

WORKDIR /app
RUN mkdir -p /app/models \
    && chown appuser:appuser /app /app/models

USER 10001

# EXTRAS="" (core) = katalog models/ pozostaje pusty; wariant AI dostaje wagę GGUF.
# Pobieranie już jako appuser — bez dodatkowej warstwy chown na 2.5 GB pliku.
RUN python - <<'PY'
import hashlib
import os
import pathlib
import sys
import urllib.request

if not os.environ.get("EXTRAS"):
    raise SystemExit(0)

name = os.environ["LLM_GGUF_FILE"]
url = (
    f"https://huggingface.co/{os.environ['LLM_GGUF_REPO']}"
    f"/resolve/{os.environ['LLM_GGUF_REVISION']}/{name}"
)
expected = os.environ["LLM_GGUF_SHA256"]
target = pathlib.Path("/app/models") / name
digest = hashlib.sha256()
with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as handle:
    while chunk := response.read(8 * 1024 * 1024):
        handle.write(chunk)
        digest.update(chunk)
if digest.hexdigest() != expected:
    target.unlink()
    sys.exit(f"sha256 wagi {name} niezgodny: {digest.hexdigest()} != {expected}")
print(f"LLM: {name} ({target.stat().st_size} B, sha256 OK)")
PY

# Kod i venv DOPIERO po warstwie wag: zmiana kodu lub README nie unieważnia 2.5 GB
# (bez ponownego pobierania i ponownego uploadu warstwy do rejestru).
COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appuser pyproject.toml README.md ./
COPY --chown=appuser:appuser backend ./backend
COPY --chown=appuser:appuser frontend ./frontend
COPY --chown=appuser:appuser data ./data

LABEL org.opencontainers.image.source="https://github.com/Porzeraklon/cyfryzacja-formularzy-hackaton2026" \
      org.opencontainers.image.title="formularzex" \
      org.opencontainers.image.description="Cyfryzacja formularzy (Wyzwanie COI) — lokalny pipeline AI/OCR + wizualizator e-Uslugi" \
      org.opencontainers.image.licenses="MIT"

# Eksponowany wyłącznie port aplikacji.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
