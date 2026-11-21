# Architektura — COI Form Digitizer

## 1. Zasady nadrzędne

- **MVP = 100% zakresu COI**: batch processing, lokalne AI/OCR, słowniki (mapowanie + propozycje),
  przepływy warunkowe, zaawansowana walidacja (PESEL/NIP/REGON/telefon/kod), GUI z wizualizacją e-Usługi.
- **100% offline w runtime**: zero zewnętrznych API, chmurowych LLM, MCP i SaaS. Modele lokalnie w `MODELS_DIR`.
- **Zero copyleft**: wyłącznie MIT / Apache-2.0 / BSD / ISC / PSF / MPL-2.0. Zakaz GPL/AGPL
  (dlatego `pypdf`/`pdfplumber`/`pypdfium2`, a **nie** `PyMuPDF`). Test `test_licenses.py` egzekwuje to w CI.
- **Ulotne przetwarzanie (RAM-only)**: pliki żyją wyłącznie jako `bytes`/`BytesIO` w cyklu życia żądania.
  Żadnych trwałych zapisów na dysk (kontener dodatkowo `read_only: true`).

## 2. Przepływ danych (wszystko w RAM)

```
UploadFile (multipart)
   │  read_upload_limited() — strumieniowo do BytesIO, limit MAX_FILE_SIZE_BYTES
   ▼
validate_filename()      — FR-05/06: <=250 znaków, zakazane znaki, '..', znaki kontrolne
validate_file_content()  — FR-07/08: magic bytes (PDF/PNG/JPEG/OLE/ZIP→docx/xlsx/ods), zgodność z rozszerzeniem
   ▼
FormPipeline.convert(filename, content: bytes, mime_type)      [interfaces/pipeline.py]
   ├── USE_MOCK_PIPELINE=true  → MockFormPipeline (core, bogaty JSON demo)
   └── USE_MOCK_PIPELINE=false → RealFormPipeline:
         DocumentParser   (modules/ingestion)      → ParsedDocument
         StructureExtractor (modules/ai_extractor) → FormDocumentSchema (OCR + lokalny LLM)
         DomainEnricher   (modules/domain_logic)   → słowniki, propozycje, flows, walidacje
   ▼
ConversionResponse / BatchConversionResponse (schemas/api_models.py)
   ▼
Frontend SPA: edytor JSON + inspektor słowników + wizualizator e-Usługi (render z JSON)
```

## 3. Warstwy i własność plików (podział na 2 branche)

| Warstwa | Ścieżka | Właściciel |
|---|---|---|
| Kontrakty danych | `backend/app/schemas/` | **wspólne (zamrożone w core)** |
| Kontrakty pipeline | `backend/app/interfaces/` | **wspólne (zamrożone w core)** |
| API v1 | `backend/app/api/` | core (wspólne) |
| Konfiguracja, bezpieczeństwo | `backend/app/core/` | core (wspólne) |
| Orkiestrator + walidatory | `backend/app/services/` | core (wspólne) |
| Parsery, OCR/AI, logika domenowa | `backend/app/modules/` | **Branch 1 — wyłączność** |
| Baza słowników i schematy | `data/` | **Branch 1 — wyłączność** |
| GUI, batch UX, wizualizator | `frontend/` | **Branch 2 — wyłączność** |
| Testy | `backend/tests/` | core; każdy branch dopisuje własne pliki testów |

Zasada: **żaden branch nie edytuje plików wspólnej warstwy**. Zmiana kontraktu wymaga uzgodnienia
i osobnego, małego commita na `main` przed rozgałęzieniem. Dzięki temu merge obu branchy jest
bezkonfliktowy z definicji (rozłączne zbiory plików).

## 4. Kontrakty

### 4.1 `FormDocumentSchema` (wyjściowy JSON COI)

`metadata → pages[] → sections[] → fields[]`, plus:
- `validation_rules` per pole: `data_type`, `min/max_length`, `min/max_value`, `pattern`,
  `custom_validator` (PESEL, PL_PHONE, NIP, REGON, POSTAL_CODE), `error_message`;
- `dependencies` / `visibility_rules` / `conditional_flows`: jawne reguły
  `source_field_id + operator (EQUALS/NOT_EQUALS/IN/IS_EMPTY/GREATER_THAN) → target (FIELD/SECTION/PAGE) + action (SHOW/HIDE/REQUIRE/OPTIONAL/SET_VALUE)`;
- `dictionaries.mapped_dictionaries` (pole ↔ istniejący słownik COI + `confidence`),
  `dictionaries.proposed_dictionaries` (nowe słowniki wywnioskowane z treści, z `items[{code,label}]`).

Semantyka widoczności (przyjęta przez wizualizator): element z regułami `SHOW` jest widoczny
tylko, gdy co najmniej jedna reguła `SHOW` jest spełniona; reguła `HIDE` ukrywa element, gdy spełniona;
`REQUIRE`/`OPTIONAL` nadpisują wymaganość; `SET_VALUE` ustawia wartość przy przejściu warunku w stan spełniony.

### 4.2 `FormPipeline` (Protocol)

Branch 1 implementuje `DocumentParser`, `StructureExtractor`, `DomainEnricher` w `modules/`
i podpina je przez `RealFormPipeline` (kompozycja już istnieje w core). Przełączenie:
`USE_MOCK_PIPELINE=false`. Dopóki stuby rzucają `NotImplementedError`, API zwraca `503 PIPELINE_NOT_READY`.

## 5. Bezpieczeństwo (FR-05..FR-09)

- MIME po **magic bytes** (w tym inspekcja kontenerów ZIP: `word/document.xml`, `xl/workbook.xml`,
  `mimetype` ODS) — rozszerzenie samo w sobie nie wystarcza.
- Nazwa pliku: ≤250 znaków, zakaz `$ : > < / \ | ' * % ? "`, `..`, znaków kontrolnych.
- Limit rozmiaru i liczby plików w batchu z ENV; odczyt strumieniowy przerywany po przekroczeniu limitu.
- JWT (PyJWT, HS256): `JWT_AUTH_ENABLED`, sekret wyłącznie z runtime ENV;
  `POST /api/v1/auth/dev-token` tylko przy `ENV=development`.
- Kontener: `python:3.14-slim` (jawny tag), non-root uid 10001, `cap_drop: ALL`,
  `no-new-privileges`, `read_only` + tmpfs `/tmp`, tylko port 8000.

## 6. Walidacja współdzielona

`services/field_validation.py` zawiera **prawdziwe** sumy kontrolne PESEL (z walidacją daty),
NIP, REGON (9/14), format telefonu PL i kodu pocztowego. Endpoint `POST /forms/validate-field`
działa od razu — Branch 2 używa go do walidacji na żywo, Branch 1 reużywa funkcji w `domain_logic`.
`services/form_consistency.py` sprawdza spójność referencji (flows, słowniki, unikalność ID)
na potrzeby `POST /forms/validate-json`.

## 7. Workflow git (2 równoległe branche)

```
main (core: kontrakty + API + mock + GUI szkielet + testy)
 ├── feat/backend-ai-pipeline   [ Branch 1: modules/ + data/ ]
 └── feat/frontend-and-visualizer [ Branch 2: frontend/ ]
```

1. Oba branche startują z tego samego commita `main`.
2. Branch 1 rozwija parsery/OCR/LLM/słowniki, testuje przez `USE_MOCK_PIPELINE=false` + pytest.
3. Branch 2 rozwija GUI/wizualizator przeciwko **działającym mock endpointom** (pełne end-to-end od dnia 1).
4. Merge: najpierw Branch 1 (backend), potem Branch 2 — zbiory plików rozłączne, więc zero konfliktów.
5. Po merge: `USE_MOCK_PIPELINE=false`, wagi modeli w wolumenie `models_data`, pełny pipeline offline.
