# Jakość i czas ekstrakcji — pomiar na formularzach testowych COI

Narzędzie: `python -m backend.app.modules.ai_extractor.eval_quality testowe-formularze`.
Model: Qwen3-4B-Instruct-2507 Q4_K_M (GGUF, Apache-2.0), llama-cpp-python 0.3.36, **wyłącznie CPU**
(12 wątków logicznych), `LLM_CTX=8192`. Formularze: 8 dokumentów z materiałów konkursowych
(7× DOCX, 1× PDF); referencje: oficjalne konfiguracje COI dla „nadpłaty” i „numeru porządkowego”.
Pomiar jest jednorazowy (bez powtórzeń) — czasy na CPU wahają się o kilkadziesiąt procent.

## Zmiany między pomiarami

- tytuł i urząd z nagłówka deterministycznie (regex, 8/8 na formularzach testowych);
- LLM wyciąga 1–3 strony merytoryczne, bez sztucznego dzielenia jedynej strony na dwie;
- skracanie promptu (`compact_text`: wykropkowania → „____”) — 9.5% mniej znaków łącznie,
  do 59% na formularzu nadpłaty;
- urząd w sekcji „Urząd…” z dokumentu zamiast stałej wartości.

## Przed

| Plik | Czas [s] | Wywołania LLM | Czas LLM [s] | Prompt [znaki] | Strony | Pola | Błędy reguł | Ostrz. |
|---|---|---|---|---|---|---|---|---|
| Deklaracja o wysokości opłaty.docx | 1113.8 | 2 | 1113.1 | 18429 | 7 | 118 | 0 | 0 |
| Wniosek o stwierdzenie nadpłaty podatku.docx | 103.6 | 1 | 103.6 | 2158 | 5 | 62 | 0 | 1 |
| Wniosek o ustalenie numeru porządkowego nieruchomości.docx | 286.7 | 1 | 286.6 | 4580 | 5 | 59 | 0 | 1 |
| Wniosek o wydanie zaświadczenia.docx | 54.0 | 1 | 54.0 | 2478 | 5 | 60 | 0 | 0 |
| Wniosek o wydanie zezwolenia na psy ras agresywnych.pdf | 212.4 | 1 | 212.0 | 6384 | 5 | 68 | 0 | 0 |
| Wniosek o wydanie zezwolenia na zajęcie pasa drogowego.docx | 37.3 | 1 | 37.3 | 4774 | 4 | 58 | 0 | 1 |
| Wniosek o wynajęcie świetlicy.docx | 119.5 | 1 | 119.5 | 2622 | 5 | 68 | 0 | 0 |
| Wniosek o zwrot opłaty skarbowej.docx | 152.5 | 1 | 152.5 | 2823 | 5 | 65 | 0 | 0 |


| Plik | Pól w ref. | Pokrycie | Typ | Walidator | Słownik | Wymagalność | Nadmiarowe | Strony (my/ref) |
|---|---|---|---|---|---|---|---|---|
| Wniosek o stwierdzenie nadpłaty podatku.docx | 51 | 74% | 95% | 84% | 95% | 13% | 24 | 5/4 |
| Wniosek o ustalenie numeru porządkowego nieruchomości.docx | 49 | 71% | 94% | 83% | 94% | 17% | 24 | 5/4 |


## Po

| Plik | Czas [s] | Wywołania LLM | Czas LLM [s] | Prompt [znaki] | Strony | Pola | Błędy reguł | Ostrz. |
|---|---|---|---|---|---|---|---|---|
| Deklaracja o wysokości opłaty.docx | 1244.0 | 2 | 1243.3 | 18447 | 5 | 123 | 1 | 0 |
| Wniosek o stwierdzenie nadpłaty podatku.docx | 41.5 | 1 | 41.4 | 2167 | 4 | 55 | 0 | 0 |
| Wniosek o ustalenie numeru porządkowego nieruchomości.docx | 371.2 | 1 | 371.1 | 4589 | 4 | 59 | 0 | 0 |
| Wniosek o wydanie zaświadczenia.docx | 58.1 | 1 | 58.1 | 2487 | 4 | 60 | 0 | 0 |
| Wniosek o wydanie zezwolenia na psy ras agresywnych.pdf | 187.2 | 1 | 186.6 | 6393 | 4 | 62 | 0 | 0 |
| Wniosek o wydanie zezwolenia na zajęcie pasa drogowego.docx | 265.9 | 1 | 265.8 | 4783 | 4 | 81 | 1 | 0 |
| Wniosek o wynajęcie świetlicy.docx | 33.2 | 1 | 33.2 | 2631 | 4 | 55 | 0 | 0 |
| Wniosek o zwrot opłaty skarbowej.docx | 45.0 | 1 | 44.9 | 2832 | 4 | 55 | 0 | 0 |


| Plik | Pól w ref. | Pokrycie | Typ | Walidator | Słownik | Wymagalność | Nadmiarowe | Strony (my/ref) |
|---|---|---|---|---|---|---|---|---|
| Wniosek o stwierdzenie nadpłaty podatku.docx | 51 | 74% | 95% | 84% | 95% | 16% | 17 | 4/4 |
| Wniosek o ustalenie numeru porządkowego nieruchomości.docx | 49 | 71% | 94% | 83% | 94% | 17% | 24 | 4/4 |


## Wnioski

- **Strony:** wynik ma teraz tyle zakładek co referencje (4/4 zamiast 5/4).
- **Czas:** bez poprawy w sumie — 7 krótszych formularzy: ok. 966 s przed, ok. 1002 s po.
  Pojedyncze formularze zmieniają się mocno w obie strony (nadpłata 104→41 s, świetlica 120→33 s,
  zwrot opłaty 153→45 s, ale pas drogowy 37→266 s, numer porządkowy 287→371 s). Czas na CPU
  zależy głównie od liczby tokenów **wyjścia** (ile pól wygeneruje model), a nie od długości
  promptu, więc skracanie promptu daje niewiele. „Deklaracja” (18 tys. znaków, 2 wywołania)
  to ok. 20 min na CPU — kandydat do `LLM_BACKEND=api` z otwartym modelem na serwerze.
- **Jakość vs referencje:** pokrycie pól 74% / 71%, typ 95% / 94%, walidator 84% / 83%,
  słownik 95% / 94%. Braki to głównie pola specyficzne dla sprawy, które LLM nazwał inaczej
  (np. „Kwota nadpłaty” zamiast „Kwota”), oraz pola TERYT, które referencja ma jako osobne
  kontrolki („TERYT_4G_DOWODOWY”).
- **Wymagalność:** referencje COI mają pustą tablicę `validation` (bez REQUIRED), więc ta kolumna
  nie mierzy jakości — nasze REQUIRED wynika z Reguł technicznych.
- **Błąd znaleziony pomiarem:** zduplikowane UUID w eksporcie, gdy LLM zwróci dwa pola o tym
  samym identyfikatorze — poprawione (eksport gwarantuje unikalne `id`).
- **Skany (JPG/PNG):** OCR (RapidOCR) nie instaluje się na Pythonie 3.15 tego stanowiska, więc
  pomiar obrazów trzeba zrobić w Dockerze (Python 3.14). Detektor miejsc do wpisania sprawdzony
  na skanach „zaświadczenia” (10/10 pól) i „zwrotu opłaty” (ramki pól scalone; część kratek
  wyboru nadal pomijana).
