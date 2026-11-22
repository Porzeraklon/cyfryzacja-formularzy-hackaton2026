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
COPY --from=builder /opt/venv /opt/venv
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY frontend ./frontend
COPY data ./data
RUN mkdir -p /app/models \
    && chown -R appuser:appuser /app

USER 10001

LABEL org.opencontainers.image.source="https://github.com/Porzeraklon/cyfryzacja-formularzy-hackaton2026" \
      org.opencontainers.image.title="formularzex" \
      org.opencontainers.image.description="Cyfryzacja formularzy (Wyzwanie COI) — lokalny pipeline AI/OCR + wizualizator e-Uslugi" \
      org.opencontainers.image.licenses="MIT"

# Eksponowany wyłącznie port aplikacji.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
