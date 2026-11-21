"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Model LLM (backend z llm_backend.py: lokalny GGUF albo endpoint OpenAI-compatible)
rozpoznający układ dokumentu: strony TEMATYCZNE (nie fizyczne strony PDF), sekcje,
etykiety, typy kontrolek i kolumny tabel powtarzalnych -> FormDocumentSchema.
OCR stron obrazowych odbywa się w ingestion (ParsedDocument niesie wyłącznie tekst).
JSON wymuszany przez response_format backendu i walidowany modelami _Llm*.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.exceptions import PipelineNotReadyError
from backend.app.interfaces.pipeline import ParsedDocument
from backend.app.modules.ai_extractor.llm_backend import LlmBackend
from backend.app.schemas.form_schema import (
    DictionaryItem,
    FieldDefinition,
    FieldType,
    FormDocumentSchema,
    FormMetadata,
    PageDefinition,
    SectionDefinition,
)

MAX_PAGE_CHARS = 9000

_PL_TRANSLIT = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")

_SYSTEM_PROMPT = (
    "Jesteś silnikiem cyfryzacji polskich formularzy urzędowych. "
    "Otrzymujesz tekst CAŁEGO dokumentu i zwracasz WYŁĄCZNIE JSON: title (tytuł wniosku) "
    "oraz pages — 2-4 strony TEMATYCZNE (np. 'Przedmiot wniosku', 'Załączniki'), "
    "NIE odwzorowuj fizycznego podziału stron pliku PDF/DOCX. "
    "Zasady: "
    "1) Wyodrębnij tylko pola wynikające z tekstu — nie zmyślaj pól. "
    "2) Pomijaj standardowe elementy: dane wnioskodawcy (imię, nazwisko, PESEL, adres), "
    "dane do kontaktu oraz zgody i oświadczenia — są dodawane automatycznie ze standardów COI. "
    "Skup się na treści merytorycznej wniosku. "
    "3) name to krótki identyfikator snake_case (np. kwota_nadplaty, rok_podatkowy); "
    "label to oryginalna etykieta pola z dokumentu, po polsku. "
    "4) field_type dobierz do treści: text, number, date, select, radio, "
    "checkbox (wielokrotny wybór), textarea, file, "
    "repeatable_group (powtarzalna tabela — kolumny podaj w fields), "
    "text_block (instrukcja lub pouczenie bez pola), info_section (blok informacyjny). "
    "5) Dla select/radio/checkbox podaj options (code + label) wyliczone z treści dokumentu. "
    "6) Tabele z wierszami do powielania (np. lista osób, okresów, nieruchomości) zamień na "
    "repeatable_group: label = nazwa tabeli, fields = kolumny (name, label, field_type każdej). "
    "7) required=true tylko gdy dokument oznacza pole jako obowiązkowe (gwiazdka, 'wymagane'). "
    "8) Dla text_block/info_section treść umieść w help_text; label może być pusty."
)


class _LlmOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    label: str


class _LlmColumn(BaseModel):
    # Kolumna bez rekurencji: rekurencyjny schemat JSON crashuje gramatykę GBNF llama.cpp.
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    field_type: FieldType
    required: bool = False
    help_text: str | None = None
    options: list[_LlmOption] | None = None


class _LlmField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    field_type: FieldType
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
    sections: list[_LlmSection] = Field(default_factory=list)


class _LlmDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    pages: list[_LlmPage] = Field(min_length=1, max_length=4)


def _slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value.translate(_PL_TRANSLIT))
    ascii_value = ascii_value.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_value).strip("_").lower()
    return slug[:60] or "pole"


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    return re.sub(r"\s*```$", "", text).strip()


class LlmStructureExtractor:
    """Implementacja protokołu StructureExtractor na wybranym backendzie LLM."""

    def __init__(self, backend: LlmBackend) -> None:
        self._backend = backend

    def extract(self, parsed: ParsedDocument) -> FormDocumentSchema:
        stem = Path(parsed.source_filename).stem
        fallback_title = stem.replace("_", " ").replace("-", " ").strip() or "Formularz"
        # Dokument bez tekstu nie woła LLM, ale i tak musi mieć gotowy backend —
        # inaczej 503 zamieniłoby się na cichy, pusty formularz.
        if not self._backend.ready():
            raise PipelineNotReadyError(
                f"Backend LLM '{self._backend.model_name}' nie jest gotowy. Sprawdź LLM_BACKEND "
                "oraz MODELS_DIR (tryb local) albo LLM_API_URL i LLM_MODEL (tryb api).",
                {"backend": self._backend.model_name},
            )

        page_texts = [page.text.strip() for page in parsed.pages if page.text.strip()]
        tables_note = self._tables_note(parsed.tables)
        if page_texts or tables_note:
            document = self._extract_document(page_texts, tables_note)
        else:
            document = _LlmDocument(title=fallback_title, pages=[_LlmPage(title=fallback_title)])

        used_ids: set[str] = set()
        pages = [
            self._to_page_definition(llm_page, order, used_ids)
            for order, llm_page in enumerate(document.pages, start=1)
        ]
        pages = self._ensure_two_pages(pages)
        self._add_table_fallbacks(parsed.tables, pages, used_ids)
        for order, page in enumerate(pages, start=1):
            page.page_id = f"page_{order}"
            page.order = order

        return FormDocumentSchema(
            metadata=FormMetadata(
                form_id=_slugify(stem) or "formularz",
                title=document.title.strip() or fallback_title,
                source_filename=parsed.source_filename,
                source_format=parsed.source_format,
                generated_at=datetime.now(UTC),
                ai_agent_ready=True,
            ),
            pages=pages,
        )

    def _extract_document(self, page_texts: list[str], tables_note: str) -> _LlmDocument:
        chunks = self._chunks([*page_texts, tables_note] if tables_note else page_texts)
        documents = [self._call_llm(chunk) for chunk in chunks]
        if len(documents) == 1:
            return documents[0]
        return self._merge_documents(documents)

    @staticmethod
    def _chunks(blocks: list[str]) -> list[str]:
        """Pakuje strony dokumentu w paczki <= MAX_PAGE_CHARS (bez cięcia w środku strony)."""
        chunks: list[str] = []
        current = ""
        for block in blocks:
            if len(block) > MAX_PAGE_CHARS:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(block[i : i + MAX_PAGE_CHARS] for i in range(0, len(block), MAX_PAGE_CHARS))
                continue
            if current and len(current) + len(block) + 2 > MAX_PAGE_CHARS:
                chunks.append(current)
                current = block
            else:
                current = f"{current}\n\n{block}" if current else block
        if current:
            chunks.append(current)
        return chunks

    def _call_llm(self, text: str) -> _LlmDocument:
        raw = self._backend.complete(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            json_schema=_LlmDocument.model_json_schema(),
        )
        try:
            return _LlmDocument.model_validate(json.loads(_strip_code_fence(raw)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise PipelineNotReadyError(
                "Model LLM zwrócił niepoprawną strukturę JSON dla dokumentu.",
                {"reason": str(exc), "model": self._backend.model_name},
            ) from exc

    @staticmethod
    def _merge_documents(documents: list[_LlmDocument]) -> _LlmDocument:
        """Scala wyniki paczek po znormalizowanym tytule strony (bez duplikatów pól)."""
        merged: dict[str, _LlmPage] = {}
        for document in documents:
            for page in document.pages:
                key = _slugify(page.title)
                if key not in merged:
                    merged[key] = page.model_copy(deep=True)
                    continue
                target = merged[key]
                sections_by_title = {_slugify(s.title): s for s in target.sections}
                for section in page.sections:
                    section_key = _slugify(section.title)
                    if section_key in sections_by_title:
                        existing = sections_by_title[section_key]
                        names = {f.name for f in existing.fields}
                        existing.fields.extend(f for f in section.fields if f.name not in names)
                    else:
                        target.sections.append(section.model_copy(deep=True))
                        sections_by_title[section_key] = target.sections[-1]
        return _LlmDocument(title=documents[0].title, pages=list(merged.values()))

    @staticmethod
    def _ensure_two_pages(pages: list[PageDefinition]) -> list[PageDefinition]:
        """Gdy LLM zwrócił jedną stronę z wieloma sekcjami — dzieli ją tematycznie na dwie."""
        if len(pages) != 1 or len(pages[0].sections) < 2:
            return pages
        only = pages[0]
        split = len(only.sections) // 2
        second = PageDefinition(
            page_id="page_2",
            title=only.sections[split].title,
            order=2,
            sections=only.sections[split:],
        )
        first = PageDefinition(
            page_id="page_1",
            title=only.title,
            order=1,
            description=only.description,
            sections=only.sections[:split],
        )
        return [first, second]

    @staticmethod
    def _tables_note(tables: list[list[list[str]]]) -> str:
        headers = []
        for index, table in enumerate(tables, start=1):
            if table and table[0]:
                header = " | ".join((cell or "").strip() for cell in table[0])
                if header.strip(" |"):
                    headers.append(f"Tabela {index}: {header}")
        if not headers:
            return ""
        return "Tabele dokumentu (nagłówki kolumn):\n" + "\n".join(headers)

    def _add_table_fallbacks(
        self, tables: list[list[list[str]]], pages: list[PageDefinition], used_ids: set[str]
    ) -> None:
        """Deterministyczny fallback: tabela z >=3 kolumnami, której LLM nie zamienił
        na repeatable_group, trafia na stronę 'Tabele dokumentu' z kolumnami z nagłówka."""
        covered: set[str] = set()
        for page in pages:
            for section in page.sections:
                for field in section.fields:
                    if field.field_type == FieldType.REPEATABLE_GROUP and field.fields:
                        covered.update(_slugify(column.label) for column in field.fields)
        sections: list[SectionDefinition] = []
        for index, table in enumerate(tables, start=1):
            if len(table) < 2:
                continue
            header = [(cell or "").strip() for cell in table[0]]
            header = [cell for cell in header if cell]
            if len(header) < 3 or {_slugify(cell) for cell in header} & covered:
                continue
            columns = [
                FieldDefinition(
                    field_id=self._unique_id(_slugify(cell), used_ids),
                    name=_slugify(cell),
                    label=cell,
                    field_type=self._column_type(cell),
                )
                for cell in header
            ]
            group_id = self._unique_id(f"tabela_{index}", used_ids)
            sections.append(
                SectionDefinition(
                    section_id=self._unique_id(f"sec_tabela_{index}", used_ids),
                    title=f"Tabela {index}",
                    order=len(sections) + 1,
                    fields=[
                        FieldDefinition(
                            field_id=group_id,
                            name=group_id,
                            label=f"Tabela {index} z dokumentu",
                            field_type=FieldType.REPEATABLE_GROUP,
                            fields=columns,
                        )
                    ],
                )
            )
        if sections:
            pages.append(
                PageDefinition(
                    page_id="page_tabele",
                    title="Tabele dokumentu",
                    order=len(pages) + 1,
                    sections=sections,
                )
            )

    @staticmethod
    def _column_type(header: str) -> FieldType:
        lowered = header.lower()
        if "data" in lowered:
            return FieldType.DATE
        if "kwota" in lowered or "rok" in lowered:
            return FieldType.NUMBER
        return FieldType.TEXT

    @staticmethod
    def _unique_id(base: str, used_ids: set[str]) -> str:
        candidate = base
        suffix = 2
        while candidate in used_ids:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used_ids.add(candidate)
        return candidate

    def _to_page_definition(self, llm_page: _LlmPage, order: int, used_ids: set[str]) -> PageDefinition:
        sections = []
        for sec_order, llm_section in enumerate(llm_page.sections, start=1):
            fields = [self._to_field_definition(llm_field, used_ids) for llm_field in llm_section.fields]
            sections.append(
                SectionDefinition(
                    section_id=f"sec_{order}_{sec_order}",
                    title=llm_section.title,
                    order=sec_order,
                    description=llm_section.description,
                    fields=fields,
                )
            )
        return PageDefinition(
            page_id=f"page_{order}",
            title=llm_page.title,
            order=order,
            description=llm_page.description,
            sections=sections,
        )

    def _to_field_definition(self, llm_field: _LlmField | _LlmColumn, used_ids: set[str]) -> FieldDefinition:
        base = _slugify(llm_field.name) or _slugify(llm_field.label)
        field_id = self._unique_id(base, used_ids)
        options = (
            [DictionaryItem(code=item.code, label=item.label) for item in llm_field.options]
            if llm_field.options
            else None
        )
        children = None
        nested = getattr(llm_field, "fields", None)
        if llm_field.field_type == FieldType.REPEATABLE_GROUP and nested:
            children = [self._to_field_definition(child, used_ids) for child in nested]
        return FieldDefinition(
            field_id=field_id,
            name=llm_field.name,
            label=llm_field.label,
            field_type=llm_field.field_type,
            required=llm_field.required,
            help_text=llm_field.help_text,
            options=options,
            fields=children,
        )
