"""Dostęp do lokalnej bazy słowników COI (data/dictionaries/, własność Brancha 1).

Baza to wszystkie pliki ``*.json`` z katalogu ``DICTIONARIES_DIR`` (wczytywane w kolejności
nazw), każdy w formacie ``{"dictionaries": [{dictionary_id, name, description, items}]}``.
Pliki TERYT (Powiat, Gmina, Miejscowość) można przegenerować narzędziem
``python -m backend.app.modules.domain_logic.teryt_import``.
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from backend.app.core.config import Settings
from backend.app.core.exceptions import AppError, NotFoundError
from backend.app.schemas.api_models import (
    DictionaryDetailResponse,
    DictionaryListResponse,
    DictionarySummary,
)
from backend.app.schemas.form_schema import DictionaryItem

# Plik bazowy (województwa, płeć, dokumenty, stany cywilne) — zachowany dla zgodności;
# serwis wczytuje wszystkie pliki *.json z katalogu, nie tylko ten.
DICTIONARIES_FILENAME = "sample_dictionaries.json"
DICTIONARIES_GLOB = "*.json"


class DictionaryDatabaseError(AppError):
    """Uszkodzona lub niespójna baza słowników (błędny JSON, zdublowane dictionary_id)."""

    status_code = 500
    code = "DICTIONARY_DATABASE_ERROR"


def _parse_file(path: Path) -> list[DictionaryDetailResponse]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        return [
            DictionaryDetailResponse(
                dictionary_id=entry["dictionary_id"],
                name=entry["name"],
                description=entry.get("description"),
                items=[DictionaryItem(**item) for item in entry["items"]],
            )
            for entry in raw["dictionaries"]
        ]
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
        raise DictionaryDatabaseError(
            f"Nieprawidłowy plik słowników: {path.name}.", {"file": path.name, "reason": str(exc)}
        ) from exc


@lru_cache(maxsize=8)
def _load_dictionaries(dir_str: str) -> tuple[DictionaryDetailResponse, ...]:
    directory = Path(dir_str)
    if not directory.is_dir():
        raise NotFoundError(f"Baza słowników nie istnieje: {directory}")
    files = sorted(path for path in directory.glob(DICTIONARIES_GLOB) if path.is_file())
    if not files:
        raise NotFoundError(f"Baza słowników jest pusta: {directory}")
    entries: list[DictionaryDetailResponse] = []
    origin: dict[str, str] = {}
    for path in files:
        for entry in _parse_file(path):
            if entry.dictionary_id in origin:
                raise DictionaryDatabaseError(
                    f"Zdublowany słownik '{entry.dictionary_id}' w plikach "
                    f"{origin[entry.dictionary_id]} i {path.name}.",
                    {"dictionary_id": entry.dictionary_id, "files": [origin[entry.dictionary_id], path.name]},
                )
            origin[entry.dictionary_id] = path.name
            entries.append(entry)
    return tuple(entries)


class DictionaryService:
    def __init__(self, settings: Settings) -> None:
        self._dir = settings.DICTIONARIES_DIR

    def _entries(self) -> tuple[DictionaryDetailResponse, ...]:
        return _load_dictionaries(str(self._dir.resolve()))

    def list_dictionaries(self) -> DictionaryListResponse:
        summaries = [
            DictionarySummary(
                dictionary_id=entry.dictionary_id,
                name=entry.name,
                description=entry.description,
                items_count=len(entry.items),
            )
            for entry in self._entries()
        ]
        return DictionaryListResponse(dictionaries=summaries, total=len(summaries))

    def get_dictionary(self, dictionary_id: str) -> DictionaryDetailResponse:
        for entry in self._entries():
            if entry.dictionary_id == dictionary_id:
                return entry
        raise NotFoundError(f"Słownik '{dictionary_id}' nie istnieje w bazie COI.")

    def known_dictionary_ids(self) -> set[str]:
        return {entry.dictionary_id for entry in self._entries()}
