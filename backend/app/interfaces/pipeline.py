"""Kontrakt (Protocol) łączący Branch 1 (feat/backend-ai-pipeline) z warstwą API.

Branch 1 implementuje te protokoły w backend/app/modules/ i NIE zmienia warstwy
API ani serwisów. Przełączenie USE_MOCK_PIPELINE=false podpina realną implementację.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.api_models import ConversionWarning
from backend.app.schemas.form_schema import FormDocumentSchema


class ParsedPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    text: str = ""
    # Tabele rozpoznane na tej stronie (wiersze -> komórki). Parsery, które nie znają podziału
    # na strony (DOCX/XLSX/ODS), zostawiają puste i wypełniają ParsedDocument.tables.
    tables: list[list[list[str]]] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    """Wynik warstwy ingestion: surowy tekst/układ bez logiki domenowej."""

    model_config = ConfigDict(extra="forbid")

    source_filename: str
    source_format: str
    pages: list[ParsedPage] = Field(default_factory=list)
    tables: list[list[list[str]]] = Field(default_factory=list)  # tabele bez przypisanej strony
    used_ocr: bool = False

    @property
    def full_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages)


class PipelineResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    form: FormDocumentSchema
    warnings: list[ConversionWarning] = Field(default_factory=list)


@runtime_checkable
class DocumentParser(Protocol):
    """ingestion/: parsery PDF/DOC/DOCX/ODS/XLSX/obrazy -> ParsedDocument."""

    def parse(self, *, filename: str, content: bytes, mime_type: str) -> ParsedDocument: ...


@runtime_checkable
class StructureExtractor(Protocol):
    """ai_extractor/: lokalny OCR + model AI -> struktura stron/sekcji/pól."""

    def extract(self, parsed: ParsedDocument) -> FormDocumentSchema: ...


@runtime_checkable
class DomainEnricher(Protocol):
    """domain_logic/: słowniki, propozycje, zależności warunkowe, walidacje."""

    def enrich(self, form: FormDocumentSchema) -> FormDocumentSchema: ...


@runtime_checkable
class FormPipeline(Protocol):
    """Pełny pipeline pojedynczego pliku (RAM-only: content to bytes)."""

    def convert(self, *, filename: str, content: bytes, mime_type: str) -> PipelineResult: ...

    def model_status(self) -> dict[str, str]:
        """Status lokalnych modeli, np. {'ocr': 'ready', 'llm': 'missing'}."""
        ...
