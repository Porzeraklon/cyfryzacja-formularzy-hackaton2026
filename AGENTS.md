# AGENTS.md

This file provides guidance to the AI agent when working with code in this repository.

Projekt konkursowy (Wyzwanie COI). Dokumentacja i komunikaty commitów po polsku, z diakrytykami.

## Twarde reguły konkursowe (NFR — nie łamać)

- Aplikacja działa w 100% offline: zakaz wywołań zewnętrznych API, chmurowych LLM, MCP, SaaS. Modele AI/OCR wyłącznie lokalne (`MODELS_DIR`, wagi pobierane ręcznie poza runtime).
- Wyłącznie licencje permisywne (MIT, Apache-2.0, BSD, ISC, PSF, MPL-2.0). Zakaz GPL/AGPL/LGPL — w szczególności PyMuPDF/fitz. `test_licenses.py` oblewa przy naruszeniu (skanuje zainstalowane pakiety przez pip-licenses). Dual-license tylko przez świadomą allowlistę w tym teście (obecnie: `odfpy`).
- Nowe zależności: ciężkie AI/OCR/LLM → extras `[ai]`/`[llm]`, nie do dependencies core; przy każdej pozycji komentarz z licencją.
- Pliki użytkowników przetwarzane wyłącznie w RAM (BytesIO, cykl życia żądania) — zero trwałego zapisu na dysk.
- Docker: jawne tagi obrazów (zakaz `:latest`), non-root uid=10001, sekrety i konfiguracja tylko przez ENV w runtime (nigdy na etapie build).

## Podział gałęzi (zero konfliktów merge)

- `main` = zamrożone warstwy wspólne: `backend/app/{schemas,interfaces,api,core,services}`. Zmiany tylko za obopólną zgodą, małym commitem na `main`.
- `feat/backend-ai-pipeline` — wyłączna własność `backend/app/modules/` + `data/`. Stuby z `TODO(branch-1)` raise NotImplementedError (API zwraca wtedy 503); po implementacji `USE_MOCK_PIPELINE=false`.
- `feat/frontend-and-visualizer` — wyłączna własność `frontend/`.
- Nie edytuj plików spoza wyłącznej własności gałęzi, na której pracujesz.

## Setup i testy

- Host ma Python 3.13 (nie 3.14); Docker celuje w `python:3.14-slim`. `requires-python = ">=3.13"` jest celowe — nie "naprawiaj".
- `.venv/bin/pip install -e ".[dev]"`, potem `pytest` (testpaths i addopts w pyproject.toml).
- `get_settings()` jest cache'owane (`lru_cache`) — po zmianie ENV w teście wywołaj `get_settings.cache_clear()` (wzorzec w conftest.py).
- Po zmianie `schemas/form_schema.py` regeneruj `data/schemas/coi_target_schema.json` z `FormDocumentSchema.model_json_schema()` (koordynacja: `data/` należy do Brancha 1).

## Styl

- Frontend: czysty vanilla HTML/CSS/JS serwowany statycznie przez FastAPI — bez frameworków, bundlerów, npm.
- Modele kontraktowe Pydantic v2 mają `extra="forbid"` — nowe pole wymaga aktualizacji schematu, mocka w `pipeline_service.py` i testów.
- Ruff: line-length 110 (nie domyślne 88).
