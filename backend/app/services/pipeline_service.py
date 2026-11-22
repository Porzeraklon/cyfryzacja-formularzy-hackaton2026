"""Orkiestrator pipeline: przełącza mock <-> real przez USE_MOCK_PIPELINE.

- MockFormPipeline (core): deterministyczny wniosek wg wzorca COI „Wniosek
  o stwierdzenie nadpłaty podatku” — bez AI/OCR, aby Branch 2 mógł testować
  end-to-end od pierwszej minuty.
- RealFormPipeline: komponuje moduły Brancha 1 (backend/app/modules/).
  Stubby rzucają NotImplementedError -> API zwraca 503 PIPELINE_NOT_READY.
"""

import time
from datetime import UTC, datetime
from importlib.util import find_spec
from uuid import uuid4

from backend.app.core.config import Settings, get_settings
from backend.app.core.exceptions import PipelineNotReadyError
from backend.app.interfaces.pipeline import FormPipeline, PipelineResult
from backend.app.schemas.api_models import ConversionResponse, ConversionWarning, ProcessingMetadata
from backend.app.schemas.form_schema import (
    ConditionalFlow,
    ConditionOperator,
    DictionaryItem,
    FieldDefinition,
    FieldType,
    FlowAction,
    FormDocumentSchema,
    FormMetadata,
    PageDefinition,
    SectionDefinition,
    TargetType,
    ValidationRules,
)
from backend.app.services.dictionary_service import DictionaryService
from backend.app.services.form_definition_validation import validate_form_definition
from backend.app.services.formdefinition_export import convert_formdefinition, iter_fields

_ZALICZYC = "Zaliczyć na poczet przyszłego zobowiązania podatkowego"
_ZWROT = "Zwrócić przelewem na rachunek bankowy"


def _field(field_id: str, label: str, field_type: FieldType = FieldType.TEXT, **kw) -> FieldDefinition:
    return FieldDefinition(field_id=field_id, name=field_id, label=label, field_type=field_type, **kw)


def _show(source: str, value: object, target: str) -> ConditionalFlow:
    return ConditionalFlow(
        source_field_id=source,
        operator=ConditionOperator.EQUALS,
        value=value,
        target_type=TargetType.FIELD,
        target_id=target,
        action=FlowAction.SHOW,
    )


def build_mock_form(filename: str, mime_type: str) -> FormDocumentSchema:
    """Wniosek o stwierdzenie nadpłaty podatku — wzorzec COI.

    Szkielet środkowej strony („Nadpłata i deklaracja”) plus zależności; enricher
    dokłada predefiniowane sekcje wnioskodawcy i kontaktu, słowniki TERYT/Kraje
    oraz flows (czysta logika domenowa, bez AI/OCR). Wynik jest tożsamy z tym,
    co realny pipeline produkuje dla tego wzorca.
    """
    from backend.app.modules.domain_logic.mapper import DomainLogicEnricher

    raw = FormDocumentSchema(
        metadata=FormMetadata(
            form_id=f"form-{uuid4().hex[:12]}",
            title="Wniosek o stwierdzenie nadpłaty podatku",
            version="1.0.0",
            source_filename=filename,
            source_format=mime_type,
            generated_at=datetime.now(UTC),
            ai_agent_ready=True,
            office_name="Urząd Gminy Ulan-Majorat",
        ),
        pages=[
            # Strona 2 referencji COI („Nadpłata i deklaracja podatkowa”) - 1:1 z wizualizacją
            # „Wniosek o stwierdzenie nadpłaty podatku” (materiały konkursowe).
            PageDefinition(
                page_id="page_sprawa",
                title="Informacje o nadpłacie",
                order=1,
                description="Informacje o nadpłacie, sposób jej rozliczenia i korekta deklaracji.",
                sections=[
                    SectionDefinition(
                        section_id="informacje_o_nadplacie",
                        title="Informacje o nadpłacie",
                        order=1,
                        fields=[
                            _field("podatek_ktorego_dotyczy_nadplata", "Podatek, którego dotyczy nadpłata", required=True),
                            _field(
                                "kwota",
                                "Kwota",
                                FieldType.NUMBER,
                                required=True,
                                placeholder="Kwota w złotych, np. 120,50",
                            ),
                            _field(
                                "uzasadnienie",
                                "Uzasadnienie",
                                FieldType.TEXTAREA,
                                required=True,
                                help_text=(
                                    "Uzasadnij dlaczego Twoim zdaniem powstała nadpłata i z czego wynika "
                                    "jej wysokość."
                                ),
                                validation_rules=ValidationRules(max_length=8000),
                            ),
                        ],
                    ),
                    SectionDefinition(
                        section_id="rozliczenie_nadplaty",
                        title="Rozliczenie nadpłaty",
                        order=2,
                        fields=[
                            _field(
                                "wybierz_co_mamy_zrobic_z_nadplata",
                                "Wybierz, co mamy zrobić z nadpłatą",
                                FieldType.RADIO,
                                required=True,
                                options=[
                                    DictionaryItem(code="zaliczyc", label=_ZALICZYC),
                                    DictionaryItem(code="zwrot", label=_ZWROT),
                                ],
                            ),
                            _field("nazwa_podatku", "Nazwa podatku", required=True),
                            _field("numer_rachunku", "Numer rachunku", required=True),
                        ],
                    ),
                    SectionDefinition(
                        section_id="korekta_deklaracji",
                        title="Korekta deklaracji",
                        order=3,
                        fields=[
                            _field(
                                "korekta_info",
                                "",
                                FieldType.TEXT_BLOCK,
                                help_text=(
                                    "Załącz poprawioną deklarację podatkową, z której wynika prawidłowa, "
                                    "niższa wysokość zobowiązania podatkowego, a w konsekwencji nadpłata."
                                ),
                            ),
                            _field(
                                "plik_deklaracji_podatkowej",
                                "Plik deklaracji podatkowej",
                                FieldType.FILE,
                                required=True,
                            ),
                        ],
                    ),
                ],
            )
        ],
        # Stany dynamiczne z wizualizacji: pola „Nazwa podatku” / „Numer rachunku” zależne od
        # wyboru w „Wybierz, co mamy zrobić z nadpłatą” (warunek jsonata SHOW).
        conditional_flows=[
            _show("wybierz_co_mamy_zrobic_z_nadplata", _ZALICZYC, "nazwa_podatku"),
            _show("wybierz_co_mamy_zrobic_z_nadplata", _ZWROT, "numer_rachunku"),
        ],
    )
    return DomainLogicEnricher(get_settings().DICTIONARIES_DIR).enrich(raw)


class MockFormPipeline:
    """Realistyczny stub zwracający bogaty JSON zgodny ze schematem COI."""

    def convert(self, *, filename: str, content: bytes, mime_type: str) -> PipelineResult:
        return PipelineResult(
            form=build_mock_form(filename, mime_type),
            warnings=[
                ConversionWarning(
                    code="MOCK_PIPELINE",
                    message=(
                        "Wynik wygenerowany przez mock pipeline (USE_MOCK_PIPELINE=true). "
                        "Ustaw false po zaimplementowaniu modułów Brancha 1."
                    ),
                )
            ],
        )

    def model_status(self) -> dict[str, str]:
        return {"ocr": "mock", "llm": "mock"}


class RealFormPipeline:
    """Kompozycja modułów Brancha 1: ingestion -> ai_extractor -> domain_logic."""

    def __init__(self, settings: Settings) -> None:
        from backend.app.modules.ai_extractor.extractor import LlmStructureExtractor
        from backend.app.modules.ai_extractor.llm_backend import build_llm_backend
        from backend.app.modules.domain_logic.mapper import DomainLogicEnricher
        from backend.app.modules.ingestion.parsers import MultiFormatParser

        self._settings = settings
        self._parser = MultiFormatParser()
        self._llm_backend = build_llm_backend(settings)
        self._extractor = LlmStructureExtractor(self._llm_backend)
        self._enricher = DomainLogicEnricher(dictionaries_dir=settings.DICTIONARIES_DIR)

    def convert(self, *, filename: str, content: bytes, mime_type: str) -> PipelineResult:
        try:
            parsed = self._parser.parse(filename=filename, content=content, mime_type=mime_type)
            form = self._extractor.extract(parsed)
            form = self._enricher.enrich(form)
        except NotImplementedError as exc:
            raise PipelineNotReadyError(
                "Realny pipeline nie jest jeszcze zaimplementowany. "
                "Ustaw USE_MOCK_PIPELINE=true lub ukończ moduły Brancha 1 "
                "(feat/backend-ai-pipeline).",
                {"reason": str(exc)},
            ) from exc
        except ImportError as exc:
            raise PipelineNotReadyError(
                "Brak bibliotek realnego pipeline (extras [ai,llm,llm-api]). "
                "Zainstaluj: pip install -e '.[ai,llm,llm-api]', użyj obrazu "
                "ghcr.io/porzeraklon/formularzex:0.2.3-ai albo ustaw USE_MOCK_PIPELINE=true.",
                {"reason": str(exc)},
            ) from exc
        return PipelineResult(form=form)

    def model_status(self) -> dict[str, str]:
        # Modele ONNX RapidOCR są wewnątrz wheela — gotowość znaczy „biblioteka jest”,
        # a nie „katalog MODELS_DIR jest niepusty” (tam leżą tylko wagi GGUF).
        return {
            "ocr": "ready" if find_spec("rapidocr_onnxruntime") else "missing",
            "llm": "ready" if self._llm_backend.ready() else "missing",
        }


def get_pipeline(settings: Settings) -> FormPipeline:
    if settings.USE_MOCK_PIPELINE:
        return MockFormPipeline()
    return RealFormPipeline(settings)


def run_conversion(
    pipeline: FormPipeline, settings: Settings, filename: str, content: bytes, mime_type: str
) -> ConversionResponse:
    started = time.perf_counter()
    result = pipeline.convert(filename=filename, content=content, mime_type=mime_type)
    elapsed_ms = (time.perf_counter() - started) * 1000
    fields_count = sum(
        len(list(iter_fields(section.fields))) for page in result.form.pages for section in page.sections
    )
    service = DictionaryService(settings)
    known = [
        service.get_dictionary(dictionary_id) for dictionary_id in sorted(service.known_dictionary_ids())
    ]
    exported = convert_formdefinition(result.form, known)
    return ConversionResponse(
        form=result.form,
        form_definition=exported["form"],
        form_definition_issues=validate_form_definition(exported["form"]),
        processing=ProcessingMetadata(
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            processing_time_ms=round(elapsed_ms, 2),
            pipeline_mode=settings.pipeline_mode,
            pages_count=len(result.form.pages),
            fields_count=fields_count,
        ),
        warnings=result.warnings,
    )
