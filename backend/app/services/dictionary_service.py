"""Dostęp do lokalnej bazy słowników COI (data/dictionaries/, własność Brancha 1)."""

import json
from functools import lru_cache
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.core.exceptions import NotFoundError
from backend.app.schemas.api_models import (
    DictionaryDetailResponse,
    DictionaryListResponse,
    DictionarySummary,
)
from backend.app.schemas.form_schema import DictionaryItem

DICTIONARIES_FILENAME = "sample_dictionaries.json"


@lru_cache(maxsize=8)
def _load_dictionaries(path_str: str) -> tuple[DictionaryDetailResponse, ...]:
    path = Path(path_str)
    if not path.is_file():
        raise NotFoundError(f"Baza słowników nie istnieje: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        DictionaryDetailResponse(
            dictionary_id=entry["dictionary_id"],
            name=entry["name"],
            description=entry.get("description"),
            items=[DictionaryItem(**item) for item in entry["items"]],
        )
        for entry in raw["dictionaries"]
    )


class DictionaryService:
    def __init__(self, settings: Settings) -> None:
        self._path = settings.DICTIONARIES_DIR / DICTIONARIES_FILENAME

    def list_dictionaries(self) -> DictionaryListResponse:
        entries = _load_dictionaries(str(self._path))
        summaries = [
            DictionarySummary(
                dictionary_id=entry.dictionary_id,
                name=entry.name,
                description=entry.description,
                items_count=len(entry.items),
            )
            for entry in entries
        ]
        return DictionaryListResponse(dictionaries=summaries, total=len(summaries))

    def get_dictionary(self, dictionary_id: str) -> DictionaryDetailResponse:
        for entry in _load_dictionaries(str(self._path)):
            if entry.dictionary_id == dictionary_id:
                return entry
        raise NotFoundError(f"Słownik '{dictionary_id}' nie istnieje w bazie COI.")

    def known_dictionary_ids(self) -> set[str]:
        return {entry.dictionary_id for entry in _load_dictionaries(str(self._path))}
