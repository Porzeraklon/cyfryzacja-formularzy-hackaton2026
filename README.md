# COI Form Digitizer — Cyfryzacja formularzy (Wyzwanie COI)

Szkielet `core` (branch `main`) rozwiązania konkursowego: lokalny pipeline AI/OCR zamieniający
formularze urzędowe (PDF, DOC/DOCX, ODS/XLSX, JPG/PNG) w ustrukturyzowany JSON e-Usługi COI
oraz webowy wizualizator gotowej e-Usługi. **100% offline**, wyłącznie licencje permisywne,
przetwarzanie ulotne (RAM-only), Docker non-root.

Szkielet jest punktem wyjścia dla **2-osobowego zespołu** pracującego równolegle na rozłącznych
zbiorach plików (zero konfliktów merge) — patrz [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Szybki start (Docker — zalecane)

```bash
cp .env.example .env          # uzupełnij JWT_SECRET_KEY w produkcji!
docker compose up --build
# GUI + API: http://localhost:8000
# Swagger:   http://localhost:8000/docs
```

Obraz: oficjalny `python:3.14-slim` (jawny tag, zero `:latest`), użytkownik non-root `uid=10001`,
`read_only` + tmpfs, tylko port 8000. Sekrety i konfiguracja wyłącznie w runtime (`.env`).

## Szybki start (lokalnie, bez Dockera)

Wymagany Python **3.14** (produkcyjnie; lokalnie do developmentu wystarczy 3.13+):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # core + testy (lekko, bez modeli AI)
# Branch 1 dodatkowo: pip install -e ".[ai,llm]"
uvicorn backend.app.main:app --reload --port 8000
```

## Testy

```bash
pytest                        # walidacja plików, kontrakty API, polityka licencji
```

- `test_file_security.py` — MIME magic bytes, nazwa ≤250 znaków, znaki zakazane, limit rozmiaru, RAM-only.
- `test_api_contracts.py` — wszystkie endpointy (convert, batch, validate-json/field, dictionaries, JWT).
- `test_licenses.py` — **oblewa, gdy wykryje GPL/AGPL/LGPL** wśród zainstalowanych pakietów (`pip-licenses`).

## Zmienne środowiskowe (runtime)

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `ENV` | `development` | `development` aktywuje `/auth/dev-token`; `production` go wyłącza |
| `MAX_FILE_SIZE_BYTES` | `20971520` | limit rozmiaru pojedynczego pliku (20 MB) |
| `MAX_BATCH_FILES` | `20` | limit plików w `convert-batch` |
| `JWT_AUTH_ENABLED` | `false` | `true` = wymagany Bearer JWT na endpointach forms/dictionaries |
| `JWT_SECRET_KEY` | dev placeholder | **sekret wyłącznie z runtime ENV**, silny w produkcji |
| `JWT_ALGORITHM` / `JWT_EXPIRE_MINUTES` | `HS256` / `60` | parametry tokenu |
| `USE_MOCK_PIPELINE` | `true` | `false` podpina realny pipeline Brancha 1 |
| `MODELS_DIR` | `models` | katalog lokalnych wag AI/OCR (wolumen, offline) |
| `DICTIONARIES_DIR` | `data/dictionaries` | lokalna baza słowników COI |
| `CORS_ORIGINS` | localhost:8000 | lista originów po przecinku |

## REST API v1 (wszystko działa od razu w `core`)

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/v1/health` | status, wersja, tryb `mock`/`real`, status modeli |
| GET | `/api/v1/config` | publiczna konfiguracja dla frontendu |
| POST | `/api/v1/auth/dev-token` | testowy JWT (tylko `ENV=development`) |
| POST | `/api/v1/forms/convert` | pojedynczy plik → JSON COI (pełna walidacja FR-05..08) |
| POST | `/api/v1/forms/convert-batch` | wiele plików → wyniki per plik + podsumowanie |
| POST | `/api/v1/forms/validate-json` | schemat COI + spójność flows/słowników |
| POST | `/api/v1/forms/validate-field` | **prawdziwa** walidacja PESEL/telefon/kod/NIP/REGON |
| GET | `/api/v1/dictionaries` | lista słowników COI |
| GET | `/api/v1/dictionaries/{id}` | pozycje słownika |

Mock `convert` zwraca bogaty wniosek: **3 strony tematyczne**, zmapowane słowniki
(województwa, rodzaj dokumentu, płeć), **1 zaproponowany nowy słownik**, sekcję warunkową
(`case_type = "inna"` → pokaż `sec_case_other` + wymagaj opisu) oraz walidacje PESEL i telefonu.
Frontend (zakładki: Import → JSON i słowniki → Wizualizacja e-Usługi) działa z tym mockiem end-to-end.

Przykład:

```bash
curl -F "file=@wniosek.pdf" http://localhost:8000/api/v1/forms/convert
curl -X POST http://localhost:8000/api/v1/forms/validate-field \
  -H "Content-Type: application/json" \
  -d '{"value":"44051401458","rules":{"custom_validator":"PESEL"}}'
```

## Workflow git dla 2 branchy

```bash
# start: oba branche z tego samego commita main
git checkout -b feat/backend-ai-pipeline    # Osoba 1
git checkout -b feat/frontend-and-visualizer # Osoba 2 (z main!)
```

| | Branch 1 `feat/backend-ai-pipeline` | Branch 2 `feat/frontend-and-visualizer` |
|---|---|---|
| **Wyłączna własność** | `backend/app/modules/`, `data/` | `frontend/` |
| **Zakres** | parsery wieloformatowe, lokalny OCR (RapidOCR/onnxruntime), lokalny LLM (llama-cpp-python), mapowanie i propozycje słowników, wnioskowanie flows, reguły walidacji | import single/batch z postępem i błędami, edytor JSON + inspektor słowników, wizualizator e-Usługi na żywo (flows, słowniki, walidacja PESEL/telefon) |
| **Przełączenie** | `USE_MOCK_PIPELINE=false` po implementacji | działa od razu przeciw mock API |

Pliki wspólne (`schemas/`, `interfaces/`, `api/`, `core/`, `services/`) są zamrożone w `core` —
zmiany tylko za obopólną zgodą, małym commitem na `main`. Merge: najpierw Branch 1, potem Branch 2
(rozłączne zbiory plików ⇒ zero konfliktów).

## Polityka licencyjna (NFR)

Dozwolone: MIT, Apache-2.0, BSD-2/3, ISC, PSF, MPL-2.0. **Zakaz GPL/AGPL** — w szczególności
`PyMuPDF`/`fitz` (AGPL). Zamiast tego: `pypdf` (BSD), `pdfplumber` (MIT), `pypdfium2` (Apache/BSD),
`rapidocr-onnxruntime` + `onnxruntime` (Apache/MIT), `python-docx`/`openpyxl` (MIT),
`odfpy` (Apache-2.0 z dual), `llama-cpp-python` (MIT). Test licencji blokuje merge przy naruszeniu.

## Struktura

```
backend/app/{core,schemas,interfaces,services,api/v1,modules}   # modules/ = Branch 1
backend/tests/                                                  # security + kontrakty + licencje
frontend/                                                       # Branch 2 (SPA, zero bundlera)
data/{dictionaries,schemas}                                     # Branch 1
docs/ARCHITECTURE.md                                            # architektura + podział prac
Dockerfile / docker-compose.yml / .env.example / pyproject.toml
```
