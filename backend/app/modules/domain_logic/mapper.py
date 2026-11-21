"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Logika domenowa: mapowanie pól do lokalnej bazy słowników COI, generowanie
propozycji nowych słowników, wnioskowanie zależności warunkowych (conditional
flows) i przypisywanie reguł walidacji (PESEL, PL_PHONE, NIP, REGON, POSTAL_CODE,
długość, regex). Gotowe walidatory: backend/app/services/field_validation.py.
"""

from pathlib import Path

from backend.app.schemas.form_schema import FormDocumentSchema


class DomainLogicEnricher:
    """Implementacja protokołu DomainEnricher."""

    def __init__(self, dictionaries_dir: Path) -> None:
        self._dictionaries_dir = dictionaries_dir

    def enrich(self, form: FormDocumentSchema) -> FormDocumentSchema:
        # TODO(branch-1):
        #   1. Dopasowanie pól select/radio do słowników COI (mapped_dictionaries,
        #      confidence) na podstawie etykiet i treści.
        #   2. Generator proposed_dictionaries, gdy brak słownika w bazie.
        #   3. Wnioskowanie ConditionalFlow (SHOW/HIDE/REQUIRE/OPTIONAL/SET_VALUE)
        #      między polami, sekcjami i stronami.
        #   4. Przypisanie ValidationRules (custom_validator, pattern, długości).
        raise NotImplementedError("TODO(branch-1): mapowanie słowników, zależności, walidacje")
