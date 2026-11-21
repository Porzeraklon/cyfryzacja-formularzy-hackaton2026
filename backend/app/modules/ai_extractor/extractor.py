"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Lokalny LLM (llama-cpp-python, wagi GGUF w MODELS_DIR) rozpoznający układ
dokumentu: sekcje, etykiety i typy kontrolek -> FormDocumentSchema.
OCR stron obrazowych odbywa się w ingestion (ParsedDocument niesie tekst i tabele).
Gramatyka GBNF z response_format wymusza JSON zgodny ze schematem wewnętrznym.
100% offline, niski koszt CPU/RAM. Zero wywołań zewnętrznych API.

Przebieg:
1. Dokument dzielony jest na fragmenty (strony PDF/arkusze albo kawałki długiego tekstu
   <= MAX_PAGE_CHARS); tabele strony trafiają do promptu tej strony, tabele bez strony - do
   fragmentu zawierającego ich nagłówek, a w ostateczności do osobnego wywołania LLM.
2. Każdy fragment -> jedno wywołanie LLM -> sekcje i pola. Tabele do wypełnienia to pola
   repeatable_group z kolumnami (`fields`); gdy LLM ich nie zwróci, działa deterministyczny
   fallback budujący grupę z nagłówków tabeli.
3. Sekcje kontynuowane między fragmentami są scalane, a następnie grupowane w strony
   tematyczne wg kolejności ze Standardów (dane stron -> sprawa -> załączniki ->
   kontakt i oświadczenia) z limitem pól na stronę. Stronę FINISH dokłada logika domenowa.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.exceptions import PipelineNotReadyError
from backend.app.interfaces.pipeline import ParsedDocument
from backend.app.schemas.form_schema import (
    ConsentItem,
    DictionaryItem,
    FieldDefinition,
    FieldType,
    FormDocumentSchema,
    FormMetadata,
    PageDefinition,
    SectionDefinition,
)

MAX_PAGE_CHARS = 9000
MAX_TABLE_CHARS = 2500  # budżet tekstu tabel doklejanych do promptu fragmentu
MAX_TABLE_ROWS_IN_PROMPT = 15
MAX_OUTPUT_TOKENS = 3072
TITLES_MAX_TOKENS = 256
N_CTX = 8192
MAX_TITLE_LENGTH = 255  # reguła 1 Reguł technicznych: tytuł formularza do 255 znaków
# Strony tematyczne: dzielimy zbyt długie, łączymy zbyt krótkie (Standardy: grupuj tematycznie).
MAX_FIELDS_PER_PAGE = 12
MIN_FIELDS_PER_PAGE = 4

# Wstrzykiwalny backend LLM: (messages, json_schema) -> surowy tekst JSON odpowiedzi.
LlmCompletion = Callable[[list[dict[str, str]], dict[str, Any]], str]

_PL_TRANSLIT = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")

_SYSTEM_PROMPT = (
    "Jesteś silnikiem cyfryzacji polskich formularzy urzędowych. "
    "Otrzymujesz tekst fragmentu dokumentu (strony lub jej części) oraz tabele rozpoznane w tym "
    "fragmencie i zwracasz WYŁĄCZNIE JSON opisujący strukturę formularza: tytuł fragmentu, "
    "sekcje i pola. "
    "Zasady: "
    "1) Wyodrębnij tylko pola wynikające z tekstu - nie zmyślaj pól. "
    "2) name to krótki identyfikator snake_case (np. pesel, imie, data_urodzenia); "
    "label to oryginalna etykieta pola z dokumentu, po polsku. "
    "3) field_type: text (krótki tekst), textarea (dłuższy opis, uzasadnienie), number, date, "
    "select (lista powyżej 5 opcji), radio (jednokrotny wybór), checkbox (bez options: pojedyncze "
    "pole tak/nie; z options: wielokrotny wybór), file (dokument do dołączenia), "
    "repeatable_group, consent_section, text_block. "
    "4) Tabela do wypełnienia (nagłówki kolumn i puste wiersze, np. lista osób, nieruchomości, "
    "pojazdów, okresów) to JEDNO pole repeatable_group: label mówi, czego dotyczy tabela, a fields "
    "to jej kolumny (name, label, field_type: text, number, date, select, checkbox lub textarea). "
    "Pomiń kolumnę 'Lp.' i nie twórz osobnych pól dla wierszy tabeli. Tabele z wypełnionymi "
    "danymi (np. cennik, stawki opłat) nie są polami. "
    "5) consent_section: zgody i oświadczenia, które wnioskodawca zaznacza lub podpisuje "
    "('Oświadczam, że...', 'Wyrażam zgodę...'); w options podaj treść kolejnych oświadczeń (label). "
    "Nie używaj do nich checkbox. "
    "6) text_block: ważna instrukcja lub pouczenie, które obywatel musi przeczytać (label: krótki "
    "tytuł, help_text: treść w skrócie). Pomijaj instrukcje wypełniania papierowego druku "
    "('wypełnić drukowanymi literami', 'niepotrzebne skreślić') i nagłówki niebędące polami. "
    "7) Dla select/radio/checkbox z wyborem podaj options (code + label) wyliczone z treści. "
    "8) required=true tylko gdy dokument oznacza pole jako obowiązkowe (gwiazdka, 'wymagane'). "
    "9) Sekcje odpowiadają tematycznym częściom dokumentu (np. dane wnioskodawcy, adres, "
    "przedmiot wniosku, załączniki, oświadczenia); title to nagłówek sekcji z dokumentu. Jeśli "
    "fragment zaczyna się kontynuacją sekcji z poprzedniej strony, powtórz jej tytuł. "
    "10) document_title: pełny tytuł formularza (np. 'Wniosek o ...'), jeśli występuje w tekście; "
    "w przeciwnym razie null. "
    "11) Jeśli fragment nie zawiera żadnych pól, zwróć pustą listę sections."
)

_TITLES_PROMPT = (
    "Nadajesz tytuły stronom polskiego formularza elektronicznego e-usługi. Dla każdej strony "
    "otrzymujesz tytuły jej sekcji. Zwróć WYŁĄCZNIE JSON z listą titles: po jednym krótkim tytule "
    "(2-5 słów, po polsku, bez numeracji) dla każdej strony, w tej samej kolejności i liczbie."
)


class _LlmFieldType(str, Enum):
    """Typy pól, które LLM może zwrócić na wejściu (bez info_section - strona końcowa)."""

    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    SELECT = "select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    TEXTAREA = "textarea"
    FILE = "file"
    REPEATABLE_GROUP = "repeatable_group"
    CONSENT_SECTION = "consent_section"
    TEXT_BLOCK = "text_block"


class _LlmColumnType(str, Enum):
    """Proste typy kolumn sekcji powtarzającej się (bez zagnieżdżania)."""

    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    SELECT = "select"
    CHECKBOX = "checkbox"
    TEXTAREA = "textarea"


class _LlmOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    label: str


class _LlmColumn(BaseModel):
    """Kolumna tabeli do wypełnienia. Celowo bez rekurencji - gramatyka GBNF llama.cpp."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    field_type: _LlmColumnType = _LlmColumnType.TEXT
    required: bool = False
    options: list[_LlmOption] | None = None


class _LlmField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    field_type: _LlmFieldType
    required: bool = False
    help_text: str | None = None
    options: list[_LlmOption] | None = None
    fields: list[_LlmColumn] | None = None  # kolumny dla repeatable_group


class _LlmSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str | None = None
    fields: list[_LlmField] = Field(default_factory=list)


class _LlmPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str | None = None
    document_title: str | None = None  # tytuł całego formularza, jeśli widoczny we fragmencie
    sections: list[_LlmSection] = Field(default_factory=list)


class _LlmPageTitles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titles: list[str] = Field(default_factory=list)


# llama.cpp nie jest thread-safe: jedna instancja na plik GGUF, inference serializowane.
_LLM_CACHE: dict[str, Any] = {}
_LLM_LOCK = threading.Lock()


def _slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value.translate(_PL_TRANSLIT))
    ascii_value = ascii_value.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_value).strip("_").lower()
    return slug[:60] or "pole"


def _norm(value: str) -> str:
    """Tekst do porównań: bez diakrytyków, małe litery, tylko alfanumeryczne + spacje."""
    ascii_value = unicodedata.normalize("NFKD", (value or "").translate(_PL_TRANSLIT))
    ascii_value = ascii_value.encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_value).split())


def _labels_match(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = sorted((na, nb), key=len)
    return len(shorter) >= 3 and f" {shorter} " in f" {longer} "


def _find_gguf(models_dir: Path) -> Path | None:
    if not models_dir.is_dir():
        return None
    files = sorted(models_dir.glob("*.gguf"))
    if not files:
        return None
    preferred = [f for f in files if "qwen3-4b" in f.name.lower()]
    return (preferred or files)[0]


def _load_llm(model_path: Path) -> Any:
    key = str(model_path.resolve())
    with _LLM_LOCK:
        if key in _LLM_CACHE:
            return _LLM_CACHE[key]
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise PipelineNotReadyError(
                "Brak biblioteki llama-cpp-python. Zainstaluj extras: pip install -e '.[llm]'.",
                {"reason": str(exc)},
            ) from exc
        try:
            llm = Llama(
                model_path=key,
                n_ctx=N_CTX,
                n_threads=os.cpu_count() or 4,
                n_gpu_layers=0,
                verbose=False,
            )
        except Exception as exc:
            raise PipelineNotReadyError(
                f"Nie udało się wczytać modelu GGUF: {model_path.name}.",
                {"reason": str(exc)},
            ) from exc
        _LLM_CACHE[key] = llm
        return llm


# --------------------------------------------------------------------------------------------
# Tabele
# --------------------------------------------------------------------------------------------

Table = list[list[str]]

_LP_HEADER = re.compile(r"^(lp|l p|nr|nr poz|poz|nr kol)$")
_FILLER_CELL = re.compile(r"^[\s._\-–—…:/]*\d{0,3}[.)]?[\s._\-–—…:/]*$")
_DATE_HEADER = re.compile(r"\bdat[aey]?\b|\bdnia\b|\btermin\b|\bod kiedy\b|\bdo kiedy\b")
_NUMBER_HEADER = re.compile(
    r"\bkwot|\bwartos|\bliczb|\bilos|\bpowierzchni|\bzl\b|\bcena\b|\bsum[aye]\b|\bprocent|\bstawk"
    r"|\bm2\b|\bmetraz|\brok\b|\bwiek\b|\budzial"
)
_TEXTAREA_HEADER = re.compile(r"\buwagi\b|\bopis\b|\buzasadnieni")
# Pola, których nie traktujemy jako "rozbitych" kolumn tabeli.
_NON_COLUMN_TYPES = frozenset({FieldType.REPEATABLE_GROUP, FieldType.TEXT_BLOCK, FieldType.CONSENT_SECTION})


def _clean_table(table: Table) -> Table:
    """Komórki bez wielokrotnych białych znaków (pdfplumber wstawia \\n w nagłówkach)."""
    return [[" ".join(str(cell or "").split()) for cell in row] for row in table]


def _header_index(rows: Table) -> int | None:
    """Indeks pierwszego wiersza z >= 2 niepustymi komórkami (wiersz nagłówka)."""
    for index, row in enumerate(rows):
        if sum(1 for cell in row if cell) >= 2:
            return index
    return None


def _is_lp_header(cell: str) -> bool:
    return bool(_LP_HEADER.match(_norm(cell)))


@dataclass
class _FormTableSpec:
    """Tabela do wypełnienia: nagłówki kolumn danych (bez 'Lp.') i liczba pustych wierszy."""

    columns: list[str]
    header_cells: list[str]
    empty_rows: int


def _form_table_spec(table: Table) -> _FormTableSpec | None:
    """Rozpoznaje tabelę-formularz: wiersz nagłówka + >= 1 pusty/prawie pusty wiersz danych.

    Tabele z wypełnionymi danymi (np. cennik) zwracają None - nie są polami formularza.
    """
    rows = _clean_table(table)
    header_at = _header_index(rows)
    if header_at is None:
        return None
    header = rows[header_at]
    data_positions = [i for i, cell in enumerate(header) if cell and not _is_lp_header(cell)]
    if len(data_positions) < 2 or any(len(header[i]) > 120 for i in data_positions):
        return None
    data_rows = rows[header_at + 1 :]
    if not data_rows:
        return None
    empty_rows = 0
    for row in data_rows:
        filled = sum(
            1 for i in data_positions if i < len(row) and row[i] and not _FILLER_CELL.match(row[i])
        )
        if filled <= max(0, len(data_positions) // 4):
            empty_rows += 1
    if empty_rows * 2 <= len(data_rows):
        return None
    return _FormTableSpec(
        columns=[header[i] for i in data_positions],
        header_cells=[cell for cell in header if cell],
        empty_rows=empty_rows,
    )


def _column_type(label: str) -> FieldType:
    """Heurystyczny typ kolumny: data -> date, kwota/liczba -> number, reszta -> text."""
    norm = _norm(label)
    if _DATE_HEADER.search(norm):
        return FieldType.DATE
    if _NUMBER_HEADER.search(norm):
        return FieldType.NUMBER
    if _TEXTAREA_HEADER.search(norm):
        return FieldType.TEXTAREA
    return FieldType.TEXT


def _table_to_text(table: Table, number: int) -> str:
    rows = [row for row in _clean_table(table) if any(row)]
    if not rows:
        return ""
    spec = _form_table_spec(table)
    if spec is not None:
        return (
            f"[Tabela {number} - do wypełnienia przez wnioskodawcę; pustych wierszy: {spec.empty_rows}]\n"
            f"Kolumny: {' | '.join(spec.header_cells)}"
        )
    lines = [" | ".join(row) for row in rows[:MAX_TABLE_ROWS_IN_PROMPT]]
    if len(rows) > MAX_TABLE_ROWS_IN_PROMPT:
        lines.append(f"... (pominięto {len(rows) - MAX_TABLE_ROWS_IN_PROMPT} wierszy)")
    return f"[Tabela {number} - z danymi]\n" + "\n".join(lines)


def _tables_to_text(tables: list[Table]) -> str:
    parts = [text for number, table in enumerate(tables, start=1) if (text := _table_to_text(table, number))]
    return "\n\n".join(parts)[:MAX_TABLE_CHARS]


def _table_matches_text(table: Table, text_norm: str) -> bool:
    """Czy nagłówek (pierwszy wiersz z treścią) tabeli występuje w tekście fragmentu."""
    rows = [row for row in _clean_table(table) if any(row)]
    if not rows or not text_norm:
        return False
    cells = [_norm(cell) for cell in rows[0] if _norm(cell)]
    if not cells:
        return False
    if " ".join(cells) in text_norm:
        return True
    long_cells = [cell for cell in cells if len(cell) >= 2]
    hits = sum(1 for cell in long_cells if cell in text_norm)
    return bool(long_cells) and hits / len(long_cells) >= 0.6


# --------------------------------------------------------------------------------------------
# Struktury robocze
# --------------------------------------------------------------------------------------------


@dataclass
class _Fragment:
    text: str
    tables: list[Table] = field(default_factory=list)


@dataclass
class _SectionDraft:
    title: str
    description: str | None
    fields: list[FieldDefinition]
    fallback_title: str = "Informacje"
    topic: int = 0


class _Topic:
    """Tematy stron wg Standardów (pkt 11: kolejność zbierania danych)."""

    APPLICANT = 0  # dane stron postępowania: urząd, wnioskodawca, pełnomocnik, adresy
    SUBJECT = 1  # informacje dotyczące sprawy (domyślny)
    ATTACHMENTS = 2  # załączniki
    CLOSING = 3  # przedostatnia strona: kontakt, opłaty, oświadczenia i zgody


_TOPIC_ATTACHMENTS = re.compile(r"\bzalacz|\bdokumenty dolaczon|\bdolaczam|\bwykaz dokumentow")
_TOPIC_CLOSING = re.compile(
    r"\boswiadcz|\bzgod|\bklauzul|\brodo\b|\bpouczeni|\bpodpis|\boplat[aye]? skarbow|\bdane do kontaktu"
    r"|\bkontakt"
)
_TOPIC_CONTACT = re.compile(r"\bkontakt")
_TOPIC_APPLICANT = re.compile(
    r"\bwnioskodaw|\btwoje dane|\bdane osob|\bdane identyfik|\bpodmiot|\bpelnomocn|\breprezent"
    r"|\badresat|\burzad|\borgan\b|\binwestor|\bwlascicie|\bstrona postepowania|\bzglaszajac"
    r"|\bskladajac|\bpodatni|\badres zamieszkania|\badres zameldowania|\badres do korespondencji"
    r"|\badres siedziby|\bdane wnioskujac|\bwnioskujac"
)
_APPLICANT_FIELD = re.compile(
    r"\bimie|\bimiona\b|\bnazwisko|\bpesel\b|\bnip\b|\bregon\b|\bkrs\b|\bulica|\bnumer domu"
    r"|\bnumer budynku|\bnumer lokalu|\bkod pocztowy|\bmiejscowosc|\bpoczta\b|\bgmina|\bpowiat"
    r"|\bwojewodztwo|\bkraj\b|\bobywatelstwo|\bdowod"
)
_CONTINUATION = re.compile(r"\s*[\(\[]?\s*(c\.?\s?d\.?|ci[aą]g dalszy|kontynuacja)\s*[\)\]]?\.?\s*$", re.IGNORECASE)


def _strip_continuation(title: str) -> str:
    return _CONTINUATION.sub("", title or "").strip(" :-–")


def _is_continuation(draft: _SectionDraft, previous: _SectionDraft) -> bool:
    title = (draft.title or "").strip()
    if not title or _CONTINUATION.search(title):
        return True
    return _norm(_strip_continuation(title)) == _norm(_strip_continuation(previous.title))


def _classify_section(draft: _SectionDraft) -> int:
    text = _norm(f"{draft.title} {draft.description or ''}")
    if _TOPIC_ATTACHMENTS.search(text):
        return _Topic.ATTACHMENTS
    if _TOPIC_CLOSING.search(text):
        return _Topic.CLOSING
    if _TOPIC_APPLICANT.search(text):
        return _Topic.APPLICANT
    types = [f.field_type for f in draft.fields]
    if types and all(t == FieldType.FILE for t in types):
        return _Topic.ATTACHMENTS
    if types and all(t in (FieldType.CONSENT_SECTION, FieldType.TEXT_BLOCK) for t in types) and (
        FieldType.CONSENT_SECTION in types
    ):
        return _Topic.CLOSING
    data_fields = [f for f in draft.fields if f.field_type not in (FieldType.TEXT_BLOCK,)]
    if data_fields:
        applicant_like = sum(1 for f in data_fields if _APPLICANT_FIELD.search(_norm(f.label)))
        if applicant_like / len(data_fields) >= 0.6:
            return _Topic.APPLICANT
    return _Topic.SUBJECT


def _field_weight(item: FieldDefinition) -> int:
    return 2 if item.field_type == FieldType.REPEATABLE_GROUP else 1


def _section_weight(draft: _SectionDraft) -> int:
    return sum(_field_weight(item) for item in draft.fields)


@dataclass
class _PageDraft:
    topics: list[int]
    sections: list[_SectionDraft]
    part: int = 1  # numer części tematu po podziale zbyt długiej strony

    @property
    def weight(self) -> int:
        return sum(_section_weight(section) for section in self.sections)


# --------------------------------------------------------------------------------------------
# Ekstraktor
# --------------------------------------------------------------------------------------------


class LocalStructureExtractor:
    """Implementacja protokołu StructureExtractor.

    `llm_complete` pozwala wstrzyknąć backend LLM (np. atrapę w testach); domyślnie ładowany
    jest lokalny plik GGUF z `models_dir` przez llama-cpp-python.
    """

    def __init__(
        self,
        models_dir: Path,
        llm_complete: LlmCompletion | None = None,
        *,
        name_pages_with_llm: bool = True,
    ) -> None:
        self._models_dir = Path(models_dir)
        self._llm_complete = llm_complete
        self._name_pages_with_llm = name_pages_with_llm

    # ---------------------------------------------------------------- API

    def extract(self, parsed: ParsedDocument) -> FormDocumentSchema:
        # Brak modelu = 503 także dla dokumentów bez treści (realny pipeline bez wag).
        self._ensure_ready()
        stem = Path(parsed.source_filename).stem
        filename_title = stem.replace("_", " ").replace("-", " ").strip() or "Formularz"
        used_ids: set[str] = set()
        document_title: str | None = None
        drafts: list[_SectionDraft] = []

        for fragment in self._build_fragments(parsed):
            llm_page = self._extract_fragment(fragment)
            if document_title is None and llm_page.document_title and llm_page.document_title.strip():
                document_title = llm_page.document_title.strip()
            fragment_drafts = self._to_section_drafts(llm_page, used_ids)
            self._apply_table_fallback(fragment, fragment_drafts, used_ids)
            for index, draft in enumerate(fragment_drafts):
                if index == 0 and drafts and _is_continuation(draft, drafts[-1]):
                    drafts[-1].fields.extend(draft.fields)
                    drafts[-1].description = drafts[-1].description or draft.description
                else:
                    drafts.append(draft)

        form_title = (document_title or filename_title)[:MAX_TITLE_LENGTH].strip() or "Formularz"
        pages = self._build_pages(drafts, form_title)

        return FormDocumentSchema(
            metadata=FormMetadata(
                form_id=_slugify(stem) or "formularz",
                title=form_title,
                source_filename=parsed.source_filename,
                source_format=parsed.source_format,
                generated_at=datetime.now(UTC),
                ai_agent_ready=True,
            ),
            pages=pages,
        )

    # ---------------------------------------------------------------- LLM

    def _model_path(self) -> Path:
        model_path = _find_gguf(self._models_dir)
        if model_path is None:
            raise PipelineNotReadyError(
                "Brak modelu LLM (plik *.gguf) w MODELS_DIR. Pobierz wagi ręcznie, np. "
                "Qwen3-4B-Instruct-2507-Q4_K_M.gguf z unsloth/Qwen3-4B-Instruct-2507-GGUF.",
                {"models_dir": str(self._models_dir)},
            )
        return model_path

    def _ensure_ready(self) -> None:
        if self._llm_complete is not None:
            return
        _load_llm(self._model_path())

    def _complete(
        self, messages: list[dict[str, str]], schema: dict[str, Any], max_tokens: int = MAX_OUTPUT_TOKENS
    ) -> str:
        if self._llm_complete is not None:
            return self._llm_complete(messages, schema)
        llm = _load_llm(self._model_path())
        with _LLM_LOCK:
            response = llm.create_chat_completion(
                messages=messages,
                response_format={"type": "json_object", "schema": schema},
                temperature=0.0,
                max_tokens=max_tokens,
            )
        return response["choices"][0]["message"]["content"]

    def _extract_fragment(self, fragment: _Fragment) -> _LlmPage:
        parts = [fragment.text[:MAX_PAGE_CHARS]] if fragment.text else []
        tables_text = _tables_to_text(fragment.tables)
        if tables_text:
            parts.append(f"Tabele rozpoznane w tym fragmencie:\n{tables_text}")
        raw = self._complete(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n".join(parts)},
            ],
            _LlmPage.model_json_schema(),
        )
        try:
            return _LlmPage.model_validate(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise PipelineNotReadyError(
                "Lokalny LLM zwrócił niepoprawną strukturę JSON dla fragmentu dokumentu.",
                {"reason": str(exc)[:500]},
            ) from exc

    def _llm_page_titles(self, groups: list[list[str]]) -> list[str] | None:
        """Jedno krótkie, opcjonalne wywołanie LLM nadające tytuły stronom; błąd = None."""
        prompt = "\n".join(
            f"Strona {index}: sekcje: {'; '.join(titles)}" for index, titles in enumerate(groups, start=1)
        )
        try:
            raw = self._complete(
                [{"role": "system", "content": _TITLES_PROMPT}, {"role": "user", "content": prompt}],
                _LlmPageTitles.model_json_schema(),
                max_tokens=TITLES_MAX_TOKENS,
            )
            titles = _LlmPageTitles.model_validate(json.loads(raw)).titles
        except (json.JSONDecodeError, TypeError, ValueError, PipelineNotReadyError):
            return None
        cleaned = [" ".join(title.split())[:120] for title in titles]
        if len(cleaned) != len(groups) or not all(cleaned):
            return None
        return cleaned

    # ---------------------------------------------------------------- fragmenty

    @staticmethod
    def _split_text(text: str, limit: int = MAX_PAGE_CHARS) -> list[str]:
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        current: list[str] = []
        size = 0
        for line in text.split("\n"):
            while len(line) > limit:
                if current:
                    chunks.append("\n".join(current))
                    current, size = [], 0
                chunks.append(line[:limit])
                line = line[limit:]
            if current and size + len(line) + 1 > limit:
                chunks.append("\n".join(current))
                current, size = [], 0
            current.append(line)
            size += len(line) + 1
        if current:
            chunks.append("\n".join(current))
        return [chunk for chunk in chunks if chunk.strip()]

    def _build_fragments(self, parsed: ParsedDocument) -> list[_Fragment]:
        fragments: list[_Fragment] = []
        for page in parsed.pages:
            text = page.text.strip()
            chunks = [_Fragment(text=chunk) for chunk in self._split_text(text)] if text else []
            if not chunks and page.tables:
                chunks = [_Fragment(text="")]
            for table in page.tables:
                target = next(
                    (chunk for chunk in chunks if _table_matches_text(table, _norm(chunk.text))), chunks[0]
                )
                target.tables.append(table)
            fragments.extend(chunks)

        # Tabele bez strony (DOCX/XLSX/ODS): do fragmentu z ich nagłówkiem, zachowując kolejność.
        orphans: list[Table] = []
        normalized = [_norm(fragment.text) for fragment in fragments]
        cursor = 0
        for table in parsed.tables:
            order = list(range(cursor, len(fragments))) + list(range(cursor))
            hit = next((i for i in order if _table_matches_text(table, normalized[i])), None)
            if hit is None:
                orphans.append(table)
            else:
                fragments[hit].tables.append(table)
                cursor = hit
        if orphans:
            fragments.append(
                _Fragment(text="Tabele dokumentu bez przypisanej strony (część formularza).", tables=orphans)
            )
        return fragments

    # ---------------------------------------------------------------- mapowanie LLM -> kontrakt

    @staticmethod
    def _unique_id(base: str, used_ids: set[str]) -> str:
        base = base or "pole"
        candidate = base
        suffix = 2
        while candidate in used_ids:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used_ids.add(candidate)
        return candidate

    @staticmethod
    def _options(items: list[_LlmOption] | None) -> list[DictionaryItem] | None:
        if not items:
            return None
        options = [
            DictionaryItem(code=(item.code.strip() or _slugify(item.label)), label=item.label.strip() or item.code)
            for item in items
            if (item.label or item.code).strip()
        ]
        return options or None

    def _to_section_drafts(self, llm_page: _LlmPage, used_ids: set[str]) -> list[_SectionDraft]:
        drafts = []
        for llm_section in llm_page.sections:
            fields = [self._to_field_definition(item, used_ids) for item in llm_section.fields]
            drafts.append(
                _SectionDraft(
                    title=llm_section.title.strip(),
                    description=llm_section.description,
                    fields=fields,
                    fallback_title=llm_page.title.strip() or "Informacje",
                )
            )
        return drafts

    def _to_field_definition(self, llm_field: _LlmField, used_ids: set[str]) -> FieldDefinition:
        label = llm_field.label.strip() or llm_field.name.strip() or "Pole"
        name = llm_field.name.strip() or _slugify(label)
        field_id = self._unique_id(_slugify(name), used_ids)
        field_type = FieldType(llm_field.field_type.value)
        common: dict[str, Any] = {"field_id": field_id, "name": name, "label": label, "field_type": field_type}

        if field_type == FieldType.TEXT_BLOCK:
            # Pole prezentacyjne: bez obligatoryjności i opcji; treść w help_text.
            return FieldDefinition(**common, help_text=llm_field.help_text or label)
        if field_type == FieldType.CONSENT_SECTION:
            descriptions = [item.label.strip() for item in llm_field.options or [] if item.label.strip()]
            consents = [
                ConsentItem(description=text, required=llm_field.required) for text in descriptions or [label]
            ]
            return FieldDefinition(
                **common, required=llm_field.required, help_text=llm_field.help_text, consents=consents
            )
        if field_type == FieldType.REPEATABLE_GROUP:
            columns = [
                self._column_definition(
                    column.name,
                    column.label,
                    FieldType(column.field_type.value),
                    field_id,
                    used_ids,
                    column.required,
                    self._options(column.options),
                )
                for column in llm_field.fields or []
                if not _is_lp_header(column.label or column.name)
            ]
            return FieldDefinition(
                **common, required=llm_field.required, help_text=llm_field.help_text, fields=columns or None
            )
        return FieldDefinition(
            **common,
            required=llm_field.required,
            help_text=llm_field.help_text,
            options=self._options(llm_field.options),
        )

    def _column_definition(
        self,
        name: str,
        label: str,
        field_type: FieldType,
        group_id: str,
        used_ids: set[str],
        required: bool = False,
        options: list[DictionaryItem] | None = None,
    ) -> FieldDefinition:
        label = label.strip() or name.strip() or "Kolumna"
        name = name.strip() or _slugify(label)
        return FieldDefinition(
            field_id=self._unique_id(f"{group_id}_{_slugify(name)}", used_ids),
            name=name,
            label=label,
            field_type=field_type,
            required=required,
            options=options if field_type in (FieldType.SELECT, FieldType.CHECKBOX) else None,
        )

    # ---------------------------------------------------------------- fallback tabel

    @staticmethod
    def _table_title(fragment_text: str, spec: _FormTableSpec) -> str | None:
        """Linia tekstu bezpośrednio nad nagłówkiem tabeli (np. 'Dane członków rodziny:')."""
        keys = [_norm(cell) for cell in spec.columns[:2]]
        lines = [line.strip() for line in fragment_text.split("\n")]
        for index, line in enumerate(lines):
            norm_line = _norm(line)
            if all(key and key in norm_line for key in keys):
                for previous in reversed(lines[:index]):
                    if not previous:
                        continue
                    if len(previous) <= 120 and " | " not in previous:
                        return previous.rstrip(" :")
                    return None
                return None
        return None

    def _apply_table_fallback(
        self, fragment: _Fragment, drafts: list[_SectionDraft], used_ids: set[str]
    ) -> None:
        for table in fragment.tables:
            spec = _form_table_spec(table)
            if spec is None:
                continue
            if self._table_covered_by_group(spec, drafts, used_ids):
                continue
            title = self._table_title(fragment.text, spec)
            label = title or f"Tabela do wypełnienia: {', '.join(spec.columns[:3])}"
            group_id = self._unique_id(_slugify(title or "tabela_" + "_".join(spec.columns[:2]))[:40], used_ids)
            group = FieldDefinition(
                field_id=group_id,
                name=group_id,
                label=label,
                field_type=FieldType.REPEATABLE_GROUP,
                help_text="Dodaj kolejny wiersz dla każdej pozycji.",
                fields=[
                    self._column_definition(_slugify(column), column, _column_type(column), group_id, used_ids)
                    for column in spec.columns
                ],
            )
            if self._replace_plain_columns(spec, drafts, group):
                continue
            target = next((d for d in drafts if title and _labels_match(d.title, title)), None)
            if target is not None:
                target.fields.append(group)
            else:
                drafts.append(
                    _SectionDraft(
                        title=title or "Tabela do wypełnienia",
                        description=None,
                        fields=[group],
                        fallback_title=label,
                    )
                )

        # Grupa bez kolumn nie da się wypełnić - degradujemy ją do pola opisowego.
        for draft in drafts:
            for index, item in enumerate(draft.fields):
                if item.field_type == FieldType.REPEATABLE_GROUP and not item.fields:
                    draft.fields[index] = item.model_copy(update={"field_type": FieldType.TEXTAREA, "fields": None})

    def _table_covered_by_group(
        self, spec: _FormTableSpec, drafts: list[_SectionDraft], used_ids: set[str]
    ) -> bool:
        needed = max(1, math.ceil(len(spec.columns) / 2))
        empty_groups: list[tuple[_SectionDraft, int]] = []
        for draft in drafts:
            for index, item in enumerate(draft.fields):
                if item.field_type != FieldType.REPEATABLE_GROUP:
                    continue
                if not item.fields:
                    empty_groups.append((draft, index))
                    continue
                hits = sum(
                    1 for column in spec.columns if any(_labels_match(column, sub.label) for sub in item.fields)
                )
                if hits >= needed:
                    return True
        if empty_groups:
            # LLM rozpoznał grupę, ale bez kolumn: uzupełniamy kolumny z nagłówka tabeli.
            draft, index = empty_groups[0]
            group = draft.fields[index]
            columns = [
                self._column_definition(_slugify(column), column, _column_type(column), group.field_id, used_ids)
                for column in spec.columns
            ]
            draft.fields[index] = group.model_copy(update={"fields": columns})
            return True
        return False

    @staticmethod
    def _replace_plain_columns(spec: _FormTableSpec, drafts: list[_SectionDraft], group: FieldDefinition) -> bool:
        """LLM rozbił tabelę na zwykłe pola? Zastępujemy je jedną grupą w miejscu pierwszego z nich."""
        needed = max(2, math.ceil(len(spec.columns) / 2))
        for draft in drafts:
            matched = [
                index
                for index, item in enumerate(draft.fields)
                if item.field_type not in _NON_COLUMN_TYPES
                and any(_labels_match(column, item.label) for column in spec.columns)
            ]
            if len(matched) >= needed:
                first = matched[0]
                kept = [item for index, item in enumerate(draft.fields) if index not in matched]
                kept.insert(first, group)
                draft.fields[:] = kept
                return True
        return False

    # ---------------------------------------------------------------- strony tematyczne

    def _build_pages(self, drafts: list[_SectionDraft], form_title: str) -> list[PageDefinition]:
        sections = [draft for draft in drafts if draft.fields]
        for draft in sections:
            if not draft.title:
                draft.title = draft.fallback_title
            draft.topic = _classify_section(draft)
        if not sections:
            return [PageDefinition(page_id="page_1", title=form_title, order=1)]

        # Kolejność tematów wg Standardów; w obrębie tematu - kolejność z dokumentu.
        topic_pages: list[_PageDraft] = []
        for topic in sorted({draft.topic for draft in sections}):
            members = [draft for draft in sections if draft.topic == topic]
            topic_pages.extend(self._split_topic(topic, members))
        page_drafts = self._merge_short_pages(topic_pages)
        titles = self._page_titles(page_drafts)

        pages = []
        for page_order, (page_draft, title) in enumerate(zip(page_drafts, titles, strict=True), start=1):
            pages.append(
                PageDefinition(
                    page_id=f"page_{page_order}",
                    title=title,
                    order=page_order,
                    sections=[
                        SectionDefinition(
                            section_id=f"sec_{page_order}_{sec_order}",
                            title=draft.title,
                            order=sec_order,
                            description=draft.description,
                            fields=draft.fields,
                        )
                        for sec_order, draft in enumerate(page_draft.sections, start=1)
                    ],
                )
            )
        return pages

    @staticmethod
    def _split_topic(topic: int, members: list[_SectionDraft]) -> list[_PageDraft]:
        units: list[_SectionDraft] = []
        for draft in members:
            if _section_weight(draft) <= MAX_FIELDS_PER_PAGE:
                units.append(draft)
                continue
            # Sekcja dłuższa niż strona: dzielimy na zbalansowane części.
            parts = math.ceil(_section_weight(draft) / MAX_FIELDS_PER_PAGE)
            size = math.ceil(len(draft.fields) / parts)
            for index in range(0, len(draft.fields), size):
                units.append(
                    _SectionDraft(
                        title=draft.title if index == 0 else f"{_strip_continuation(draft.title)} (cd.)",
                        description=draft.description if index == 0 else None,
                        fields=draft.fields[index : index + size],
                        fallback_title=draft.fallback_title,
                        topic=topic,
                    )
                )
        total = sum(_section_weight(unit) for unit in units)
        target = math.ceil(total / max(1, math.ceil(total / MAX_FIELDS_PER_PAGE)))
        pages: list[_PageDraft] = []
        current: list[_SectionDraft] = []
        weight = 0
        for unit in units:
            unit_weight = _section_weight(unit)
            if current and (weight + unit_weight > MAX_FIELDS_PER_PAGE or weight >= target):
                pages.append(_PageDraft(topics=[topic], sections=current, part=len(pages) + 1))
                current, weight = [], 0
            current.append(unit)
            weight += unit_weight
        if current:
            pages.append(_PageDraft(topics=[topic], sections=current, part=len(pages) + 1))
        return pages

    @staticmethod
    def _merge_short_pages(pages: list[_PageDraft]) -> list[_PageDraft]:
        """Łączy strony krótsze niż MIN_FIELDS_PER_PAGE z sąsiednią, jeśli zmieszczą się w limicie.

        Strony danych wnioskodawcy i informacji o sprawie (osobne kroki wg Standardów) nie są łączone
        z innymi tematami; łączyć wolno tylko załączniki z oświadczeniami/zgodami.
        """

        def can_merge(a: _PageDraft, b: _PageDraft) -> bool:
            if a.weight + b.weight > MAX_FIELDS_PER_PAGE:
                return False
            mixed = set(a.topics) != set(b.topics)
            separate = {_Topic.APPLICANT, _Topic.SUBJECT}
            return not (mixed and separate & (set(a.topics) | set(b.topics)))

        result = list(pages)
        changed = True
        while changed:
            changed = False
            for index, page in enumerate(result):
                if page.weight >= MIN_FIELDS_PER_PAGE or len(result) == 1:
                    continue
                if index > 0 and can_merge(result[index - 1], page):
                    previous = result[index - 1]
                    previous.sections.extend(page.sections)
                    previous.topics.extend(t for t in page.topics if t not in previous.topics)
                    del result[index]
                    changed = True
                    break
                if index + 1 < len(result) and can_merge(page, result[index + 1]):
                    following = result[index + 1]
                    page.sections.extend(following.sections)
                    page.topics.extend(t for t in following.topics if t not in page.topics)
                    del result[index + 1]
                    changed = True
                    break
        return result

    def _page_titles(self, pages: list[_PageDraft]) -> list[str]:
        titles: list[str] = []
        subject_pages: list[int] = []
        for index, page in enumerate(pages):
            topics = set(page.topics)
            first_title = page.sections[0].title
            if _Topic.SUBJECT in topics:
                titles.append(_strip_continuation(first_title) or "Przedmiot wniosku")
                if len(page.sections) > 1:
                    subject_pages.append(index)
                continue
            if _Topic.APPLICANT in topics:
                title = "Dane wnioskodawcy"
            elif topics == {_Topic.ATTACHMENTS}:
                title = "Załączniki"
            else:
                has_contact = any(_TOPIC_CONTACT.search(_norm(s.title)) for s in page.sections)
                base = "Kontakt i zgody" if has_contact else "Oświadczenia i zgody"
                title = f"Załączniki, {base[0].lower()}{base[1:]}" if _Topic.ATTACHMENTS in topics else base
            if page.part > 1:
                title = _strip_continuation(first_title) or f"{title} (cd.)"
            titles.append(title)

        # Opcjonalnie: LLM nadaje tytuły stronom sprawy złożonym z kilku sekcji.
        if subject_pages and self._name_pages_with_llm:
            groups = [[s.title for s in pages[index].sections] for index in subject_pages]
            llm_titles = self._llm_page_titles(groups)
            if llm_titles:
                for index, title in zip(subject_pages, llm_titles, strict=True):
                    titles[index] = title
        return titles
