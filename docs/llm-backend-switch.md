# Przełącznik backendu LLM (local / api) — branch `feat/llm-backend-switch`

Notatka przekazania dla pracy na tym branchu. Dotyczy wyłącznie `feat/llm-backend-switch`
(commity `20bf4ea`..`be6c5c1` na bazie `0f071d7`, plus ten dokument); `main` ani
`feat/backend-ai-pipeline` tych zmian nie mają.

## 1. Stan brancha

Commity (baza `0f071d7`):

| Commit | Zakres |
| --- | --- |
| `20bf4ea` | `modules:` nowy `llm_backend.py` + refaktor `extractor.py` |
| `14124b9` | `core:` `LLM_BACKEND` w ENV, `pipeline_service`, extra `llm-api`, `.env.example` |
| `be6c5c1` | `tests:` `test_llm_backend.py`, `test_extractor_llm.py`, `test_health_api_mode.py` |

Diff: 9 plików, +808/−81. Warstwy zamrożone ruszone minimalnie — **14 linii w 2 plikach**:
`backend/app/core/config.py` (+9/−1) i `backend/app/services/pipeline_service.py` (+5/−3).
Zgoda Brancha 2 na ten diff jest, ale merge do `feat/backend-ai-pipeline` / `main` nie był robiony.

## 2. Architektura

Seam `LlmBackend` (Protocol) w `backend/app/modules/ai_extractor/llm_backend.py`:

```python
model_name: str                              # plik GGUF albo LLM_MODEL
ready() -> bool                              # wyłącznie konfiguracja, nigdy osiągalność
complete(messages, json_schema) -> str
```

- **`LocalLlamaBackend`** — przeniesione bez zmian z `extractor.py`: `_find_gguf`
  (preferuje „qwen3-4b"), `_load_llm`, `_LLM_CACHE` + `_LLM_LOCK` (llama.cpp nie jest
  thread-safe), `N_CTX = 8192`.
- **`HttpApiBackend`** — httpx, `trust_env=False` (ignoruje `HTTP(S)_PROXY` i `.netrc`),
  przekierowania wyłączone (POST nie zamieni się w GET), `httpx.Timeout(LLM_TIMEOUT_SECONDS,
  connect=10)`, klient tworzony per wywołanie w `with` (`get_pipeline()` buduje pipeline
  per żądanie), `Authorization: Bearer` tylko gdy klucz niepusty, URL = `LLM_API_URL`
  + `/chat/completions` (chyba że już się tak kończy).
- **`build_llm_backend(settings)`** — nigdy nie rzuca przy błędnej konfiguracji; o błędzie
  decyduje `ready()` / `complete()`, bo fabryka działa też wewnątrz `/health`.

`extractor.py` jest backend-agnostyczny: `LocalStructureExtractor` → `LlmStructureExtractor(backend)`
(bez aliasu kompatybilności). **Pre-check `backend.ready()` w `extract()` zostaje** — dokument
bez tekstu omija LLM, więc bez tego checka zwracałby cichy, pusty formularz zamiast 503
(regresja: `test_convert_real_pipeline_not_ready`). Bez zmian: chunking po `MAX_PAGE_CHARS = 9000`,
scalanie paczek, mapowanie stron/sekcji/pól, fallbacki tabel, `_SYSTEM_PROMPT`, modele `_Llm*`
(`_LlmColumn` celowo nierekurencyjny — GBNF w llama.cpp crashuje na rekurencji).

`ready()` znaczy „skonfigurowany”, nie „osiągalny”: `/health` jest odpytywane co 30 s przez
HEALTHCHECK Dockera z timeoutem 5 s, więc nie wolno tam wykonywać zapytań sieciowych.

### Obsługa błędów

Każdy błąd endpointu = **twarde 503 `PIPELINE_NOT_READY`, bez fallbacku na local**:
timeout, błąd połączenia, HTTP ≥ 400, odpowiedź nie-JSON, brak `choices[0].message.content`,
pusta treść, niepoprawny JSON struktury, brak biblioteki httpx. `details` zawiera tylko
`{reason, model}`, body jest przycinane do 300 znaków, a **klucz API zamieniany na `***`**.

## 3. Uruchomienie w świeżym klonie

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e ".[ai,llm-api,dev]"   # [llm] tylko dla trybu local
cp .env.example .env
.venv/bin/pytest                               # oczekiwane: 122 passed
.venv/bin/ruff check backend && .venv/bin/ruff format --check backend
.venv/bin/uvicorn backend.app.main:app --reload --port 8000
```

`[llm]` (llama-cpp-python) nie jest potrzebne w trybie api. `[ai]` jest potrzebne nawet do
testów (`python-docx`, `pypdf`, `pdfplumber`, rapidocr).

Świeży klon **nie ma wag**: `models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (2.5 GB) jest gitignored.
Wyjątek: obraz Docker `ghcr.io/porzeraklon/formularzex:0.2.1-ai` ma tę wagę wypieczoną w `/app/models`
(pobrana na etapie build z przypiętą rewizją HF i sha256), więc tam nic nie pobierasz — nie montuj
też pustego `./models`, bo przesłoni model z obrazu.
`models/.gitkeep` jest śledzony, więc `/health` raportuje `ocr=ready` także bez wag — to heurystyka
„katalog niepusty”, a rapidocr ma modele ONNX w wheelu, więc OCR skanów działa od razu.

## 4. Konfiguracja trybu api (Qwen)

```dotenv
ENV=development
USE_MOCK_PIPELINE=false
JWT_AUTH_ENABLED=false
LLM_BACKEND=api
LLM_API_URL=http://llm-host:8000/v1      # samo /v1 wystarczy; pełny /chat/completions też działa
LLM_MODEL=Qwen/Qwen3-4B-Instruct-2507    # musi zgadzać się z --served-model-name serwera
LLM_API_KEY=sekret-tylko-w-runtime
LLM_TIMEOUT_SECONDS=600
LLM_RESPONSE_FORMAT=json_object
```

Wysyłany payload: `{model, messages, temperature: 0.0, stream: false}` plus opcjonalny
`response_format`. **Bez `max_tokens`** — o limicie wyjścia decyduje serwer/model
(local: `max_tokens = N_CTX`, bo llama-cpp-python domyślnie ucina do 16 tokenów):

| `LLM_RESPONSE_FORMAT` | Co leci w żądaniu | Kiedy |
| --- | --- | --- |
| `json_object` (domyślny) | `{"type": "json_object"}` | najszersza zgodność (vLLM, llama.cpp server, Ollama `/v1`) |
| `json_schema` | `{"type": "json_schema", "json_schema": {name: "form_document", strict: true, schema: …}}` | serwer z guided/structured output — twarda gwarancja kształtu |
| `none` | pole pomijane | serwer odrzuca `response_format` |

Brak auto-detekcji trybu — wymagałaby probe sieciowego.

Smoke:

```bash
curl -s localhost:8000/api/v1/health | jq '.models'   # llm=ready gdy URL+MODEL ustawione
curl -s -F "file=@wniosek.pdf" localhost:8000/api/v1/forms/convert | jq '.processing'
```

### Trzy rzeczy, które ugryzą na prawdziwym modelu

1. **Limit wyjścia zależy od serwera/modelu** (nie wysyłamy `max_tokens`). Duża paczka pól
   może się uciąć → niepoprawny JSON → 503. Chunking po 9000 znaków to łagodzi, ale przy
   gęstych formularzach to pierwszy podejrzany.
2. **Warianty „thinking” Qwen3** mogą zużyć budżet na rozumowanie i zwrócić puste
   `message.content` (treść w `reasoning_content`, którego nie czytamy) → 503 „pusta odpowiedź”.
   Nie wysyłamy `chat_template_kwargs: {"enable_thinking": false}`, więc należy wybrać wariant
   Instruct-2507 / non-thinking albo wyłączyć myślenie po stronie serwera.
3. **Kontekst serwera ≥ 16k**: 9000 znaków polskiego tekstu + prompt + 3072 tokenów wyjścia.
   `N_CTX = 8192` dotyczy wyłącznie trybu local.

## 5. Co zweryfikowane, a co nie

Zweryfikowane:

- `ruff check` + `ruff format --check` — czysto.
- `pytest` — **122/122** (87 wcześniejszych + 35 nowych), bez socketów i bez ładowania modelu;
  `test_licenses` i `test_convert_real_pipeline_not_ready` zielone.
- Smoke api na stubie loopback (realne sockety): `pipeline_mode=real`, 4 strony / 63 pola /
  5 zakładek, poprawny tytuł; `POST /v1/chat/completions` z Bearer; wszystkie trzy tryby
  `response_format`; fałszywy `HTTP_PROXY` ignorowany; 401 / timeout / HTML / nie-JSON → 503 z `***`.

Niezweryfikowane:

- **Jakość ekstrakcji na prawdziwym dużym modelu** — stub dowodzi okablowania, nie jakości.
- Smoke na `llama_cpp.server` — wymagałby dodatkowego pakietu `sse_starlette` (nieinstalowany).
- Wierność względem referencji COI: zrobione (strona „Dane wnioskodawcy”, strona sprawy wzorca nadpłaty,
  „Dane do kontaktu” i „Zgoda” wg Standardów) — branch `feat/coi-dokonczenie`.
- Lokalny koszt CPU dla Qwen3-4B: 156–655 s na formularz (pomiar wcześniejszy).

## 6. Ograniczenia i pułapki

- **Reguła o modelach** (AGENTS.md, README, docs/ARCHITECTURE.md): tylko modele z otwartymi wagami,
  na CPU; domyślnie lokalnie, tryb api dopuszczalny wyłącznie dla otwartego modelu, który da się
  postawić na własnej infrastrukturze. Pozostałe NFR-y obowiązują: wyłącznie licencje permisywne
  (zakaz GPL/AGPL/LGPL, w szczególności PyMuPDF), pliki użytkownika tylko w RAM, Docker
  non-root uid=10001, sekrety wyłącznie przez ENV w runtime.
- **Własność**: `backend/app/modules/` + `data/` = Branch 1; `frontend/` = Branch 2;
  `backend/app/{schemas,interfaces,api,core,services}` zamrożone (zmiany tylko za obopólną
  zgodą, małym commitem na `main`). `backend/tests/` de facto wspólne.
- `get_settings()` jest `lru_cache` — po zmianie ENV w teście `get_settings.cache_clear()`.
- `rapidocr-onnxruntime` przypięte `>=1.2.3,<1.3` — nie podnosić; `rapidocr` 3.x dociąga wagi z sieci.
- Host Python 3.13, Docker `python:3.14-slim`; `requires-python = ">=3.13"` jest celowe.
- Pydantic v2 `extra="forbid"`; nowe pole → aktualizacja schematu, mocka i testów. Po zmianie
  `schemas/form_schema.py` regenerować `data/schemas/coi_target_schema.json`.
- `CORS_ORIGINS` najbezpieczniej jako tablica JSON. Ruff line-length 110.
- `Materiały dla projketu/` i `testowe-formularze/` są gitignored — materiał konkursowy,
  nie commitować.
- Commity po polsku z diakrytykami; commit / push / merge tylko na wyraźną zgodę.

## 7. Wznowienie pracy w nowym czacie

> Jestem na branchu `feat/llm-backend-switch`. Przełącznik LLM local/api jest
> zaimplementowany i przetestowany (122/122), ale jakość na prawdziwym modelu niezweryfikowana.
> Chcę postawić endpoint OpenAI-compatible z Qwen i przegnać przez niego `testowe-formularze/`.
> Zacznij od `pytest` i `/health`, potem skonfiguruj `.env` na `LLM_BACKEND=api`. Uwaga na
> warianty thinking Qwen3 i kontekst serwera (sztywny limit `max_tokens` usunięty — decyduje
> serwer). Nie ruszaj dokumentacji NFR ani `frontend/`. Szczegóły: `docs/llm-backend-switch.md`.