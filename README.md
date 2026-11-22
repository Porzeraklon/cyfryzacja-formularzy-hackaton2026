# formularzex — Cyfryzacja formularzy (Wyzwanie COI)

Aplikacja zamieniająca urzędowe formularze źródłowe (**PDF, DOC/DOCX, ODS/XLSX, JPG/JPEG/JPE, PNG**)
w ustrukturyzowaną e-Uługę COI:

1. **Ingestion** — parsowanie wieloformatowe i lokalny OCR (RapidOCR, modele ONNX wewnątrz wheela),
   wyłącznie w RAM (`BytesIO`, zero zapisów plików użytkownika na dysk).
2. **Ekstrakcja AI** — lokalny LLM (llama.cpp + wagi GGUF) rozpoznaje strony tematyczne, sekcje,
   etykiety, typy kontrolek i kolumny tabel powtarzalnych → `FormDocumentSchema`.
3. **Mapowanie COI** — słowniki urzędowe, sekcje standardowe (dane wnioskodawcy, zgody),
   wnioskowanie flows warunkowych, walidatory (PESEL, NIP, REGON, kod pocztowy, e-mail, ePUAP…).
4. **Eksport i wizualizacja** — `FormDefinition` w oficjalnym formacie COI + webowy wizualizator
   e-Uługi (czysty vanilla HTML/CSS/JS, zero frameworków i bundlerów).

Domyślnie **lokalnie na CPU** (`LLM_BACKEND=local`, bez GPU): zero wywołań zewnętrznych usług w runtime.
Opcjonalny tryb `LLM_BACKEND=api` podpina model z **otwartymi wagami** postawiony na własnej
infrastrukturze (endpoint OpenAI-compatible, np. vLLM, także w chmurze) — modele dostępne wyłącznie
przez API są wykluczone. Patrz [Konfiguracja](#3-konfiguracja).
Wyłącznie licencje permisywne (MIT/Apache-2.0/BSD/ISC/PSF/MPL-2.0), Docker non-root.

---

## 1. Uruchomienie — polecana metoda: `docker-compose.yml`

Wymagania: Docker z pluginem `compose` i nic więcej. Gotowe obrazy są na GHCR, a cała konfiguracja
wchodzi przez ENV **w runtime** (nic nie jest wypiekane w obrazie).

| Obraz | Zawartość | Rozmiar |
|---|---|---|
| `ghcr.io/porzeraklon/formularzex:0.2.1` | GUI + całe REST API + realistyczny **mock pipeline** (bez modeli AI) | ~160 MB |
| `ghcr.io/porzeraklon/formularzex:0.2.1-ai` | to samo + **realny pipeline**: OCR RapidOCR + lokalny LLM llama.cpp (i tryb `api`) **z wagą GGUF w obrazie** | ~3,5 GB |

Obraz `-ai` ma wypieczoną wagę `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (2,33 GiB, Apache-2.0)
w `/app/models` — po `docker pull` działa od razu, **bez pobierania modelu i bez wolumenu**.

Obrazy są **publiczne** — `docker pull` działa bez logowania do GHCR.

Po starcie (oba warianty):

- GUI (Import → JSON i słowniki → Wizualizator e-Uługi): **http://localhost:8000**
- Swagger (interaktywna dokumentacja API): **http://localhost:8000/docs**
- Health: **http://localhost:8000/api/v1/health**

### A. Compose z konfiguracją wprost w pliku (nie wymaga `.env`)

Samowystarczalny plik — zapisz jako `docker-compose.yml` w dowolnym katalogu i uruchom
`docker compose up -d`. Słowniki COI i frontend są w obrazie, więc żadne wolumeny nie są potrzebne:

```yaml
services:
  app:
    image: ghcr.io/porzeraklon/formularzex:0.2.1   # jawny tag, zero :latest
    ports:
      - "8000:8000"                  # wyłącznie port aplikacji
    environment:
      ENV: development               # production wyłącza /auth/dev-token
      USE_MOCK_PIPELINE: "true"      # "false" = realny pipeline (obraz -ai, waga GGUF w środku)
      MODELS_DIR: models
      DICTIONARIES_DIR: data/dictionaries
      LLM_BACKEND: local             # local = GGUF offline | api = endpoint OpenAI-compatible
      MAX_FILE_SIZE_BYTES: "20971520"
      MAX_BATCH_FILES: "20"
      JWT_AUTH_ENABLED: "false"      # w produkcji "true" + silny sekret
      JWT_SECRET_KEY: change-me-to-a-long-random-secret-in-production
      JWT_ALGORITHM: HS256
      JWT_EXPIRE_MINUTES: "60"
      CORS_ORIGINS: '["http://localhost:8000","http://127.0.0.1:8000"]'
    read_only: true                  # przetwarzanie plików wyłącznie w RAM
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
```

`CORS_ORIGINS` **musi być tablicą JSON** (patrz [Konfiguracja](#3-konfiguracja)).
W produkcji sekret podawaj przez ENV powłoki (`JWT_SECRET_KEY=$(openssl rand -hex 32)`)
albo `.env` — nie commituj go w pliku compose.

### B. Compose ciągnący konfigurację z `.env`

Wariant z repozytorium: wartości trzymasz w `.env` (gitignore), compose je tylko wczytuje.

```bash
cp .env.example .env        # w produkcji ustaw silny JWT_SECRET_KEY!
docker compose up -d
docker compose logs -f
```

```yaml
services:
  app:
    image: ghcr.io/porzeraklon/formularzex:0.2.1
    ports:
      - "8000:8000"
    env_file:
      - path: .env                   # konfiguracja i sekrety WYŁĄCZNIE w runtime
        required: false              # brak .env = wartości domyślne z aplikacji
    volumes:
      - ./data:/app/data:ro          # baza słowników COI (podmiana bez przebudowy obrazu)
      - models_data:/app/models:ro   # katalog na lokalne wagi AI (pusty w trybie mock)
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped

volumes:
  models_data:
```

### C. Realny pipeline AI w Dockerze (OCR + lokalny LLM)

Obraz `-ai` ma zainstalowane extras `[ai,llm,llm-api]` **oraz wagę GGUF w `/app/models`**
(pobrana i zweryfikowana sumą sha256 na etapie build, nie w runtime). Nie musisz nic pobierać
ani montować — `/health` raportuje `llm: ready` od pierwszego startu:

```yaml
services:
  app-ai:
    image: ghcr.io/porzeraklon/formularzex:0.2.1-ai
    ports:
      - "8000:8000"
    environment:
      USE_MOCK_PIPELINE: "false"
      LLM_BACKEND: local             # albo api + LLM_API_URL/LLM_MODEL/LLM_API_KEY
      MODELS_DIR: models
      CORS_ORIGINS: '["http://localhost:8000","http://127.0.0.1:8000"]'
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
```

**Pułapka wolumenu:** zamontowanie `./models:/app/models` (np. pustego katalogu z klona)
**przesłania** wagę z obrazu i `/health` wraca do `llm: missing`. Montuj ten katalog tylko
świadomie, gdy chcesz podmienić model na inny plik `*.gguf`:

```yaml
    volumes:
      - ./models:/app/models:ro      # własna waga zamiast wypieczonej
```

W repozytorium ten sam wariant jest usługą `app-ai` w profilu `ai`:

```bash
docker compose --profile ai up -d app-ai
```

Obie usługi nasłuchują na 8000 — uruchamiaj **jedną naraz** (`docker compose down` przed switchem).
Pierwsza konwersja ładuje model do RAM (kilka–kilkadziesiąt sekund); `/health` pokazuje
`llm: ready`, gdy waga jest na miejscu.

### D. Budowanie obrazu lokalnie (zamiast pobierania z GHCR)

`Dockerfile` jest wieloetapowy: etap build kompiluje zależności (m.in. `llama-cpp-python`)
w venv, etap finalny nie ma narzędzi buildowych.

```bash
docker compose build                        # core (EXTRAS="")
docker compose --profile ai build app-ai    # realny pipeline (EXTRAS="ai,llm,llm-api") + waga GGUF

# albo ręcznie, z jawnym tagiem (zakaz :latest)
docker build -t formularzex:0.2.1 .
docker build --build-arg EXTRAS=ai,llm,llm-api -t formularzex:0.2.1-ai .
```

Build wariantu `-ai` pobiera **2,33 GiB** wagi z Hugging Face. Warstwa wag leży **przed** `COPY`
kodu i venv, więc zmiany kodu ani dokumentacji nie pobierają jej ponownie (i nie wysyłają jej
ponownie do rejestru). Katalog `models/` z repozytorium **nie jest** używany
przez build (`.dockerignore`), więc obraz buduje się też z czystego klona. Waga jest przypięta
rewizją repo i sumą kontrolną, a niezgodność sha256 **przerywa build**:

```bash
docker build --build-arg EXTRAS=ai,llm,llm-api \
  --build-arg LLM_GGUF_REPO=unsloth/Qwen3-4B-Instruct-2507-GGUF \
  --build-arg LLM_GGUF_REVISION=a06e946bb6b655725eafa393f4a9745d460374c9 \
  --build-arg LLM_GGUF_FILE=Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  --build-arg LLM_GGUF_SHA256=3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597 \
  -t formularzex:0.2.1-ai .
```

Inny model otwarty = podmień te cztery argumenty (pamiętaj o licencji permisywnej i CPU).

Właściwości obrazu (NFR konkursowe):

- baza `python:3.14-slim` — **jawny tag wersji**, zero `:latest`,
- użytkownik **non-root `uid=10001`**, `read_only` + tmpfs `/tmp`, `cap_drop: ALL`,
  `no-new-privileges:true` (compose),
- **sekrety i konfiguracja wyłącznie w runtime** (ENV/`.env`) — nic na etapie build,
- wariant `-ai`: waga GGUF pobierana **na etapie build** (spoza runtime) i wypiekana w obrazie
  jako `appuser` — runtime pozostaje w 100% offline,
- `HEALTHCHECK` odpytuje `GET /api/v1/health` (interval 30 s, timeout 5 s),
- eksponowany wyłącznie port **8000**,
- `CMAKE_ARGS=-DGGML_NATIVE=OFF` — llama.cpp bez instrukcji specyficznych dla maszyny build
  (przenośność obrazu między CPU).

## 2. Uruchomienie lokalne (bez Dockera)

### A. Realny pipeline AI (tryb konkursowy, CPU, bez sieci)

Wymagany Python **3.13+** (host deweloperski 3.13; obraz Dockera celuje w 3.14):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ai,llm,llm-api,dev]"   # parsery + OCR + lokalny LLM + klient api + testy
```

Wagi modeli (pobierane **ręcznie, poza runtime** — aplikacja nie sięga do sieci):

- **OCR**: RapidOCR ma modele ONNX wewnątrz wheela — nic nie pobierasz.
- **LLM**: umieść plik GGUF w katalogu `MODELS_DIR` (domyślnie `models/`), np.
  `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` (repozytorium `unsloth/Qwen3-4B-Instruct-2507-GGUF`,
  licencja Apache-2.0, sha256 `3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597`).
  Wybierany jest pierwszy plik `*.gguf` (preferowane nazwy z `qwen3-4b`).
  W Dockerze ten krok nie jest potrzebny — obraz `-ai` ma wagę w środku (sekcja 1C).

Konfiguracja `.env` dla realnego pipeline offline:

```dotenv
USE_MOCK_PIPELINE=false
LLM_BACKEND=local
```

Start:

```bash
uvicorn backend.app.main:app --port 8000
# GUI: http://localhost:8000   Swagger: http://localhost:8000/docs
```

Brak wag GGUF albo bibliotek extras → `/health` raportuje `llm=missing` (`status: degraded`),
a `convert` zwraca **503 `PIPELINE_NOT_READY`** z instrukcją naprawy (bez cichego fallbacku).

### B. Opcjonalnie: duży LLM na endpointcie OpenAI-compatible (`LLM_BACKEND=api`)

Gdy urząd dysponuje własnym serwerem modelu (vLLM, llama.cpp server…), aplikacja może zamiast
lokalnego GGUF wołać ten endpoint — **wyłącznie wskazany adres** i wyłącznie model z otwartymi wagami,
który da się uruchomić na własnej infrastrukturze (np. Qwen, Bielik, Llama na licencji permisywnej):

```dotenv
USE_MOCK_PIPELINE=false
LLM_BACKEND=api
LLM_API_URL=http://llm-host:8000/v1     # albo pełny URL .../chat/completions
LLM_MODEL=qwen2.5-72b-instruct
LLM_API_KEY=sekret-tylko-w-runtime      # opcjonalny; nagłówek Bearer tylko gdy niepusty
LLM_TIMEOUT_SECONDS=600
LLM_RESPONSE_FORMAT=json_schema         # json_schema | json_object | none
```

Zasady trybu `api`:

- klient **nie podąża za przekierowaniami** i ignoruje `HTTP(S)_PROXY` (`trust_env=False`) —
  ruch nie wychodzi poza wskazany endpoint,
- błąd endpointu (timeout, HTTP 5xx, nie-JSON, pusta odpowiedź) = **twarde 503**, bez fallbacku
  na `local`; klucz API jest redagowany (`***`) w komunikatach błędów,
- `ready()` sprawdza wyłącznie konfigurację (nie osiągalność) — `/health` nie wykonuje zapytań
  sieciowych (timeout HEALTHCHECK Dockera to 5 s),
- bez `max_tokens` — o limicie wyjścia decyduje serwer/model; `json_schema` wymusza strukturę
  odpowiedzi (strict structured output), `json_object` gwarantuje tylko poprawny JSON.

Szczegóły: [docs/llm-backend-switch.md](docs/llm-backend-switch.md).

## 3. Konfiguracja

Cała konfiguracja przez **zmienne środowiskowe** (runtime) lub plik `.env` (gitignore;
wzorzec: `.env.example`). Sekrety nigdy nie trafiają do obrazu Dockera ani repozytorium.

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `ENV` | `development` | `development` aktywuje `/auth/dev-token`; `production` go wyłącza |
| `USE_MOCK_PIPELINE` | `true` | `false` = realny pipeline AI (OCR + LLM + mapowanie COI) |
| `MODELS_DIR` | `models` | katalog lokalnych wag GGUF (w obrazie `-ai` waga już tam jest; OCR nie wymaga wag) |
| `DICTIONARIES_DIR` | `data/dictionaries` | lokalna baza słowników COI |
| `LLM_BACKEND` | `local` | `local` = llama.cpp + GGUF (offline); `api` = endpoint OpenAI-compatible |
| `LLM_API_URL` | — | bazowy URL endpointu (`/chat/completions` dopisywane automatycznie) |
| `LLM_API_KEY` | — | klucz Bearer (SecretStr; redagowany w błędach, niewidoczny w repr) |
| `LLM_MODEL` | — | nazwa modelu na endpointcie (wymagana w trybie `api`) |
| `LLM_TIMEOUT_SECONDS` | `600` | timeout żądania do endpointu (połączenie: 10 s) |
| `LLM_THREADS` | `0` | tryb local: wątki llama.cpp (0 = wszystkie rdzenie logiczne) |
| `LLM_CTX` | `8192` | tryb local: rozmiar kontekstu modelu GGUF |
| `LLM_RESPONSE_FORMAT` | `json_object` | `json_schema` (strict, zalecane) / `json_object` / `none` |
| `MAX_FILE_SIZE_BYTES` | `20971520` | limit rozmiaru pojedynczego pliku (20 MB) |
| `MAX_BATCH_FILES` | `20` | limit plików w `convert-batch` |
| `JWT_AUTH_ENABLED` | `false` | `true` = wymagany Bearer JWT na endpointach forms/dictionaries |
| `JWT_SECRET_KEY` | dev placeholder | **silny sekret wyłącznie z runtime ENV w produkcji** |
| `JWT_ALGORITHM` / `JWT_EXPIRE_MINUTES` | `HS256` / `60` | parametry tokenu |
| `CORS_ORIGINS` | localhost:8000 | dozwolone originy — **w `.env` tablica JSON** (patrz niżej) |
| `API_V1_PREFIX` | `/api/v1` | prefiks wszystkich endpointów API |

**Pułapka `CORS_ORIGINS`:** pydantic-settings dekoduje pola listowe z `.env` jako JSON
*przed* walidatorami — zapis po przecinku wywala start aplikacji. W `.env` używaj tablicy:

```dotenv
CORS_ORIGINS=["http://localhost:8000","http://127.0.0.1:8000"]
```

(Wartość po przecinku działa tylko jako prawdziwa zmienna środowiskowa, np. `docker run -e`.)

Dozwolone rozszerzenia plików: `.pdf .doc .docx .ods .xlsx .jpg .jpeg .jpe .png`
(weryfikacja: nazwa ≤250 znaków, znaki zakazane, rozmiar, **MIME po magic bytes**, nie po rozszerzeniu).

## 4. REST API

Bazowy prefiks: **`/api/v1`**. Interaktywna dokumentacja (Swagger): `/docs`.
Autoryzacja: gdy `JWT_AUTH_ENABLED=true`, endpointy `forms/*` i `dictionaries/*` wymagają
nagłówka `Authorization: Bearer <JWT>` (token testowy: `POST /auth/dev-token` w `ENV=development`).

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/health` | status `ok`/`degraded`, wersja, tryb `mock`/`real`, status modeli (`ocr`, `llm`: `ready`/`missing`) |
| GET | `/config` | publiczna konfiguracja dla frontendu (rozszerzenia, limity, JWT) |
| POST | `/auth/dev-token` | testowy JWT — wyłącznie `ENV=development` (inaczej 403) |
| POST | `/forms/convert` | multipart `file` → pełna konwersja: `FormDocumentSchema` + `FormDefinition` |
| POST | `/forms/convert-batch` | multipart `files` (≤ `MAX_BATCH_FILES`) → wynik per plik + podsumowanie |
| POST | `/forms/validate-json` | walidacja JSON względem schematu COI + spójność flows/słowników |
| POST | `/forms/export-formdefinition` | `FormDocumentSchema` → oficjalny `FormDefinition` COI + notatki + uwagi walidatora |
| POST | `/forms/validate-formdefinition` | walidacja `FormDefinition`: schemat JSON, reguły 1–12 z Reguł technicznych, Standardy |
| POST | `/forms/validate-field` | walidacja pojedynczej wartości (PESEL, NIP, REGON, ZIP_CODE, EMAIL, EPUAP, ADE…) |
| GET | `/dictionaries` | lista lokalnych słowników COI |
| GET | `/dictionaries/{id}` | pozycje słownika (404 `NOT_FOUND` dla nieznanego id) |

### Odpowiedź `convert`

```jsonc
{
  "status": "success",
  "form": {                    // FormDocumentSchema: metadata, pages[].sections[].fields[],
    "metadata": { "form_id": "...", "title": "...", "generated_at": "..." },
    "pages": [ /* strony TEMATYCZNE (nie fizyczne strony PDF): sekcje, pola,
                  options, repeatable_group (kolumny w fields), help_text */ ]
  },
  "form_definition": { /* eksport w oficjalnym formacie COI (tabs, questions, dictionaries) */ },
  "form_definition_issues": [ /* uwagi walidatora reguł 1–12: {level, path, rule, message}; [] = zgodny */ ],
  "processing": {
    "filename": "wniosek.pdf", "mime_type": "application/pdf", "size_bytes": 12345,
    "processing_time_ms": 42000.0, "pipeline_mode": "real", "pages_count": 4, "fields_count": 66
  },
  "warnings": [ /* np. propozycje nowych słowników, ograniczenia mapowania */ ]
}
```

### Błędy — jednolity format

```json
{ "error": { "code": "PIPELINE_NOT_READY", "message": "...", "details": { } } }
```

| HTTP | Kod | Kiedy |
|---|---|---|
| 400 | `FILE_VALIDATION_ERROR` | zła nazwa/rozmiar/MIME pliku lub niepoprawny JSON formularza |
| 400 | `BATCH_TOO_LARGE` | batch przekracza `MAX_BATCH_FILES` |
| 401 | `AUTHENTICATION_REQUIRED` | brak/niepoprawny JWT (gdy auth włączony) |
| 403 | `FORBIDDEN` | np. `dev-token` poza `ENV=development` |
| 404 | `NOT_FOUND` | nieznany słownik |
| 422 | `REQUEST_VALIDATION_ERROR` | nieprawidłowe parametry żądania |
| 503 | `PIPELINE_NOT_READY` | brak wag/bibliotek AI, błąd endpointu LLM, zły JSON z modelu |

### Przykłady

```bash
# konwersja pliku (realny pipeline: USE_MOCK_PIPELINE=false + wagi w MODELS_DIR)
curl -F "file=@wniosek.pdf" http://localhost:8000/api/v1/forms/convert

# walidacja PESEL
curl -X POST http://localhost:8000/api/v1/forms/validate-field \
  -H "Content-Type: application/json" \
  -d '{"value":"44051401458","rules":{"custom_validator":"PESEL"}}'

# sprawdzenie stanu aplikacji
curl http://localhost:8000/api/v1/health
```

---

## Testy i jakość

```bash
pytest          # 180 testów: bezpieczeństwo plików, kontrakty API, walidatory, reguły 1–12, backend LLM, licencje
ruff check backend/ && ruff format --check backend/
```

- `test_file_security.py` — MIME magic bytes, nazwa ≤250 znaków, znaki zakazane, limit rozmiaru, RAM-only.
- `test_api_contracts.py` — wszystkie endpointy (convert, batch, validate-json/field, dictionaries, JWT).
- `test_llm_backend.py` / `test_extractor_llm.py` — przełącznik backendu, klient api i ekstrakcja:
  **zero sieci i zero modelu** (`httpx.MockTransport`, fałszywe GGUF).
- `test_pipeline_readiness.py` — `/health` raportuje brak **bibliotek** (nie tylko wag), a brak extras
  daje 503 `PIPELINE_NOT_READY`, nie 500.
- `test_form_definition_validation.py` — reguły 1–12 FormDefinition (każda łapana na zepsutym wariancie).
- `test_field_validation.py` — sumy kontrolne PESEL/NIP/REGON/dowód i formaty ZIP_CODE/EMAIL/EPUAP/ADE.

Pomiar jakości i czasu na prawdziwych formularzach (wymaga modelu; referencje `<plik>.reference.json`):

```bash
python -m backend.app.modules.ai_extractor.eval_quality testowe-formularze --report docs/jakosc-ekstrakcji.md
```

- `test_licenses.py` — **oblewa przy GPL/AGPL/LGPL** wśród zainstalowanych pakietów (`pip-licenses`);
  dual-license wyłącznie przez świadomą allowlistę (obecnie `odfpy` → Apache-2.0).

Testy zakładają neutralne ENV (np. `LLM_BACKEND=local`); przy `.env` z trybem `api` nadpisz:
`LLM_BACKEND=local LLM_API_URL= LLM_MODEL= pytest`.

## Polityka licencyjna (NFR)

Dozwolone: MIT, Apache-2.0, BSD-2/3, ISC, PSF, MPL-2.0. **Zakaz GPL/AGPL/LGPL** — w szczególności
`PyMuPDF`/`fitz` (AGPL). Zamiast tego: `pypdf` (BSD), `pdfplumber` (MIT), `pypdfium2` (Apache/BSD),
`rapidocr-onnxruntime` + `onnxruntime` (Apache/MIT), `python-docx`/`openpyxl` (MIT),
`odfpy` (Apache-2.0 z dual), `llama-cpp-python` (MIT), `httpx` (BSD).

## Struktura repozytorium

```
backend/app/{core,schemas,interfaces,services,api/v1}   # warstwy wspólne (kontrakty, API, security)
backend/app/modules/{ingestion,ai_extractor,...}        # pipeline AI: parsery, OCR, LLM, mapowanie COI
backend/tests/                                          # security + kontrakty + LLM + licencje
frontend/                                               # SPA: import, edytor JSON, wizualizator (vanilla)
data/{dictionaries,schemas}                             # słowniki COI + schemat docelowy
docs/ARCHITECTURE.md                                    # architektura i podział prac
docs/llm-backend-switch.md                              # przełącznik LLM local/api — szczegóły
Dockerfile / docker-compose.yml / .env.example / pyproject.toml
```

Materiały konkursowe (`testowe-formularze/`, `Materiały dla projketu/`) są gitignore —
nie trafiają do repozytorium.
