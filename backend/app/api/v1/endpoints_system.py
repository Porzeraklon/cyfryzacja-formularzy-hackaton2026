"""Endpointy systemowe: health, config publiczny, dev-token JWT."""

from fastapi import APIRouter, Depends

from backend.app.core.config import Settings, get_settings
from backend.app.core.exceptions import ForbiddenError
from backend.app.core.security import create_access_token
from backend.app.schemas.api_models import HealthResponse, ModelStatus, PublicConfigResponse, TokenResponse
from backend.app.services.pipeline_service import get_pipeline

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    pipeline = get_pipeline(settings)
    status = pipeline.model_status()
    ready = settings.USE_MOCK_PIPELINE or all(v == "ready" for v in status.values())
    return HealthResponse(
        status="ok" if ready else "degraded",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENV,
        pipeline_mode=settings.pipeline_mode,
        models=ModelStatus(
            ocr=status.get("ocr", "unknown"),
            llm=status.get("llm", "unknown"),
            models_dir=str(settings.MODELS_DIR),
            ready=ready,
        ),
    )


@router.get("/config", response_model=PublicConfigResponse)
def public_config(settings: Settings = Depends(get_settings)) -> PublicConfigResponse:
    return PublicConfigResponse(
        allowed_extensions=settings.allowed_extensions,
        max_file_size_bytes=settings.MAX_FILE_SIZE_BYTES,
        max_batch_files=settings.MAX_BATCH_FILES,
        jwt_auth_enabled=settings.JWT_AUTH_ENABLED,
        environment=settings.ENV,
    )


@router.post("/auth/dev-token", response_model=TokenResponse)
def dev_token(settings: Settings = Depends(get_settings)) -> TokenResponse:
    if settings.ENV != "development":
        raise ForbiddenError("Endpoint dev-token jest aktywny wyłącznie w trybie ENV=development.")
    token = create_access_token("dev-user", settings, extra_claims={"role": "developer"})
    return TokenResponse(access_token=token, expires_in=settings.JWT_EXPIRE_MINUTES * 60)
