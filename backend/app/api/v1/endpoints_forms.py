"""Endpointy konwersji i walidacji formularzy (działają od razu w core)."""

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from backend.app.core.config import Settings, get_settings
from backend.app.core.exceptions import AppError, BatchTooLargeError, FileValidationError
from backend.app.core.security import read_upload_limited, require_auth, validate_upload_bytes
from backend.app.schemas.api_models import (
    BatchConversionResponse,
    BatchItemResult,
    BatchSummary,
    ConversionResponse,
    ErrorDetail,
    FormDefinitionExportRequest,
    FormDefinitionExportResponse,
    JsonValidationRequest,
    JsonValidationResponse,
    ValidationCheckRequest,
    ValidationCheckResponse,
)
from backend.app.schemas.form_schema import FormDocumentSchema
from backend.app.services.dictionary_service import DictionaryService
from backend.app.services.field_validation import validate_value
from backend.app.services.form_consistency import validate_form_consistency
from backend.app.services.formdefinition_export import convert_formdefinition
from backend.app.services.pipeline_service import get_pipeline, run_conversion

router = APIRouter(prefix="/forms", tags=["forms"], dependencies=[Depends(require_auth)])


@router.post("/convert", response_model=ConversionResponse)
async def convert_form(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> ConversionResponse:
    content = await read_upload_limited(file, settings.MAX_FILE_SIZE_BYTES)
    validated = validate_upload_bytes(file.filename, content, settings)
    pipeline = get_pipeline(settings)
    # Synchroniczna inferencja (LLM/OCR) w threadpoolu - nie blokuje event loop.
    return await run_in_threadpool(
        run_conversion, pipeline, settings, validated.filename, validated.content, validated.mime_type
    )


@router.post("/convert-batch", response_model=BatchConversionResponse)
async def convert_batch(
    files: list[UploadFile] = File(...),
    settings: Settings = Depends(get_settings),
) -> BatchConversionResponse:
    if len(files) > settings.MAX_BATCH_FILES:
        raise BatchTooLargeError(
            f"Batch przekracza limit {settings.MAX_BATCH_FILES} plików (otrzymano {len(files)}).",
            {"count": len(files), "max_batch_files": settings.MAX_BATCH_FILES},
        )
    pipeline = get_pipeline(settings)
    results: list[BatchItemResult] = []
    for upload in files:
        filename = upload.filename or ""
        try:
            content = await read_upload_limited(upload, settings.MAX_FILE_SIZE_BYTES)
            validated = validate_upload_bytes(filename, content, settings)
            conversion = await run_in_threadpool(
                run_conversion, pipeline, settings, validated.filename, validated.content, validated.mime_type
            )
            results.append(
                BatchItemResult(
                    filename=validated.filename,
                    status="success",
                    form=conversion.form,
                    processing=conversion.processing,
                    warnings=conversion.warnings,
                )
            )
        except AppError as exc:
            results.append(
                BatchItemResult(
                    filename=filename,
                    status="error",
                    error=ErrorDetail(code=exc.code, message=exc.message, details=exc.details),
                )
            )
    succeeded = sum(1 for item in results if item.status == "success")
    return BatchConversionResponse(
        results=results,
        summary=BatchSummary(total=len(results), succeeded=succeeded, failed=len(results) - succeeded),
    )


@router.post("/validate-json", response_model=JsonValidationResponse)
def validate_json(
    payload: JsonValidationRequest,
    settings: Settings = Depends(get_settings),
) -> JsonValidationResponse:
    try:
        form = FormDocumentSchema.model_validate(payload.form)
    except ValidationError as exc:
        return JsonValidationResponse(
            valid=False,
            schema_errors=[
                f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
            ],
        )
    known_ids = DictionaryService(settings).known_dictionary_ids()
    consistency_errors, warnings = validate_form_consistency(form, known_ids)
    return JsonValidationResponse(
        valid=not consistency_errors,
        consistency_errors=consistency_errors,
        warnings=warnings,
    )


@router.post("/export-formdefinition", response_model=FormDefinitionExportResponse)
def export_formdefinition(
    payload: FormDefinitionExportRequest,
    settings: Settings = Depends(get_settings),
) -> FormDefinitionExportResponse:
    try:
        form = FormDocumentSchema.model_validate(payload.form)
    except ValidationError as exc:
        raise FileValidationError(
            "FORM_VALIDATION_ERROR",
            "JSON nie jest poprawny względem wewnętrznego schematu formularza.",
            {
                "errors": [
                    f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
                ]
            },
        ) from exc
    service = DictionaryService(settings)
    known = [
        service.get_dictionary(dictionary_id) for dictionary_id in sorted(service.known_dictionary_ids())
    ]
    exported = convert_formdefinition(form, known)
    return FormDefinitionExportResponse(form_definition=exported["form"], notes=exported["notes"])


@router.post("/validate-field", response_model=ValidationCheckResponse)
def validate_field(payload: ValidationCheckRequest) -> ValidationCheckResponse:
    valid, errors, normalized = validate_value(payload.value, payload.rules)
    return ValidationCheckResponse(valid=valid, errors=errors, normalized_value=normalized)
