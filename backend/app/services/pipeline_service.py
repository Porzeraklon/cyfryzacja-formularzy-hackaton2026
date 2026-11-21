"""Orkiestrator pipeline: przełącza mock <-> real przez USE_MOCK_PIPELINE.

- MockFormPipeline (core): realistyczny, bogaty strukturalnie JSON wniosku
  urzędowego, aby Branch 2 mógł testować end-to-end od pierwszej minuty.
- RealFormPipeline: komponuje moduły Brancha 1 (backend/app/modules/).
  Stubby rzucają NotImplementedError -> API zwraca 503 PIPELINE_NOT_READY.
"""

import logging
import time
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.core.exceptions import AppError, PipelineNotReadyError
from backend.app.interfaces.pipeline import FormPipeline, PipelineResult
from backend.app.schemas.api_models import ConversionResponse, ConversionWarning, ProcessingMetadata
from backend.app.schemas.form_definition import FormDefinition
from backend.app.schemas.form_schema import DictionaryItem, FormDocumentSchema
from backend.app.services.dictionary_service import DictionaryService
from backend.app.services.form_definition_export import export_form_definition, used_dictionary_ids
from backend.app.services.mock_form import build_mock_form

logger = logging.getLogger(__name__)

# Błędy bazy słowników: brak pliku/słownika (AppError), I/O, uszkodzony JSON lub struktura.
_DICTIONARY_ERRORS = (AppError, OSError, ValueError, KeyError, TypeError)


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
    """Kompozycja modułów Brancha 1: ingestion -> ai_extractor -> domain_logic.

    Brak opcjonalnych bibliotek (extras ``[ai]``/``[llm]``) nie wywraca aplikacji: błąd importu
    jest zapamiętywany, /health raportuje modele jako "missing", a konwersja zwraca 503.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._import_error: ImportError | None = None
        try:
            from backend.app.modules.ai_extractor.extractor import LocalStructureExtractor
            from backend.app.modules.domain_logic.mapper import DomainLogicEnricher
            from backend.app.modules.ingestion.parsers import MultiFormatParser

            self._parser = MultiFormatParser()
            self._extractor = LocalStructureExtractor(models_dir=settings.MODELS_DIR)
            self._enricher = DomainLogicEnricher(dictionaries_dir=settings.DICTIONARIES_DIR)
        except ImportError as exc:
            logger.warning("Realny pipeline niedostępny (brak biblioteki): %s", exc)
            self._import_error = exc

    @staticmethod
    def _missing_dependency_error(exc: ImportError) -> PipelineNotReadyError:
        missing = getattr(exc, "name", None) or str(exc)
        return PipelineNotReadyError(
            f"Realny pipeline wymaga opcjonalnych bibliotek, których brakuje w środowisku ({missing}). "
            'Zainstaluj extras: pip install -e ".[ai,llm]" albo ustaw USE_MOCK_PIPELINE=true.',
            {"reason": str(exc), "missing_module": missing},
        )

    def convert(self, *, filename: str, content: bytes, mime_type: str) -> PipelineResult:
        if self._import_error is not None:
            raise self._missing_dependency_error(self._import_error)
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
        except ImportError as exc:  # leniwy import biblioteki [ai]/[llm] wewnątrz modułu
            raise self._missing_dependency_error(exc) from exc
        return PipelineResult(form=form)

    def model_status(self) -> dict[str, str]:
        if self._import_error is not None:
            return {"ocr": "missing", "llm": "missing"}
        models_dir = Path(self._settings.MODELS_DIR)
        has_models = models_dir.is_dir() and any(models_dir.iterdir())
        return {
            "ocr": "ready" if has_models else "missing",
            "llm": "ready" if has_models else "missing",
        }


def get_pipeline(settings: Settings) -> FormPipeline:
    if settings.USE_MOCK_PIPELINE:
        return MockFormPipeline()
    return RealFormPipeline(settings)


def _load_known_dictionaries(form: FormDocumentSchema, settings: Settings) -> dict[str, list[DictionaryItem]]:
    """Pozycje słowników z lokalnej bazy dla ``dictionary_ref`` użytych w formularzu.

    Błędy bazy słowników (brak pliku, nieznany id, uszkodzony JSON) nie mogą przerwać konwersji.
    """
    known: dict[str, list[DictionaryItem]] = {}
    refs = used_dictionary_ids(form)
    if not refs:
        return known
    try:
        service = DictionaryService(settings)
        available = service.known_dictionary_ids()
    except _DICTIONARY_ERRORS as exc:  # baza słowników jest opcjonalna dla eksportu
        logger.warning("Baza słowników niedostępna podczas eksportu FormDefinition: %s", exc)
        return known
    for dictionary_id in sorted(refs & available):
        try:
            known[dictionary_id] = list(service.get_dictionary(dictionary_id).items)
        except _DICTIONARY_ERRORS as exc:
            logger.warning("Nie udało się wczytać słownika %s: %s", dictionary_id, exc)
    return known


def build_form_definition(form: FormDocumentSchema, settings: Settings) -> tuple[FormDefinition | None, list[str]]:
    """Eksport do FormDefinition; błąd eksportu nie wywraca konwersji (wynik None + notatka)."""
    try:
        return export_form_definition(form, _load_known_dictionaries(form, settings))
    except Exception as exc:
        logger.exception("Eksport FormDefinition nie powiódł się")
        return None, [f"Eksport do FormDefinition nie powiódł się: {exc}"]


def run_conversion(
    pipeline: FormPipeline, settings: Settings, filename: str, content: bytes, mime_type: str
) -> ConversionResponse:
    started = time.perf_counter()
    result = pipeline.convert(filename=filename, content=content, mime_type=mime_type)
    form_definition, form_definition_notes = build_form_definition(result.form, settings)
    elapsed_ms = (time.perf_counter() - started) * 1000
    fields_count = sum(
        len(section.fields) for page in result.form.pages for section in page.sections
    )
    return ConversionResponse(
        form=result.form,
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
        form_definition=form_definition,
        form_definition_notes=form_definition_notes,
    )
