"""Endpointy lokalnej bazy słowników i danych inicjalnych COI."""

from fastapi import APIRouter, Depends

from backend.app.core.config import Settings, get_settings
from backend.app.core.security import require_auth
from backend.app.schemas.api_models import DictionaryDetailResponse, DictionaryListResponse
from backend.app.services.dictionary_service import DictionaryService

router = APIRouter(prefix="/dictionaries", tags=["dictionaries"], dependencies=[Depends(require_auth)])


@router.get("", response_model=DictionaryListResponse)
def list_dictionaries(settings: Settings = Depends(get_settings)) -> DictionaryListResponse:
    return DictionaryService(settings).list_dictionaries()


@router.get("/{dictionary_id}", response_model=DictionaryDetailResponse)
def get_dictionary(
    dictionary_id: str, settings: Settings = Depends(get_settings)
) -> DictionaryDetailResponse:
    return DictionaryService(settings).get_dictionary(dictionary_id)
