"""Orkiestrator pipeline: przełącza mock <-> real przez USE_MOCK_PIPELINE.

- MockFormPipeline (core): deterministyczny wniosek wg wzorca COI „Wniosek
  o stwierdzenie nadpłaty podatku” — bez AI/OCR, aby Branch 2 mógł testować
  end-to-end od pierwszej minuty.
- RealFormPipeline: komponuje moduły Brancha 1 (backend/app/modules/).
  Stubby rzucają NotImplementedError -> API zwraca 503 PIPELINE_NOT_READY.
"""

import time
from datetime import UTC, datetime
from pathlib import Path
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
        ),
        pages=[
            PageDefinition(
                page_id="page_sprawa",
                title="Nadpłata i deklaracja",
                order=1,
                description="Przedmiot nadpłaty, okresy, rozliczenie i korekta deklaracji.",
                sections=[
                    SectionDefinition(
                        section_id="informacje_o_nadplacie",
                        title="Informacje o nadpłacie",
                        order=1,
                        fields=[
                            _field("podatek", "Podatek, którego dotyczy nadpłata", required=True),
                            _field(
                                "kwota",
                                "Kwota nadpłaty",
                                FieldType.NUMBER,
                                required=True,
                                placeholder="Kwota w złotych, np. 120,50",
                            ),
                            _field(
                                "uzasadnienie_info",
                                "",
                                FieldType.TEXT_BLOCK,
                                help_text="Opisz krótko, skąd wynika nadpłata.",
                            ),
                            _field(
                                "uzasadnienie",
                                "Uzasadnienie",
                                FieldType.TEXTAREA,
                                required=True,
                                validation_rules=ValidationRules(min_length=10, max_length=2000),
                            ),
                        ],
                    ),
                    SectionDefinition(
                        section_id="sekcja_okresy",
                        title="Okresy nadpłaty",
                        order=2,
                        fields=[
                            _field(
                                "okresy_nadplaty",
                                "Okresy, których dotyczy nadpłata",
                                FieldType.REPEATABLE_GROUP,
                                help_text="Dodaj każdy rok, w którym powstała nadpłata.",
                                fields=[
                                    _field("okres_rok", "Rok", FieldType.NUMBER, required=True),
                                    _field("okres_data_deklaracji", "Data deklaracji", FieldType.DATE),
                                    _field(
                                        "okres_kwota",
                                        "Kwota nadpłaty w okresie",
                                        FieldType.NUMBER,
                                        required=True,
                                    ),
                                ],
                            ),
                        ],
                    ),
                    SectionDefinition(
                        section_id="rozliczenie_nadplaty",
                        title="Rozliczenie nadpłaty",
                        order=3,
                        fields=[
                            _field(
                                "co_z_nadplata",
                                "Co zrobić z nadpłatą?",
                                FieldType.RADIO,
                                required=True,
                                options=[
                                    DictionaryItem(code="zaliczyc", label=_ZALICZYC),
                                    DictionaryItem(code="zwrot", label=_ZWROT),
                                ],
                            ),
                            _field(
                                "nazwa_podatku",
                                "Nazwa podatku",
                                required=True,
                                dependencies=[_show("co_z_nadplata", _ZALICZYC, "nazwa_podatku")],
                            ),
                            _field(
                                "numer_rachunku",
                                "Numer rachunku bankowy",
                                required=True,
                                dependencies=[_show("co_z_nadplata", _ZWROT, "numer_rachunku")],
                            ),
                        ],
                    ),
                    SectionDefinition(
                        section_id="korekta_deklaracji",
                        title="Korekta deklaracji",
                        order=4,
                        fields=[
                            _field(
                                "korekta_info",
                                "",
                                FieldType.TEXT_BLOCK,
                                help_text="Dołącz deklarację, której dotyczy korekta.",
                            ),
                            _field("plik_deklaracji", "Plik deklaracji", FieldType.FILE, required=True),
                        ],
                    ),
                    SectionDefinition(
                        section_id="oswiadczenia_sprawy",
                        title="Oświadczenia",
                        order=5,
                        fields=[
                            _field(
                                "osw_prawdziwosc",
                                "Oświadczam, że dane podane we wniosku są zgodne z prawdą.",
                                FieldType.CHECKBOX,
                                required=True,
                            ),
                            _field(
                                "osw_zgoda",
                                "Wyrażam zgodę na przetwarzanie danych osobowych.",
                                FieldType.CHECKBOX,
                                required=True,
                            ),
                        ],
                    ),
                ],
            )
        ],
        conditional_flows=[
            ConditionalFlow(
                source_field_id="kwota",
                operator=ConditionOperator.GREATER_THAN,
                value=1000,
                target_type=TargetType.FIELD,
                target_id="nazwa_podatku",
                action=FlowAction.SHOW,
            ),
            ConditionalFlow(
                source_field_id="co_z_nadplata",
                operator=ConditionOperator.IN,
                value=[_ZALICZYC, _ZWROT],
                target_type=TargetType.SECTION,
                target_id="korekta_deklaracji",
                action=FlowAction.SHOW,
            ),
            ConditionalFlow(
                source_field_id="uzasadnienie",
                operator=ConditionOperator.IS_EMPTY,
                value=None,
                target_type=TargetType.FIELD,
                target_id="plik_deklaracji",
                action=FlowAction.HIDE,
            ),
            ConditionalFlow(
                source_field_id="co_z_nadplata",
                operator=ConditionOperator.EQUALS,
                value=_ZWROT,
                target_type=TargetType.FIELD,
                target_id="numer_rachunku",
                action=FlowAction.SET_VALUE,
                action_value="PL",
            ),
            ConditionalFlow(
                source_field_id="podatek",
                operator=ConditionOperator.EQUALS,
                value="PIT",
                target_type=TargetType.PAGE,
                target_id="page_sprawa",
                action=FlowAction.SHOW,
            ),
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
        return PipelineResult(form=form)

    def model_status(self) -> dict[str, str]:
        models_dir = Path(self._settings.MODELS_DIR)
        has_models = models_dir.is_dir() and any(models_dir.iterdir())
        return {
            "ocr": "ready" if has_models else "missing",
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
