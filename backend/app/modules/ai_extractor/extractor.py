"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Lokalny LLM (llama-cpp-python, wagi GGUF w MODELS_DIR) rozpoznający układ
dokumentu: strony tematyczne, sekcje, etykiety i typy kontrolek -> FormDocumentSchema.
OCR stron obrazowych odbywa się w ingestion (ParsedDocument niesie wyłącznie tekst).
Gramatyka GBNF z response_format wymusza JSON zgodny ze schematem wewnętrznym.
100% offline, niski koszt CPU/RAM. Zero wywołań zewnętrznych API.
"""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.core.exceptions import PipelineNotReadyError
from backend.app.interfaces.pipeline import ParsedDocument
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
MAX_OUTPUT_TOKENS = 3072
N_CTX = 8192

_PL_TRANSLIT = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")

_SYSTEM_PROMPT = (
    "Jesteś silnikiem cyfryzacji polskich formularzy urzędowych. "
    "Otrzymujesz tekst jednej strony dokumentu i zwracasz WYŁĄCZNIE JSON opisujący "
    "strukturę formularza: tytuł strony, sekcje i pola. "
    "Zasady: "
    "1) Wyodrębnij tylko pola wynikające z tekstu - nie zmyślaj pól. "
    "2) name to krótki identyfikator snake_case po angielsku lub polsku (np. pesel, imie, data_urodzenia); "
    "label to oryginalna etykieta pola z dokumentu, po polsku. "
    "3) field_type dobierz do treści: text, number, date, select, radio, checkbox, textarea, file, "
    "repeatable_group (dla powtarzalnych tabel, np. 'dodaj kolejną osobę'). "
    "4) Dla select/radio/checkbox podaj options (code + label) wyliczone z treści dokumentu. "
    "5) required=true tylko gdy dokument oznacza pole jako obowiązkowe (gwiazdka, 'wymagane'). "
    "6) Pomijaj instrukcje wypełniania, pouczenia i nagłówki niebędące polami. "
    "7) Jeśli strona nie zawiera żadnych pól, zwróć pustą listę sections."
)


class _LlmOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    label: str


class _LlmField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    field_type: FieldType
    required: bool = False
    help_text: str | None = None
    options: list[_LlmOption] | None = None


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


# llama.cpp nie jest thread-safe: jedna instancja na plik GGUF, inference serializowane.
_LLM_CACHE: dict[str, Any] = {}
_LLM_LOCK = threading.Lock()


def _slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value.translate(_PL_TRANSLIT))
    ascii_value = ascii_value.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_value).strip("_").lower()
    return slug[:60] or "pole"


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


class LocalStructureExtractor:
    """Implementacja protokołu StructureExtractor."""

    def __init__(self, models_dir: Path) -> None:
        self._models_dir = Path(models_dir)

    def extract(self, parsed: ParsedDocument) -> FormDocumentSchema:
        stem = Path(parsed.source_filename).stem
        form_title = stem.replace("_", " ").replace("-", " ").strip() or "Formularz"
        used_ids: set[str] = set()
        pages: list[PageDefinition] = []
        first_llm_title: str | None = None
        tables_addendum = self._tables_as_text(parsed.tables)
        addendum_used = False

        for page in parsed.pages:
            text = page.text.strip()
            if not text:
                pages.append(
                    PageDefinition(
                        page_id=f"page_{page.page_number}",
                        title=f"Strona {page.page_number}",
                        order=len(pages) + 1,
                    )
                )
                continue
            if tables_addendum and not addendum_used:
                text = f"{text}\n\nTabele dokumentu:\n{tables_addendum}"
                addendum_used = True
            llm_page = self._extract_page(text)
            if first_llm_title is None:
                first_llm_title = llm_page.title
            pages.append(self._to_page_definition(llm_page, len(pages) + 1, used_ids))

        if not pages:
            pages.append(PageDefinition(page_id="page_1", title=form_title, order=1))
        form_title = first_llm_title or form_title

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

    def _extract_page(self, text: str) -> _LlmPage:
        model_path = _find_gguf(self._models_dir)
        if model_path is None:
            raise PipelineNotReadyError(
                "Brak modelu LLM (plik *.gguf) w MODELS_DIR. Pobierz wagi ręcznie, np. "
                "Qwen3-4B-Instruct-2507-Q4_K_M.gguf z unsloth/Qwen3-4B-Instruct-2507-GGUF.",
                {"models_dir": str(self._models_dir)},
            )
        llm = _load_llm(model_path)
        user_text = text[:MAX_PAGE_CHARS]
        with _LLM_LOCK:
            response = llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                response_format={"type": "json_object", "schema": _LlmPage.model_json_schema()},
                temperature=0.0,
                max_tokens=MAX_OUTPUT_TOKENS,
            )
        raw = response["choices"][0]["message"]["content"]
        try:
            return _LlmPage.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValueError) as exc:
            raise PipelineNotReadyError(
                "Lokalny LLM zwrócił niepoprawną strukturę JSON dla strony dokumentu.",
                {"reason": str(exc), "model": model_path.name},
            ) from exc

    @staticmethod
    def _tables_as_text(tables: list[list[list[str]]]) -> str:
        parts = []
        for table in tables:
            parts.extend(" | ".join(row) for row in table)
        return "\n".join(parts)[:MAX_PAGE_CHARS]

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

    @staticmethod
    def _to_field_definition(llm_field: _LlmField, used_ids: set[str]) -> FieldDefinition:
        base = _slugify(llm_field.name) or _slugify(llm_field.label)
        field_id = base
        suffix = 2
        while field_id in used_ids:
            field_id = f"{base}_{suffix}"
            suffix += 1
        used_ids.add(field_id)
        options = (
            [DictionaryItem(code=item.code, label=item.label) for item in llm_field.options]
            if llm_field.options
            else None
        )
        return FieldDefinition(
            field_id=field_id,
            name=llm_field.name,
            label=llm_field.label,
            field_type=llm_field.field_type,
            required=llm_field.required,
            help_text=llm_field.help_text,
            options=options,
        )
