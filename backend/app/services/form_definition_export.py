"""Eksport wewnętrznego JSON-a formularza (FormDocumentSchema) do oficjalnego FormDefinition COI.

Port funkcji ``fdConvertInternal`` z ``frontend/formdef.js`` (ta sama semantyka i heurystyki),
rozszerzony o elementy nowego kontraktu: ``consent_section`` -> CONSENT_SECTION, ``text_block`` ->
TEXT_BLOCK, ``info_section`` -> INFO_SECTION, ``read_only`` -> ``readOnly``, dane inicjalne
(``default_value_type`` + ``default_value_key`` -> ``"<ŹRÓDŁO>.<atrybut>"`` + EXTERNAL_SYSTEM)
oraz strona ``page_type=FINISH`` -> zakładka FINISH.

Zasady mapowania:
- identyfikatory: deterministyczne UUID v5 z ziarna ``metadata.form_id`` (stabilne przy ponownym
  eksporcie tego samego formularza),
- ``techName``: unikalne snake_case (reguła 12 — jednoznaczne zapytania jsonata),
- ``validation``: tablica nazw z katalogu COI (REQUIRED + walidator niestandardowy; POSTAL_CODE ->
  ZIP_CODE; PL_PHONE pomijany z notatką); heurystyki etykiet tylko dla TEXT_FIELD,
- słowniki: ``dict_*`` z lokalnej bazy <-> ``dictionaryName`` z katalogu COI; słowniki spoza
  katalogu trafiają do ``options`` pola,
- sekcje -> pytania typu SECTION z ``repetingSectionGrid``; ``repeatable_group`` -> osobna
  REPEATING_SECTION na poziomie strony (reguła 6: bez zagnieżdżania sekcji),
- zależności warunkowe -> ``states`` jsonata (SHOW; HIDE jako ``$not(...)``),
- strona końcowa FINISH z INFO_SECTION (dokładana domyślnie, gdy formularz jej nie ma),
- ``flow.links`` łańcuchowo w kolejności stron.

Funkcja jest czysta (bez I/O) — słowniki z bazy przekazuje wywołujący.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.app.schemas.form_definition import (
    FD_CHOICE_TYPES,
    FD_DICTIONARIES,
    FD_DICTIONARY_ALIASES,
    FD_DICTIONARY_BY_BACKEND_ID,
    FD_PRESENTATION_TYPES,
    FdConsent,
    FdConsentSectionConfig,
    FdField,
    FdFlow,
    FdLink,
    FdOption,
    FdQuestion,
    FdState,
    FdTab,
    FormDefinition,
)
from backend.app.schemas.form_schema import (
    COI_VALIDATORS,
    ConditionalFlow,
    ConditionOperator,
    CustomValidator,
    DictionaryItem,
    FieldDefinition,
    FieldType,
    FlowAction,
    FormDocumentSchema,
    PageDefinition,
    PageType,
    SectionDefinition,
    TargetType,
)

# Stała przestrzeń nazw UUID v5 dla eksportu FormDefinition (nie zmieniać — zmieniłaby wszystkie id).
FORM_DEFINITION_UUID_NAMESPACE = uuid.UUID("5d0c4f7e-8a3b-4e6f-9c1d-2b7a6e9f0c31")

# Dane inicjalne: źródło -> dozwolone atrybuty (materiał "Dane inicjalne", tabele zakresu danych).
INITIAL_DATA_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "PESEL": (
        "pesel", "name", "surname", "birthDate", "citizenship", "motherName", "birthPlace",
        "motherSurname", "sex", "fatherName", "secondName", "birthCountry", "foreignBirthUSC",
        "familyName", "fatherSurname", "maritalStatus",
    ),
    "PESEL_ADDRESS": (
        "temporaryAddressVoivodeship", "temporaryAddressDistrict", "temporaryAddressCommunity",
        "temporaryAddressPlace", "temporaryAddressStreetType", "temporaryAddressStreet",
        "temporaryAddressHouseNumber", "temporaryAddressApartmentNumber", "temporaryAddressPostalCode",
        "permanentAddressVoivodeship", "permanentAddressDistrict", "permanentAddressCommunity",
        "permanentAddressPlace", "permanentAddressStreetType", "permanentAddressStreet",
        "permanentAddressPostalCode", "permanentAddressHouseNumber", "permanentAddressApartmentNumber",
    ),
    "MLPZ": (
        "name", "secondName", "surname", "pesel", "phoneNumber", "email", "country", "voivodeship",
        "district", "community", "place", "postCode", "street", "building", "apartment",
        "institutionName", "descriptiveOrganizationName",
    ),
}
EXTERNAL_SYSTEM = "EXTERNAL_SYSTEM"

FD_TYPE_MAP: dict[FieldType, str] = {
    FieldType.TEXT: "TEXT_FIELD",
    FieldType.NUMBER: "NUMBER",
    FieldType.DATE: "DATEPICKER",
    FieldType.SELECT: "SELECT",
    FieldType.RADIO: "RADIO",
    FieldType.CHECKBOX: "CHECKBOX",
    FieldType.TEXTAREA: "TEXTAREA",
    FieldType.FILE: "FILE_UPLOADER",
    FieldType.CONSENT_SECTION: "CONSENT_SECTION",
    FieldType.TEXT_BLOCK: "TEXT_BLOCK",
    FieldType.INFO_SECTION: "INFO_SECTION",
}

# Heurystyki etykiet -> walidatory (Standardy: Twoje dane, Dane podmiotu, Adres, Dane do kontaktu).
FD_LABEL_VALIDATORS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"pesel", re.IGNORECASE), "PESEL"),
    (re.compile(r"\bnip\b", re.IGNORECASE), "NIP"),
    (re.compile(r"\bregon\b", re.IGNORECASE), "REGON"),
    (re.compile(r"kod pocztowy", re.IGNORECASE), "ZIP_CODE"),
    (re.compile(r"e-?mail", re.IGNORECASE), "EMAIL"),
    (re.compile(r"epuap|skrytk", re.IGNORECASE), "EPUAP"),
    (re.compile(r"e-?dor[eę]cze|\bade\b", re.IGNORECASE), "ADE"),
    (re.compile(r"seria i numer dowodu|numer dowodu osobistego", re.IGNORECASE), "SERIAL_NUMBER_ID_CARD"),
)
# Heurystyki etykiet -> słowniki predefiniowane.
FD_LABEL_DICTIONARIES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^kraj\b|^państwo\b|obywatelstwo|kraj urodzenia|kraj zamieszkania", re.IGNORECASE), "Kraje"),
    (re.compile(r"^województwo", re.IGNORECASE), "Województwo"),
    (re.compile(r"^powiat", re.IGNORECASE), "Powiat"),
    (re.compile(r"^gmina", re.IGNORECASE), "Gmina"),
    (re.compile(r"numer kierunkowy|prefiks telefon", re.IGNORECASE), "Numery kierunkowe"),
)
# Sekcja "Twoje dane": dane wnioskodawcy z MLPZ, tylko do odczytu (Standardy, s. 10).
FD_OWN_DATA_SECTION = re.compile(
    r"twoje dane|dane wnioskodawcy|dane osobowe wnioskodawcy|dane osoby sk[łl]adaj", re.IGNORECASE
)
FD_OWN_DATA_EXCLUDED = re.compile(r"innej osoby|pe[łl]nomocnik|podmiot", re.IGNORECASE)
FD_OWN_DATA_FIELDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^imi[eę]( pierwsze)?$", re.IGNORECASE), "MLPZ.name"),
    (re.compile(r"^nazwisko$", re.IGNORECASE), "MLPZ.surname"),
    (re.compile(r"pesel", re.IGNORECASE), "MLPZ.pesel"),
)
FD_CONSENT_LABEL = re.compile(r"o[śs]wiadczam|wyra[żz]am zgod|zapozna[łl]|akceptuj|zgod[ay] na", re.IGNORECASE)

DEFAULT_FINISH_SECTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "podsumowanie",
        "Podsumowanie",
        (
            "Po wysłaniu wniosku urząd rozpatrzy sprawę. Jeśli będą potrzebne dodatkowe informacje, "
            "urzędnik skontaktuje się z Tobą."
        ),
    ),
    (
        "podglad",
        "Podgląd",
        (
            "Zanim przejdziesz do wyboru sposobu podpisu, sprawdź dane na podglądzie. "
            "Jeśli zobaczysz błędy, wróć do edycji."
        ),
    ),
)
TITLE_MAX_LENGTH = 255
SEARCH_THRESHOLD = 15  # Standardy: lista z ponad 15 opcjami powinna mieć wyszukiwanie


# ---------------------------------------------------------------------------------------------
# Narzędzia
# ---------------------------------------------------------------------------------------------


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    without = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without.replace("ł", "l").replace("Ł", "L")


def normalize_name(text: Any) -> str:
    """Klucz porównania nazw słowników: wielkie litery, bez diakrytyków, ``_`` zamiast spacji/myślnika."""
    return re.sub(r"[\s-]+", "_", _strip_accents(str(text or "")).strip().upper())


def slugify(text: Any, fallback: str = "pole") -> str:
    """snake_case ASCII (maks. 60 znaków) — jak ``fdSlug`` we frontendzie."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", _strip_accents(str(text or "")))
    slug = slug.strip("_").lower()[:60]
    return slug or fallback


def find_official_dictionary(name: str | None) -> str | None:
    """Zwraca oficjalną ``dictionaryName`` COI dla id z bazy (``dict_*``), nazwy lub aliasu."""
    if not name:
        return None
    if name in FD_DICTIONARY_BY_BACKEND_ID:
        return FD_DICTIONARY_BY_BACKEND_ID[name]
    key = normalize_name(name)
    for official, (backend_id, _types) in FD_DICTIONARIES.items():
        if key in (normalize_name(official), normalize_name(backend_id)):
            return official
    return FD_DICTIONARY_ALIASES.get(key)


def _jsonata_name(text: str) -> str:
    """Literał jsonata w apostrofach (jak w oficjalnym przykładzie)."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _jsonata_value(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _format_number(value: Any) -> str | None:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return str(int(number)) if number.is_integer() else repr(number)


def _section_tech_base(section: SectionDefinition) -> str:
    """techName sekcji: znaczący section_id (np. "adres_zamieszkania") albo slug tytułu.

    Identyfikatory techniczne z prefiksem "sec_" (np. "sec_1_2" z ekstraktora, "sec_address")
    nie są czytelne w jsonacie, więc wtedy techName powstaje z tytułu sekcji (jak we wzorze COI).
    """
    section_id = section.section_id or ""
    if section_id and not re.match(r"^(sec|section|sekcja)_", section_id, re.IGNORECASE):
        return section_id
    title_slug = slugify(section.title or "", fallback="")[:40].strip("_")
    return title_slug or section_id or "sekcja"


@dataclass
class _FieldRef:
    """Rejestr pola: dane potrzebne do zbudowania ścieżki jsonata i stanów."""

    tab_title: str
    section_tech: str
    tech_name: str
    fd_type: str
    choices: list[DictionaryItem]
    out: FdField
    in_repeating: bool = False

    @property
    def answer_path(self) -> str:
        base = (
            f"tabs[{_jsonata_name(self.tab_title)} in title]"
            f".questions[{_jsonata_name(self.section_tech)} in field.techName].field"
        )
        if self.in_repeating:
            # Reguły techniczne, przykład 2: odpowiedź z pola w sekcji powtarzającej się.
            return (
                f"{base}.__userAnswer[$repeatingSectionIndex]"
                f"[{_jsonata_name(self.tech_name)} in fieldTechName].answers"
            )
        return f"{base}.repetingSectionGrid[{_jsonata_name(self.tech_name)} in field.techName].field.__userAnswer"


@dataclass
class _Context:
    tab_title: str
    section_tech: str
    section_title: str
    tab_index: int
    in_repeating: bool = False
    finish: bool = False


@dataclass
class _Exporter:
    form: FormDocumentSchema
    known_dictionaries: dict[str, list[DictionaryItem]]
    notes: list[str] = field(default_factory=list)
    _used_tech: set[str] = field(default_factory=set)
    _used_ids: set[str] = field(default_factory=set)
    _refs: dict[str, _FieldRef] = field(default_factory=dict)
    _section_refs: dict[str, FdField] = field(default_factory=dict)
    _dropped_rules: list[str] = field(default_factory=list)
    _question_order: int = 0

    # --- identyfikatory --------------------------------------------------------------------

    @property
    def _seed(self) -> str:
        return self.form.metadata.form_id or self.form.metadata.title or "form"

    def uid(self, key: str) -> str:
        """Deterministyczny UUID v5 z ziarna ``<form_id>:<klucz>``; unikalny w obrębie eksportu."""
        candidate = key
        counter = 2
        while candidate in self._used_ids:
            candidate = f"{key}#{counter}"
            counter += 1
        self._used_ids.add(candidate)
        return str(uuid.uuid5(FORM_DEFINITION_UUID_NAMESPACE, f"{self._seed}:{candidate}"))

    def unique_tech(self, base: Any) -> str:
        root = slugify(base)
        tech = root
        counter = 2
        while tech in self._used_tech:
            tech = f"{root}_{counter}"
            counter += 1
        self._used_tech.add(tech)
        return tech

    def next_order(self) -> int:
        self._question_order += 1
        return self._question_order

    # --- pola --------------------------------------------------------------------------------

    def _choice_items(self, f: FieldDefinition) -> list[DictionaryItem]:
        if f.options:
            return list(f.options)
        if not f.dictionary_ref:
            return []
        for proposed in self.form.dictionaries.proposed_dictionaries:
            if proposed.proposed_id == f.dictionary_ref and proposed.items:
                return list(proposed.items)
        return list(self.known_dictionaries.get(f.dictionary_ref, []))

    @staticmethod
    def _label_for(value: Any, choices: list[DictionaryItem]) -> str:
        text = str(value)
        for item in choices:
            if item.code == text or item.label == text:
                return item.label
        return text

    def _resolve_type_and_dictionary(self, f: FieldDefinition, fd_type: str, label: str) -> tuple[str, str | None]:
        dict_ref = f.dictionary_ref
        official = find_official_dictionary(dict_ref) if dict_ref else None
        if official:
            allowed = FD_DICTIONARIES[official][1]
            if fd_type not in allowed:
                if fd_type in FD_CHOICE_TYPES or fd_type == "TEXT_FIELD":
                    self.notes.append(
                        f"Pole „{label}”: słownik {official} można przypisać wyłącznie do "
                        f"{', '.join(sorted(allowed))} — zamieniono {fd_type} na listę rozwijaną (SELECT)."
                    )
                    return "SELECT", official
                self.notes.append(
                    f"Pole „{label}” ({fd_type}): słownik {official} nie może być podpięty do tego typu "
                    "pola — pominięto słownik."
                )
                return fd_type, None
            return fd_type, official
        if (
            not dict_ref
            and not f.options
            and fd_type in ("TEXT_FIELD", "SELECT")
            and f.default_value_type is None
            and not f.read_only
        ):
            for pattern, name in FD_LABEL_DICTIONARIES:
                if pattern.search(label):
                    if fd_type != "SELECT":
                        self.notes.append(f"Pole „{label}”: zamieniono na listę rozwijaną ze słownikiem {name}.")
                    return "SELECT", name
        return fd_type, None

    def _validation(self, f: FieldDefinition, fd_type: str, label: str) -> list[str]:
        validation: list[str] = ["REQUIRED"] if f.required else []
        rules = f.validation_rules
        custom = rules.custom_validator if rules else None
        if custom is not None:
            coi_name: str | None = None
            if custom == CustomValidator.POSTAL_CODE:
                coi_name = CustomValidator.ZIP_CODE.value
            elif custom in COI_VALIDATORS:
                coi_name = custom.value
            if coi_name is None:
                self.notes.append(
                    f"Pole „{label}”: walidator {custom.value} nie występuje w katalogu COI — pominięto."
                )
            elif fd_type != "TEXT_FIELD":
                self.notes.append(
                    f"Pole „{label}” ({fd_type}): walidator {coi_name} jest dostępny tylko dla pola "
                    "tekstowego — pominięto."
                )
            else:
                validation.append(coi_name)
        if rules and (rules.pattern or rules.min_length or rules.max_length):
            self._dropped_rules.append(label)
        if fd_type == "TEXT_FIELD":
            for pattern, name in FD_LABEL_VALIDATORS:
                if pattern.search(label) and name not in validation:
                    validation.append(name)
        return validation

    def _default_value(self, f: FieldDefinition, fd_type: str, choices: list[DictionaryItem]) -> str | list[str]:
        value = f.initial_value
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            return [self._label_for(item, choices) for item in value]
        if isinstance(value, bool):
            return "true" if value else "false"
        if fd_type in FD_CHOICE_TYPES:
            return self._label_for(value, choices)
        return str(value)

    def _register(self, f: FieldDefinition, ctx: _Context, out: FdField, choices: list[DictionaryItem]) -> None:
        self._refs[f.field_id] = _FieldRef(
            tab_title=ctx.tab_title,
            section_tech=ctx.section_tech,
            tech_name=out.tech_name or "",
            fd_type=out.type or "",
            choices=choices,
            out=out,
            in_repeating=ctx.in_repeating,
        )

    def _presentation_field(self, f: FieldDefinition, ctx: _Context, fd_type: str) -> FdField:
        label = f.label or f.name or ""
        if f.required:
            self.notes.append(
                f"Pole „{label}” ({fd_type}): pole prezentacyjne nie ma atrybutu obligatoryjności — "
                "pominięto required."
            )
        text = f.help_text or (str(f.initial_value) if f.initial_value not in (None, "") else "") or label
        title = label if (f.help_text or f.initial_value not in (None, "")) else ""
        return FdField(
            id=self.uid(f"field:{f.field_id}"),
            type=fd_type,
            tech_name=self.unique_tech(f.name or f.field_id or label),
            title=title,
            description=text,
            states=[],
            question_order=self.next_order(),
        )

    def _consent_field(self, f: FieldDefinition, label: str) -> FdField:
        consents = [FdConsent(description=c.description, required=c.required) for c in f.consents or []]
        if not consents:
            consents = [FdConsent(description=label, required=f.required)]
            self.notes.append(
                f"Sekcja zgód „{label}”: brak listy consents — użyto etykiety pola jako jedynej zgody."
            )
        required = f.required or any(c.required for c in consents)
        return FdField(
            id=self.uid(f"field:{f.field_id}"),
            type="CONSENT_SECTION",
            tech_name=self.unique_tech(f.name or f.field_id or label),
            title=label,
            description=f.help_text or "",
            validation=["REQUIRED"] if required else [],
            states=[],
            question_order=self.next_order(),
            select_all_enabled=len(consents) > 1,
            consent_section_config=FdConsentSectionConfig(consents=consents, required=required, files=[]),
        )

    def _apply_initial_data(
        self, f: FieldDefinition, ctx: _Context, out: FdField, label: str
    ) -> None:
        if f.default_value_type is not None:
            source = f.default_value_type.value
            key = (f.default_value_key or "").strip()
            if not key:
                self.notes.append(
                    f"Pole „{label}”: źródło danych inicjalnych {source} bez atrybutu "
                    "(default_value_key) — pominięto dane inicjalne."
                )
                return
            if key not in INITIAL_DATA_ATTRIBUTES.get(source, ()):
                self.notes.append(
                    f"Pole „{label}”: źródło {source} nie ma atrybutu „{key}” (materiał Dane inicjalne)."
                )
            out.read_only = True
            out.default_value = f"{source}.{key}"
            out.default_value_type = EXTERNAL_SYSTEM
            return
        own_data = FD_OWN_DATA_SECTION.search(ctx.section_title) or FD_OWN_DATA_SECTION.search(ctx.tab_title)
        if own_data and ctx.tab_index == 0 and not FD_OWN_DATA_EXCLUDED.search(ctx.section_title):
            for pattern, value in FD_OWN_DATA_FIELDS:
                if pattern.search(label):
                    out.read_only = True
                    out.default_value = value
                    out.default_value_type = EXTERNAL_SYSTEM
                    self.notes.append(
                        f"Pole „{label}”: dane inicjalne {value} (sekcja „Twoje dane”, tylko do odczytu)."
                    )
                    return

    def convert_field(self, f: FieldDefinition, ctx: _Context, y: int) -> FdQuestion | None:
        label = f.label or f.name or "Pole"
        if f.field_type == FieldType.REPEATABLE_GROUP:
            self.notes.append(
                f"Grupa „{label}” zagnieżdżona w innej grupie — pominięto (reguła 6: bez zagnieżdżania sekcji)."
            )
            return None
        fd_type = FD_TYPE_MAP.get(f.field_type, "TEXT_FIELD")

        if fd_type in FD_PRESENTATION_TYPES:
            out = self._presentation_field(f, ctx, fd_type)
            self._register(f, ctx, out, [])
            return FdQuestion(x=0, y=y, rows=1, cols=1, field=out)
        if fd_type == "CONSENT_SECTION":
            out = self._consent_field(f, label)
            self._register(f, ctx, out, [])
            return FdQuestion(x=0, y=y, rows=1, cols=1, field=out)

        fd_type, dictionary_name = self._resolve_type_and_dictionary(f, fd_type, label)
        validation = self._validation(f, fd_type, label)

        options: list[FdOption] | None = None
        if dictionary_name:
            options = []
            backend_id = FD_DICTIONARIES[dictionary_name][0]
            choices = list(f.options or []) or list(self.known_dictionaries.get(backend_id, []))
        elif fd_type in FD_CHOICE_TYPES:
            choices = self._choice_items(f)
            dict_ref = f.dictionary_ref
            if not f.options and dict_ref:
                mapped = {m.dictionary_id: m.dictionary_name for m in self.form.dictionaries.mapped_dictionaries}
                if not choices:
                    self.notes.append(
                        f"Pole „{label}”: słownik {dict_ref} nie należy do słowników COI i nie ma pozycji."
                    )
                else:
                    self.notes.append(
                        f"Pole „{label}”: słownik spoza katalogu COI ({mapped.get(dict_ref, dict_ref)}) "
                        "wstawiono jako opcje pola."
                    )
            used_option_tech: set[str] = set()
            options = []
            for item in choices:
                tech = slugify(item.code, "opcja")
                base, counter = tech, 2
                while tech in used_option_tech:
                    tech = f"{base}_{counter}"
                    counter += 1
                used_option_tech.add(tech)
                options.append(
                    FdOption(id=self.uid(f"opt:{f.field_id}:{item.code}"), name=item.label, tech_name=tech)
                )
        else:
            choices = []

        out = FdField(
            id=self.uid(f"field:{f.field_id}"),
            type=fd_type,
            tech_name=self.unique_tech(f.name or f.field_id or label),
            title=label,
            description=f.help_text or "",
            options=options,
            validation=validation,
            dictionary_name=dictionary_name,
            read_only=bool(f.read_only),
            default_value=self._default_value(f, fd_type, choices),
            default_value_type="",
            states=[],
            question_order=self.next_order(),
            tooltip_message=(
                "Np. " + re.sub(r"^np\.\s*", "", str(f.placeholder), flags=re.IGNORECASE) if f.placeholder else None
            ),
        )
        if fd_type == "SELECT" and (dictionary_name or len(options or []) > SEARCH_THRESHOLD):
            out.search_enabled = True
        if not out.default_value:
            if dictionary_name == "Kraje":
                out.default_value = "Polska"
            elif dictionary_name == "Numery kierunkowe" and fd_type == "SELECT":
                out.default_value = "+48 Polska"
        self._apply_initial_data(f, ctx, out, label)
        self._register(f, ctx, out, choices)
        return FdQuestion(x=0, y=y, rows=1, cols=1, field=out)

    # --- sekcje i strony ---------------------------------------------------------------------

    def make_section(self, fd_type: str, title: str, tech_base: Any, description: str | None, y: int) -> FdQuestion:
        tech = self.unique_tech(tech_base)
        return FdQuestion(
            x=0,
            y=y,
            rows=1,
            cols=1,
            field=FdField(
                id=self.uid(f"section:{tech}"),
                type=fd_type,
                tech_name=tech,
                title=title,
                description=description or "",
                states=[],
                question_order=self.next_order(),
                repeting_section_grid=[],
            ),
        )

    def _info_question(self, fd_type: str, key: str, tech_base: str, text: str, y: int) -> FdQuestion:
        return FdQuestion(
            x=0,
            y=y,
            rows=1,
            cols=1,
            field=FdField(
                id=self.uid(key),
                type=fd_type,
                tech_name=self.unique_tech(tech_base),
                title="",
                description=text,
                states=[],
                question_order=self.next_order(),
            ),
        )

    @staticmethod
    def _grid(question: FdQuestion) -> list[FdQuestion]:
        assert question.field is not None and question.field.repeting_section_grid is not None
        return question.field.repeting_section_grid

    def _main_section(self, tab: FdTab, section: SectionDefinition, tab_index: int) -> None:
        fields = section.fields
        consent_like = bool(fields) and all(
            f.field_type == FieldType.CHECKBOX
            and not f.options
            and not f.dictionary_ref
            and FD_CONSENT_LABEL.search(f.label or "")
            for f in fields
        )
        plain = [f for f in fields if f.field_type != FieldType.REPEATABLE_GROUP]
        groups = [f for f in fields if f.field_type == FieldType.REPEATABLE_GROUP]
        tab_title = tab.title or ""

        if plain or not groups:
            question = self.make_section(
                "SECTION", section.title, _section_tech_base(section), section.description,
                len(tab.questions),
            )
            assert question.field is not None
            grid = self._grid(question)
            ctx = _Context(tab_title, question.field.tech_name or "", section.title, tab_index)
            self._section_refs[section.section_id] = question.field
            if consent_like:
                required = any(f.required for f in fields)
                consents = [FdConsent(description=f.label, required=f.required) for f in fields]
                grid.append(
                    FdQuestion(
                        x=0, y=0, rows=1, cols=1,
                        field=FdField(
                            id=self.uid(f"consent:{section.section_id}"),
                            type="CONSENT_SECTION",
                            tech_name=self.unique_tech(f"{section.section_id}_zgody"),
                            title=section.title,
                            validation=["REQUIRED"] if required else [],
                            select_all_enabled=len(fields) > 1,
                            question_order=self.next_order(),
                            states=[],
                            consent_section_config=FdConsentSectionConfig(
                                consents=consents, required=required, files=[]
                            ),
                        ),
                    )
                )
                self.notes.append(
                    f"Sekcja „{section.title}”: oświadczenia z checkboxów zamieniono na sekcję zgód "
                    "CONSENT_SECTION (Standardy: zgody nie jako checkbox)."
                )
            else:
                for f in plain:
                    converted = self.convert_field(f, ctx, len(grid))
                    if converted is not None:
                        grid.append(converted)
            if not grid:
                grid.append(
                    self._info_question(
                        "TEXT_BLOCK", f"info:{section.section_id}", f"{section.section_id}_opis",
                        section.description or section.title, 0,
                    )
                )
            tab.questions.append(question)

        for group in groups:
            question = self.make_section(
                "REPEATING_SECTION", group.label or section.title, group.name or group.field_id,
                group.help_text, len(tab.questions),
            )
            assert question.field is not None
            grid = self._grid(question)
            ctx = _Context(
                tab_title, question.field.tech_name or "", section.title, tab_index, in_repeating=True
            )
            for sub in group.fields or []:
                converted = self.convert_field(sub, ctx, len(grid))
                if converted is not None:
                    grid.append(converted)
            if not grid:
                self.notes.append(
                    f"Sekcja powtarzająca się „{question.field.title}” nie ma kolumn — "
                    "backend nie zwrócił pól tabeli."
                )
            self._section_refs[group.field_id] = question.field
            tab.questions.append(question)
            self.notes.append(
                f"Grupa „{question.field.title}” → REPEATING_SECTION na poziomie strony "
                "(reguła 6: bez zagnieżdżania sekcji)."
            )

    def _finish_section(self, tab: FdTab, section: SectionDefinition, tab_index: int) -> None:
        question = self.make_section(
            "SECTION", section.title, _section_tech_base(section), section.description,
            len(tab.questions),
        )
        assert question.field is not None
        grid = self._grid(question)
        ctx = _Context(tab.title or "", question.field.tech_name or "", section.title, tab_index, finish=True)
        self._section_refs[section.section_id] = question.field
        for f in section.fields:
            if f.field_type in (FieldType.INFO_SECTION, FieldType.TEXT_BLOCK):
                out = self._presentation_field(f, ctx, "INFO_SECTION")
                self._register(f, ctx, out, [])
                grid.append(FdQuestion(x=0, y=len(grid), rows=1, cols=1, field=out))
            else:
                self.notes.append(
                    f"Strona końcowa „{tab.title}”: pole „{f.label}” ({f.field_type.value}) pominięto — "
                    "dozwolone wyłącznie INFO_SECTION (reguła 7)."
                )
        if not grid:
            grid.append(
                self._info_question(
                    "INFO_SECTION", f"info:{section.section_id}", f"{section.section_id}_info",
                    section.description or section.title, 0,
                )
            )
        tab.questions.append(question)

    def _default_finish_questions(self, tab: FdTab) -> None:
        for tech_base, title, text in DEFAULT_FINISH_SECTIONS:
            question = self.make_section("SECTION", title, tech_base, None, len(tab.questions))
            assert question.field is not None
            self._grid(question).append(
                self._info_question("INFO_SECTION", f"info:{tech_base}", f"{tech_base}_info", text, 0)
            )
            tab.questions.append(question)

    def _page_tab(self, page: PageDefinition, tab_index: int, finish: bool) -> FdTab:
        tab = FdTab(
            id=self.uid(f"tab:{page.page_id}"),
            title=page.title or f"Strona {tab_index + 1}",
            type="FINISH" if finish else "MAIN",
            questions=[],
        )
        if page.visibility_rules:
            self.notes.append(
                f"Strona „{tab.title}”: warunki widoczności stron nie mają odpowiednika w FormDefinition "
                "(stany są tylko na polach i sekcjach) — pominięto."
            )
        for section in sorted(page.sections, key=lambda s: s.order):
            if finish:
                self._finish_section(tab, section, tab_index)
            else:
                self._main_section(tab, section, tab_index)
        if finish and not tab.questions:
            self._default_finish_questions(tab)
        return tab

    # --- zależności warunkowe --------------------------------------------------------------

    def _condition_query(self, flow: ConditionalFlow, source: _FieldRef) -> str | None:
        path = source.answer_path

        def option_name(code: Any) -> str:
            return self._label_for(code, source.choices)

        values = flow.value if isinstance(flow.value, (list, tuple)) else [flow.value]
        match flow.operator:
            case ConditionOperator.EQUALS:
                if source.fd_type == "CHECKBOX" and not source.choices:
                    truthy = flow.value is True or str(flow.value).strip().lower() == "true"
                    return f"{path} = {'true' if truthy else 'false'}"
                return f"{_jsonata_value(option_name(flow.value))} in {path}"
            case ConditionOperator.NOT_EQUALS:
                return f"$not({_jsonata_value(option_name(flow.value))} in {path})"
            case ConditionOperator.IN:
                parts = [f"{_jsonata_value(option_name(v))} in {path}" for v in values]
                return "(" + " or ".join(parts) + ")" if parts else None
            case ConditionOperator.IS_EMPTY:
                return f'($not($exists({path})) or {path} = "")'
            case ConditionOperator.GREATER_THAN:
                number = _format_number(flow.value)
                return f"$number({path}) > {number}" if number is not None else None
        return None

    def _all_flows(self) -> list[ConditionalFlow]:
        flows: list[ConditionalFlow] = list(self.form.conditional_flows)

        def collect(fields: list[FieldDefinition]) -> None:
            for f in fields:
                flows.extend(f.dependencies)
                if f.fields:
                    collect(f.fields)

        for page in self.form.pages:
            for section in page.sections:
                flows.extend(section.visibility_rules)
                collect(section.fields)
        return flows

    def apply_states(self) -> None:
        seen: set[str] = set()
        for flow in self._all_flows():
            key = flow.model_dump_json()
            if key in seen:
                continue
            seen.add(key)
            source = self._refs.get(flow.source_field_id)
            target: FdField | None = None
            if flow.target_type == TargetType.SECTION:
                target = self._section_refs.get(flow.target_id)
            elif flow.target_type == TargetType.FIELD:
                ref = self._refs.get(flow.target_id)
                target = ref.out if ref else self._section_refs.get(flow.target_id)
            if source is None or target is None:
                self.notes.append(
                    f"Zależność {flow.source_field_id} → {flow.target_id}: brak pola/sekcji lub cel typu "
                    "strona — pominięto."
                )
                continue
            if flow.action not in (FlowAction.SHOW, FlowAction.HIDE):
                self.notes.append(
                    f"Zależność {flow.action.value} ({flow.source_field_id} → {flow.target_id}) nie ma "
                    "odpowiednika w COI (dostępne stany: SHOW, READ_ONLY) — pominięto."
                )
                continue
            query = self._condition_query(flow, source)
            if query is None:
                self.notes.append(
                    f"Zależność {flow.source_field_id} → {flow.target_id}: nie da się zbudować warunku "
                    f"jsonata dla operatora {flow.operator.value} i wartości {flow.value!r} — pominięto."
                )
                continue
            if flow.action == FlowAction.HIDE:
                query = f"$not({query})"
            if target.states is None:
                target.states = []
            target.states.append(FdState(type="SHOW", query=query))

    # --- całość -------------------------------------------------------------------------------

    def run(self) -> FormDefinition:
        raw_title = self.form.metadata.title or ""
        title = (raw_title or "Formularz")[:TITLE_MAX_LENGTH]
        if len(raw_title) > TITLE_MAX_LENGTH:
            self.notes.append(f"Tytuł przycięto do {TITLE_MAX_LENGTH} znaków (reguła 1).")

        pages = sorted(self.form.pages, key=lambda p: p.order)
        main_pages = [p for p in pages if p.page_type != PageType.FINISH]
        finish_pages = [p for p in pages if p.page_type == PageType.FINISH]
        if finish_pages and pages[-1].page_type != PageType.FINISH:
            self.notes.append("Strona FINISH nie była ostatnia — przeniesiono ją na koniec przepływu.")
        if len(finish_pages) > 1:
            self.notes.append(
                f"Formularz ma {len(finish_pages)} strony FINISH — reguła 2b wymaga dokładnie jednej."
            )

        tabs: list[FdTab] = []
        for page in main_pages:
            tabs.append(self._page_tab(page, len(tabs), finish=False))
        for page in finish_pages:
            tabs.append(self._page_tab(page, len(tabs), finish=True))
        if not finish_pages:
            finish = FdTab(id=self.uid("tab:finish"), title="Podsumowanie", type="FINISH", questions=[])
            self._default_finish_questions(finish)
            tabs.append(finish)
            self.notes.append("Dodano domyślną stronę końcową FINISH z sekcjami informacyjnymi (reguła 2b, 7).")

        self.apply_states()

        if self._dropped_rules:
            self.notes.append(
                f"Reguły długości/regex ({', '.join(self._dropped_rules)}) nie mają odpowiednika "
                "w FormDefinition — pominięto."
            )
        links = [
            FdLink(id=self.uid(f"link:{index}"), source=tabs[index].id, target=tabs[index + 1].id)
            for index in range(len(tabs) - 1)
        ]
        return FormDefinition(title=title, tabs=tabs, flow=FdFlow(links=links))


def export_form_definition(
    form: FormDocumentSchema,
    known_dictionaries: dict[str, list[DictionaryItem]] | None = None,
) -> tuple[FormDefinition, list[str]]:
    """Konwertuje wewnętrzny JSON formularza do FormDefinition COI.

    ``known_dictionaries``: pozycje słowników z lokalnej bazy (``dictionary_id -> items``) —
    potrzebne do wstawienia słowników spoza katalogu COI jako ``options`` oraz do tłumaczenia kodów
    na etykiety w warunkach jsonata. Zwraca ``(form_definition, notes)``, gdzie ``notes`` opisują
    mapowania heurystyczne i elementy, których nie da się wyrazić w FormDefinition.
    """
    exporter = _Exporter(form=form, known_dictionaries=dict(known_dictionaries or {}))
    definition = exporter.run()
    return definition, exporter.notes


def used_dictionary_ids(form: FormDocumentSchema) -> set[str]:
    """Identyfikatory słowników (``dictionary_ref``) użyte w formularzu, łącznie z kolumnami grup."""
    found: set[str] = set()

    def collect(fields: list[FieldDefinition]) -> None:
        for f in fields:
            if f.dictionary_ref:
                found.add(f.dictionary_ref)
            if f.fields:
                collect(f.fields)

    for page in form.pages:
        for section in page.sections:
            collect(section.fields)
    return found
