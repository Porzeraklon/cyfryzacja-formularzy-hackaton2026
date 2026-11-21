"""Orkiestrator pipeline: przełącza mock <-> real przez USE_MOCK_PIPELINE.

- MockFormPipeline (core): realistyczny, bogaty strukturalnie JSON wniosku
  urzędowego, aby Branch 2 mógł testować end-to-end od pierwszej minuty.
- RealFormPipeline: komponuje moduły Brancha 1 (backend/app/modules/).
  Stubby rzucają NotImplementedError -> API zwraca 503 PIPELINE_NOT_READY.
"""

import time
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.core.exceptions import PipelineNotReadyError
from backend.app.interfaces.pipeline import FormPipeline, PipelineResult
from backend.app.schemas.api_models import ConversionResponse, ConversionWarning, ProcessingMetadata
from backend.app.services.mock_form import build_mock_form


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
        from backend.app.modules.ai_extractor.extractor import LocalStructureExtractor
        from backend.app.modules.domain_logic.mapper import DomainLogicEnricher
        from backend.app.modules.ingestion.parsers import MultiFormatParser

        self._settings = settings
        self._parser = MultiFormatParser()
        self._extractor = LocalStructureExtractor(models_dir=settings.MODELS_DIR)
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
            "llm": "ready" if has_models else "missing",
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
    )
