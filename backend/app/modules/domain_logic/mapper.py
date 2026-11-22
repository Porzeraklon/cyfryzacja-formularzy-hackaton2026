"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Logika domenowa (DomainLogicEnricher): mapowanie pól na 6 predefiniowanych
słowników COI (materiał „Słowniki”), propozycje nowych słowników dla opcji
spoza katalogu, walidatory i słowniki wnioskowane z etykiet (port heurystyk
frontend/formdef.js) oraz deterministyczne wstawienie sekcji ze Standardów
(predefined.py): strona 1 „Dane wnioskodawcy”, ostatnia „Kontakt i zgody”.
Sekcje LLM o tytułach pokrywających się z predefiniowanymi są usuwane.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from backend.app.schemas.form_schema import (
    ConditionalFlow,
    ConditionOperator,
    ConsentItem,
    CustomValidator,
    DictionariesBlock,
    FieldDefinition,
    FieldType,
    FormDocumentSchema,
    MappedDictionary,
    PageDefinition,
    ProposedDictionary,
    SectionDefinition,
    TargetType,
    ValidationRules,
)
from backend.app.services.dictionary_service import DICTIONARIES_FILENAME, _load_dictionaries

from .predefined import applicant_sections, contact_sections

APPLICANT_PAGE_TITLE = "Dane wnioskodawcy"
CONTACT_PAGE_TITLE = "Kontakt i zgody"

# Port FD_DICTIONARIES z formdef.js: nazwa + aliasy (fdNormalize) + id w bazie.
_OFFICIAL_DICTIONARIES: list[dict[str, Any]] = [
    {
        "name": "Województwo",
        "aliases": ["WOJEWODZTWO", "WOJEWODZTWA", "dict_voivodeships"],
        "backend_id": "dict_voivodeships",
    },
    {"name": "Powiat", "aliases": ["POWIAT", "POWIATY", "dict_districts"], "backend_id": "dict_districts"},
    {"name": "Gmina", "aliases": ["GMINA", "GMINY", "dict_communities"], "backend_id": "dict_communities"},
    {
        "name": "Miejscowość",
        "aliases": ["MIEJSCOWOSC", "MIEJSCOWOSCI", "TERYT_4G_DOWODOWY", "dict_places"],
        "backend_id": "dict_places",
    },
    {
        "name": "Kraje",
        "aliases": ["KRAJE", "KRAJ", "PANSTWA", "dict_countries"],
        "backend_id": "dict_countries",
    },
    {
        "name": "Numery kierunkowe",
        "aliases": ["NUMERY_KIERUNKOWE", "NUMERY KIERUNKOWE", "dict_dial_codes"],
        "backend_id": "dict_dial_codes",
    },
]

# Port FD_LABEL_VALIDATORS / FD_LABEL_DICTIONARIES z formdef.js.
_LABEL_VALIDATORS: list[tuple[re.Pattern[str], CustomValidator]] = [
    (re.compile(r"pesel", re.IGNORECASE), CustomValidator.PESEL),
    (re.compile(r"\bnip\b", re.IGNORECASE), CustomValidator.NIP),
    (re.compile(r"\bregon\b", re.IGNORECASE), CustomValidator.REGON),
    (re.compile(r"kod pocztowy", re.IGNORECASE), CustomValidator.ZIP_CODE),
    (re.compile(r"e-?mail", re.IGNORECASE), CustomValidator.EMAIL),
    (re.compile(r"epuap|skrytk", re.IGNORECASE), CustomValidator.EPUAP),
    (re.compile(r"e-?dor[eę]cze|\bade\b", re.IGNORECASE), CustomValidator.ADE),
    (
        re.compile(r"seria i numer dowodu|numer dowodu osobistego", re.IGNORECASE),
        CustomValidator.SERIAL_NUMBER_ID_CARD,
    ),
]
_LABEL_DICTIONARIES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"^kraj\b|^państwo\b|obywatelstwo|kraj urodzenia|kraj zamieszkania", re.IGNORECASE),
        "dict_countries",
    ),
    (re.compile(r"^województwo", re.IGNORECASE), "dict_voivodeships"),
    (re.compile(r"^powiat", re.IGNORECASE), "dict_districts"),
    (re.compile(r"^gmina", re.IGNORECASE), "dict_communities"),
    (re.compile(r"numer kierunkowy|prefiks telefon", re.IGNORECASE), "dict_dial_codes"),
]

_CHOICE_TYPES = {FieldType.SELECT, FieldType.RADIO, FieldType.CHECKBOX}


def _normalize(text: str) -> str:
    """Port fdNormalize: NFD bez diakrytyków, ł→l, upper, spacje/myślniki→_."""
    decomposed = unicodedata.normalize("NFD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    stripped = stripped.replace("ł", "l").replace("Ł", "L")
    return re.sub(r"[\s-]+", "_", stripped.strip().upper())


def _find_official(ref: str) -> dict[str, Any] | None:
    key = _normalize(ref)
    for entry in _OFFICIAL_DICTIONARIES:
        if _normalize(entry["name"]) == key or any(_normalize(alias) == key for alias in entry["aliases"]):
            return entry
    return None


# Oświadczenia/zgody rozpoznawane po treści (checkbox bez opcji), jak w eksporterze FormDefinition.
_CONSENT_LABEL = re.compile(
    r"o[śs]wiadczam|wyra[żz]am zgod|zapozna[łl]|zapoznaj|akceptuj|zgod[ay] na", re.IGNORECASE
)


def _is_consent_field(field: FieldDefinition) -> bool:
    if field.field_type == FieldType.CONSENT_SECTION:
        return True
    return (
        field.field_type == FieldType.CHECKBOX
        and not field.options
        and not field.dictionary_ref
        and not field.dependencies
        and bool(_CONSENT_LABEL.search(field.label or ""))
    )


def _iter_fields(fields: list[FieldDefinition]) -> Any:
    for field in fields:
        yield field
        if field.fields:
            yield from _iter_fields(field.fields)


class DomainLogicEnricher:
    """Implementacja protokołu DomainEnricher."""

    def __init__(self, dictionaries_dir: Path) -> None:
        self._dictionaries_dir = dictionaries_dir

    def enrich(self, form: FormDocumentSchema) -> FormDocumentSchema:
        result = form.model_copy(deep=True)
        known_ids = {
            entry.dictionary_id
            for entry in _load_dictionaries(str(self._dictionaries_dir / DICTIONARIES_FILENAME))
        }

        applicant = applicant_sections(office_name=result.metadata.office_name, form_title=result.metadata.title)
        contact = contact_sections()
        predefined_titles = {_normalize(section.title) for section in [*applicant, *contact]}
        injected_page_titles = {_normalize(APPLICANT_PAGE_TITLE), _normalize(CONTACT_PAGE_TITLE)}

        middle: list[PageDefinition] = []
        seen_page_titles: set[str] = set(injected_page_titles)
        for page in result.pages:
            if _normalize(page.title) in injected_page_titles:
                continue
            kept = [s for s in page.sections if _normalize(s.title) not in predefined_titles]
            if not kept:
                continue
            page.sections = kept
            base_title = page.title
            title_key = _normalize(base_title)
            suffix = 2
            while title_key in seen_page_titles:
                page.title = f"{base_title} ({suffix})"
                title_key = _normalize(page.title)
                suffix += 1
            seen_page_titles.add(title_key)
            middle.append(page)

        removed_ids = self._move_consents_to_end(middle, contact)
        middle = [page for page in middle if page.sections]

        pages = [
            PageDefinition(
                page_id="page_wnioskodawca",
                title=APPLICANT_PAGE_TITLE,
                order=1,
                sections=applicant,
            ),
            *middle,
            PageDefinition(
                page_id="page_kontakt",
                title=CONTACT_PAGE_TITLE,
                order=len(middle) + 2,
                sections=contact,
            ),
        ]

        remap = self._unique_ids(pages)
        for flow in result.conditional_flows:
            self._remap_flow(flow, remap)
        for page in pages:
            for flow in page.visibility_rules:
                self._remap_flow(flow, remap)
            for section in page.sections:
                for flow in section.visibility_rules:
                    self._remap_flow(flow, remap)
                for field in _iter_fields(section.fields):
                    for flow in field.dependencies:
                        self._remap_flow(flow, remap)

        mapped: list[MappedDictionary] = []
        proposed: list[ProposedDictionary] = []
        proposed_ids: set[str] = set()
        for page in middle:
            for section in page.sections:
                for field in _iter_fields(section.fields):
                    self._enrich_llm_field(field, known_ids, mapped, proposed, proposed_ids)
        for section in [*applicant, *contact]:
            for field in _iter_fields(section.fields):
                official = _find_official(field.dictionary_ref) if field.dictionary_ref else None
                if official:
                    mapped.append(
                        MappedDictionary(
                            field_id=field.field_id,
                            dictionary_id=official["backend_id"],
                            dictionary_name=official["name"],
                            confidence=1.0,
                        )
                    )

        if removed_ids:
            self._drop_flows_to(result, pages, removed_ids)

        for page_order, page in enumerate(pages, start=1):
            page.order = page_order
            for section_order, section in enumerate(page.sections, start=1):
                section.order = section_order

        result.pages = pages
        result.dictionaries = DictionariesBlock(mapped_dictionaries=mapped, proposed_dictionaries=proposed)
        return result

    @staticmethod
    def _move_consents_to_end(middle: list[PageDefinition], contact: list[SectionDefinition]) -> set[str]:
        """Oświadczenia i zgody z treści dokumentu -> jedna sekcja zgód na przedostatniej stronie.

        Standardy („Oświadczenie”): zgody i oświadczenia umieszczamy na końcu formularza, w polu
        typu „Sekcja zgód”, nie jako checkbox/radio. Tytuł sekcji: „Oświadczenie” / „Zgoda”, gdy
        jest jeden element, „Zgody i oświadczenia”, gdy więcej. Zwraca id usuniętych pól.
        """
        consent_section = next(
            (s for s in contact if any(f.field_type == FieldType.CONSENT_SECTION for f in s.fields)), None
        )
        if consent_section is None:
            return set()
        target = next(f for f in consent_section.fields if f.field_type == FieldType.CONSENT_SECTION)
        consents = list(target.consents or [])
        removed: set[str] = set()
        for page in middle:
            kept_sections = []
            for section in page.sections:
                moved = [f for f in section.fields if _is_consent_field(f)]
                if not moved:
                    kept_sections.append(section)
                    continue
                for field in moved:
                    removed.add(field.field_id)
                    items = field.consents or [ConsentItem(description=field.label, required=field.required)]
                    for item in items:
                        if _normalize(item.description) not in {_normalize(c.description) for c in consents}:
                            consents.append(item)
                section.fields = [f for f in section.fields if f.field_id not in removed]
                if section.fields:
                    kept_sections.append(section)
            page.sections = kept_sections
        if len(consents) > len(target.consents or []):
            target.consents = consents
            target.required = any(c.required for c in consents)
            consent_section.title = target.label = "Zgody i oświadczenia"
        return removed

    @staticmethod
    def _drop_flows_to(result: FormDocumentSchema, pages: list[PageDefinition], removed: set[str]) -> None:
        """Usuwa zależności odwołujące się do pól przeniesionych do sekcji zgód."""

        def keep(flow: ConditionalFlow) -> bool:
            return flow.source_field_id not in removed and flow.target_id not in removed

        result.conditional_flows = [f for f in result.conditional_flows if keep(f)]
        for page in pages:
            page.visibility_rules = [f for f in page.visibility_rules if keep(f)]
            for section in page.sections:
                section.visibility_rules = [f for f in section.visibility_rules if keep(f)]
                for field in _iter_fields(section.fields):
                    field.dependencies = [f for f in field.dependencies if keep(f)]

    def _enrich_llm_field(
        self,
        field: FieldDefinition,
        known_ids: set[str],
        mapped: list[MappedDictionary],
        proposed: list[ProposedDictionary],
        proposed_ids: set[str],
    ) -> None:
        rules = field.validation_rules
        if field.field_type == FieldType.TEXT and (rules is None or rules.custom_validator is None):
            for pattern, validator in _LABEL_VALIDATORS:
                if pattern.search(field.label):
                    if rules is None:
                        field.validation_rules = ValidationRules(custom_validator=validator)
                    else:
                        rules.custom_validator = validator
                    break

        official = _find_official(field.dictionary_ref) if field.dictionary_ref else None
        if official:
            field.dictionary_ref = official["backend_id"]
            mapped.append(
                MappedDictionary(
                    field_id=field.field_id,
                    dictionary_id=official["backend_id"],
                    dictionary_name=official["name"],
                    confidence=1.0,
                )
            )
            return

        if field.dictionary_ref is None and field.field_type in {FieldType.TEXT, FieldType.SELECT}:
            for pattern, backend_id in _LABEL_DICTIONARIES:
                if pattern.search(field.label):
                    field.dictionary_ref = backend_id
                    if field.field_type == FieldType.TEXT:
                        field.field_type = FieldType.SELECT
                    official = _find_official(backend_id)
                    assert official is not None
                    mapped.append(
                        MappedDictionary(
                            field_id=field.field_id,
                            dictionary_id=backend_id,
                            dictionary_name=official["name"],
                            confidence=0.8,
                        )
                    )
                    return

        if field.field_type in _CHOICE_TYPES and field.options:
            if field.dictionary_ref and field.dictionary_ref in known_ids:
                return
            proposed_id = f"prop_{field.field_id}"
            if proposed_id not in proposed_ids:
                proposed.append(
                    ProposedDictionary(
                        proposed_id=proposed_id,
                        name=field.label or field.name,
                        target_field_id=field.field_id,
                        reason="Opcje z dokumentu źródłowego — brak słownika w katalogu COI.",
                        items=field.options,
                    )
                )
                proposed_ids.add(proposed_id)
            field.dictionary_ref = proposed_id
        elif field.dictionary_ref and field.dictionary_ref not in known_ids:
            field.dictionary_ref = None

    def _unique_ids(self, pages: list[PageDefinition]) -> dict[str, str]:
        """Zapewnia unikalne field_id/section_id (predefiniowane mają pierwszeństwo)."""
        used: set[str] = set()
        remap: dict[str, str] = {}

        def reserve(old_id: str) -> str:
            if old_id not in used:
                used.add(old_id)
                return old_id
            suffix = 2
            while f"{old_id}_{suffix}" in used:
                suffix += 1
            new_id = f"{old_id}_{suffix}"
            used.add(new_id)
            remap[old_id] = new_id
            return new_id

        predefined_pages = [pages[0], pages[-1]]
        for page in predefined_pages:
            used.add(page.page_id)
            for section in page.sections:
                used.add(section.section_id)
                for field in _iter_fields(section.fields):
                    used.add(field.field_id)

        for page in pages[1:-1]:
            page.page_id = reserve(page.page_id)
            for section in page.sections:
                section.section_id = reserve(section.section_id)
                for field in _iter_fields(section.fields):
                    field.field_id = reserve(field.field_id)
        return remap

    def _remap_flow(self, flow: ConditionalFlow, remap: dict[str, str]) -> None:
        if not remap:
            return
        flow.source_field_id = remap.get(flow.source_field_id, flow.source_field_id)
        if flow.target_type in {TargetType.FIELD, TargetType.SECTION}:
            flow.target_id = remap.get(flow.target_id, flow.target_id)
        if flow.operator in {ConditionOperator.ALL, ConditionOperator.ANY} and isinstance(flow.value, list):
            for sub in flow.value:
                self._remap_subcondition(sub, remap)

    def _remap_subcondition(self, sub: Any, remap: dict[str, str]) -> None:
        if not isinstance(sub, dict):
            return
        if "field_id" in sub:
            sub["field_id"] = remap.get(sub["field_id"], sub["field_id"])
        elif isinstance(sub.get("value"), list):
            for child in sub["value"]:
                self._remap_subcondition(child, remap)
