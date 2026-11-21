# Frontend — wizualizator e-Usługi (FormDefinition)

Vanilla HTML/CSS/JS bez bundlera, serwowany statycznie przez FastAPI.

| Plik | Rola |
|---|---|
| `formdef.js` | Katalogi z materiałów konkursu (typy pól, walidatory, 6 słowników, źródła danych inicjalnych), walidatory wartości, konwerter formatu backendu → FormDefinition, walidator reguł 1–12 |
| `visualizer.js` | Renderer FormDefinition: strony MAIN/FINISH i `flow.links`, sekcje i sekcje powtarzające się, 11 typów pól, `readOnly` + `EXTERNAL_SYSTEM`, stany `states` (SHOW, READ_ONLY) liczone przez jsonata, podgląd wniosku |
| `app.js` | Import plików, edytor JSON, inspektor słowników, spięcie z API |
| `vendor/jsonata.min.js` | jsonata 1.8.7 (MIT, licencja w `vendor/jsonata.LICENSE.txt`) — lokalnie, bez sieci |
| `examples/` | Przykładowy FormDefinition wg wzoru „Wniosek o stwierdzenie nadpłaty podatku” |

Model danych dla zapytań jsonata: kopia formularza, w której każde pole ma `__userAnswer`
(opcje wyboru po nazwie, np. `"Polska" in ...field.__userAnswer`). Sekcja powtarzająca się ma
`__userAnswer` = lista wpisów `[{ fieldTechName, answers }]`, a zapytanie dostaje `$repeatingSectionIndex`.
Siatka sekcji jest dostępna pod kluczem `repetingSectionGrid` (schemat) i `repeatingSectionGrid` (przykłady jsonaty).
