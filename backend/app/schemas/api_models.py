"""Request/Response DTO dla wszystkich endpointów API v1."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.form_schema import (
    DictionaryItem,
    FieldType,
    FormDocumentSchema,
    ValidationRules,
)


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(_ApiModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(_ApiModel):
    error: ErrorDetail


class ConversionWarning(_ApiModel):
    code: str
    message: str
    field_id: str | None = None


class ProcessingMetadata(_ApiModel):
    filename: str
    mime_type: str
    size_bytes: int
    processing_time_ms: float
    pipeline_mode: Literal["mock", "real"]
    pages_count: int
    fields_count: int


class ConversionResponse(_ApiModel):
    status: Literal["success"] = "success"
    form: FormDocumentSchema
    form_definition: dict[str, Any] | None = None
    processing: ProcessingMetadata
    warnings: list[ConversionWarning] = Field(default_factory=list)


class BatchItemResult(_ApiModel):
    filename: str
    status: Literal["success", "error"]
    form: FormDocumentSchema | None = None
    processing: ProcessingMetadata | None = None
    warnings: list[ConversionWarning] = Field(default_factory=list)
    error: ErrorDetail | None = None


class BatchSummary(_ApiModel):
    total: int
    succeeded: int
    failed: int


class BatchConversionResponse(_ApiModel):
    results: list[BatchItemResult]
    summary: BatchSummary


class DictionarySummary(_ApiModel):
    dictionary_id: str
    name: str
    description: str | None = None
    items_count: int


class DictionaryListResponse(_ApiModel):
    dictionaries: list[DictionarySummary]
    total: int


class DictionaryDetailResponse(_ApiModel):
    dictionary_id: str
    name: str
    description: str | None = None
    items: list[DictionaryItem]


class JsonValidationRequest(_ApiModel):
    form: dict[str, Any]


class JsonValidationResponse(_ApiModel):
    valid: bool
    schema_errors: list[str] = Field(default_factory=list)
    consistency_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FormDefinitionExportRequest(_ApiModel):
    form: dict[str, Any]


class FormDefinitionExportResponse(_ApiModel):
    form_definition: dict[str, Any]
    notes: list[str] = Field(default_factory=list)


class ValidationCheckRequest(_ApiModel):
    value: Any = None
    field_type: FieldType | None = None
    rules: ValidationRules | None = None


class ValidationCheckResponse(_ApiModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    normalized_value: str | None = None


class ModelStatus(_ApiModel):
    ocr: str
    llm: str
    models_dir: str
    ready: bool


class HealthResponse(_ApiModel):
    status: Literal["ok", "degraded"]
    app_name: str
    version: str
    environment: str
    pipeline_mode: Literal["mock", "real"]
    models: ModelStatus


class PublicConfigResponse(_ApiModel):
    allowed_extensions: list[str]
    max_file_size_bytes: int
    max_batch_files: int
    jwt_auth_enabled: bool
    environment: str
    api_version: str = "v1"


class TokenResponse(_ApiModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
