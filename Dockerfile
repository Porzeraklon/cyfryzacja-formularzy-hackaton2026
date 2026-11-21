# Oficjalny, lekki obraz Python 3.14 — jawny tag wersji (ZAKAZ :latest).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dedykowany użytkownik non-root (uid=10001).
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid appuser --create-home appuser

COPY pyproject.toml README.md ./
COPY backend ./backend
COPY frontend ./frontend
COPY data ./data

# Instalacja wyłącznie zależności permisywnych (MIT/Apache-2.0/BSD).
# Sekrety i konfiguracja NIGDY na etapie build — wyłącznie runtime ENV.
RUN pip install --no-cache-dir . \
    && mkdir -p /app/models \
    && chown -R appuser:appuser /app

USER 10001

# Eksponowany wyłącznie port aplikacji.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
