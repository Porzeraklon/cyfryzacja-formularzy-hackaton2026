"""Oficjalny format importu e-Usług COI: FormDefinition (Pydantic v2).

Źródło prawdy: materiały konkursowe "importowanyPlikJsonSchemat.json" + "OpisImportowanegoPlikuJSON"
oraz "Reguły techniczne konfiguracji formularzy i pól" (typy komponentów, walidatory, stany jsonata,
`searchEnabled`, `selectAllEnabled`). Struktura:
``FormDefinition{title, tabs[{id, title, type, questions[{x, y, rows, cols, field}]}], flow{links[]}}``.

Wszystkie klucze są opcjonalne, jak w oficjalnym schemacie (brak tam "required"); obligatoryjność
wynikającą z reguł 1–12 sprawdza ``services/form_definition_validation.py``. Serializacja pomija
klucze o wartości ``None`` (oficjalny przykład pomija np. ``options`` w polach tekstowych).
Klucz sekcji ma pisownię ze schematu: ``repetingSectionGrid``; przy wczytywaniu akceptujemy też
``repeatingSectionGrid`` (pisownia z przykładów jsonaty w Regułach technicznych).
"""

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_serializer
from pydantic.alias_generators import to_camel

# ---------------------------------------------------------------------------------------------
# Katalogi (Reguły techniczne, materiał "Słowniki")
# ---------------------------------------------------------------------------------------------

FdTabType = Literal["MAIN", "FINISH"]
FdFieldType = Literal[
    "SECTION",
    "REPEATING_SECTION",
    "TEXT_FIELD",
    "CHECKBOX",
    "NUMBER",
    "CONSENT_SECTION",
    "SELECT",
    "TEXTAREA",
    "FILE_UPLOADER",
    "RADIO",
    "DATEPICKER",
    "TEXT_BLOCK",
    "INFO_SECTION",
]
FdStateType = Literal["SHOW", "READ_ONLY"]
FdDefaultValueType = Literal["", "EXTERNAL_SYSTEM"]

FD_TAB_TYPES: tuple[str, ...] = ("MAIN", "FINISH")
FD_SECTION_TYPES: frozenset[str] = frozenset({"SECTION", "REPEATING_SECTION"})
FD_FIELD_TYPES: frozenset[str] = frozenset(
    {
        "TEXT_FIELD",
        "CHECKBOX",
        "NUMBER",
        "CONSENT_SECTION",
        "SELECT",
        "TEXTAREA",
        "FILE_UPLOADER",
        "RADIO",
        "DATEPICKER",
        "TEXT_BLOCK",
        "INFO_SECTION",
    }
)
# Komponenty prezentacyjne: bez wprowadzania danych (bez atrybutu obligatoryjności).
FD_PRESENTATION_TYPES: frozenset[str] = frozenset({"TEXT_BLOCK", "INFO_SECTION"})
# Komponenty, do których wolno podpiąć słownik lub opcje.
FD_CHOICE_TYPES: frozenset[str] = frozenset({"SELECT", "RADIO", "CHECKBOX"})
FD_STATE_TYPES_FIELD: tuple[str, ...] = ("SHOW", "READ_ONLY")
FD_STATE_TYPES_SECTION: tuple[str, ...] = ("SHOW",)

# Katalog walidatorów (Reguły techniczne): nazwy predefiniowanych walidacji w `validation`.
FD_VALIDATORS: tuple[str, ...] = (
    "REQUIRED",
    "EMAIL",
    "PESEL",
    "NIP",
    "REGON",
    "ZIP_CODE",
    "EPUAP",
    "ADE",
    "SERIAL_NUMBER_ID_CARD",
)
# Typ pola -> walidatory, które da się na nim ustawić (specyfikacja komponentów).
FD_VALIDATORS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "TEXT_FIELD": FD_VALIDATORS,
    "TEXTAREA": ("REQUIRED",),
    "NUMBER": ("REQUIRED",),
    "SELECT": ("REQUIRED",),
    "RADIO": ("REQUIRED",),
    "CHECKBOX": ("REQUIRED",),
    "DATEPICKER": ("REQUIRED",),
    "FILE_UPLOADER": ("REQUIRED",),
    "CONSENT_SECTION": ("REQUIRED",),
}

# Słowniki predefiniowane COI: oficjalna `dictionaryName` -> (id w lokalnej bazie, dozwolone typy pól).
# Województwo/Powiat/Gmina/Miejscowość/Kraje: wyłącznie lista rozwijana (SELECT);
# Numery kierunkowe: SELECT, RADIO lub CHECKBOX.
FD_DICTIONARIES: dict[str, tuple[str, frozenset[str]]] = {
    "Województwo": ("dict_voivodeships", frozenset({"SELECT"})),
    "Powiat": ("dict_districts", frozenset({"SELECT"})),
    "Gmina": ("dict_communities", frozenset({"SELECT"})),
    "Miejscowość": ("dict_places", frozenset({"SELECT"})),
    "Kraje": ("dict_countries", frozenset({"SELECT"})),
    "Numery kierunkowe": ("dict_dial_codes", frozenset({"SELECT", "RADIO", "CHECKBOX"})),
}
# Identyfikator w lokalnej bazie COI -> oficjalna nazwa słownika.
FD_DICTIONARY_BY_BACKEND_ID: dict[str, str] = {
    backend_id: name for name, (backend_id, _types) in FD_DICTIONARIES.items()
}
# Dodatkowe aliasy nazw (znormalizowane: wielkie litery, bez diakrytyków, "_" zamiast spacji).
FD_DICTIONARY_ALIASES: dict[str, str] = {
    "WOJEWODZTWO": "Województwo",
    "WOJEWODZTWA": "Województwo",
    "POWIAT": "Powiat",
    "POWIATY": "Powiat",
    "GMINA": "Gmina",
    "GMINY": "Gmina",
    "MIEJSCOWOSC": "Miejscowość",
    "MIEJSCOWOSCI": "Miejscowość",
    "TERYT_4G_DOWODOWY": "Miejscowość",
    "KRAJE": "Kraje",
    "KRAJ": "Kraje",
    "PANSTWA": "Kraje",
    "NUMERY_KIERUNKOWE": "Numery kierunkowe",
}


# ---------------------------------------------------------------------------------------------
# Modele
# ---------------------------------------------------------------------------------------------


class _FdModel(BaseModel):
    """Baza modeli FormDefinition: klucze camelCase, extra="forbid", serializacja bez ``None``."""

    model_config = ConfigDict(
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    @model_serializer(mode="wrap")
    def _drop_none(self, handler: Any) -> Any:
        data = handler(self)
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if value is not None}
        return data


class FdOption(_FdModel):
    """Opcja wyboru pola SELECT/RADIO/CHECKBOX."""

    id: str | None = None
    name: str | None = None
    tech_name: str | None = None


class FdConsent(_FdModel):
    """Pojedyncza zgoda w sekcji zgód (CONSENT_SECTION)."""

    description: str | None = None
    required: bool | None = None


class FdConsentSectionConfig(_FdModel):
    """Konfiguracja sekcji zgód — dostępna wyłącznie dla typu CONSENT_SECTION."""

    consents: list[FdConsent] | None = None
    required: bool | None = None
    files: list[Any] | None = None


class FdState(_FdModel):
    """Dynamiczny stan pola/sekcji: SHOW lub READ_ONLY + warunek jsonata."""

    type: FdStateType | None = None
    query: str | None = None


class FdField(_FdModel):
    """Ustawienia pola (`tabs.questions.field`) — także sekcji i sekcji powtarzającej się."""

    id: str | None = None
    type: FdFieldType | None = None
    tech_name: str | None = None
    title: str | None = None
    description: str | None = None
    options: list[FdOption] | None = None
    validation: list[str] | None = None
    dictionary_name: str | None = None
    read_only: bool | None = None
    repeting_section_grid: list["FdQuestion"] | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "repetingSectionGrid", "repeatingSectionGrid", "repeting_section_grid"
        ),
        serialization_alias="repetingSectionGrid",
    )
    # SELECT/TEXTAREA/FILE_UPLOADER: Reguły techniczne dopuszczają tablicę ("defaultValue": []).
    default_value: str | list[str] | None = None
    default_value_type: FdDefaultValueType | None = None
    consent_section_config: FdConsentSectionConfig | None = None
    states: list[FdState] | None = None
    question_order: int | float | None = None
    tooltip_message: str | None = None
    # Z Reguł technicznych (brak w schemacie JSON): wyszukiwanie w liście, "Zaznacz wszystkie".
    search_enabled: bool | None = None
    select_all_enabled: bool | None = None


class FdQuestion(_FdModel):
    """Element siatki strony: pozycja (CSS grid-area) + pole."""

    x: int | float | None = None
    y: int | float | None = None
    rows: int | float | None = None
    cols: int | float | None = None
    field: FdField | None = None


class FdTab(_FdModel):
    """Strona/zakładka formularza: MAIN lub FINISH."""

    id: str | None = None
    title: str | None = None
    type: FdTabType | None = None
    questions: list[FdQuestion] = Field(default_factory=list)


class FdLink(_FdModel):
    """Przejście ze strony `source` na stronę `target`."""

    id: str | None = None
    source: str | None = None
    target: str | None = None


class FdFlow(_FdModel):
    links: list[FdLink] = Field(default_factory=list)


class FormDefinition(_FdModel):
    """Definicja formularza w oficjalnym formacie importu e-Usług COI."""

    title: str | None = None
    tabs: list[FdTab] = Field(default_factory=list)
    flow: FdFlow | None = None


FdField.model_rebuild()


class FormDefinitionIssue(BaseModel):
    """Problem wykryty przez walidator strukturalny FormDefinition (reguły 1–12 + Standardy)."""

    model_config = ConfigDict(extra="forbid")

    level: Literal["error", "warn"]
    path: str
    rule: str
    message: str


# Krótszy alias używany w serwisach.
Issue = FormDefinitionIssue
