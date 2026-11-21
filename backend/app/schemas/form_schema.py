"""Niezmienny kontrakt danych (Pydantic v2) między branchemi.

Główny wyjściowy JSON formularza COI: strony tematyczne -> sekcje -> pola,
walidacje, zależności warunkowe oraz słowniki (zmapowane i proponowane).
Format przystosowany do renderowania przez wizualizator e-Usługi (Branch 2)
i do dalszego przetwarzania przez agentów AI (ai_agent_ready).

UWAGA: pliki w schemas/ oraz interfaces/ są WSPÓLNE - zmiany wymagają uzgodnienia
obu branchy (patrz docs/ARCHITECTURE.md).
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FieldType(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    SELECT = "select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    TEXTAREA = "textarea"
    FILE = "file"
    REPEATABLE_GROUP = "repeatable_group"
    # Komponenty COI bez odpowiednika w starszym formacie (Reguły techniczne):
    CONSENT_SECTION = "consent_section"  # sekcja zgód: lista `consents`, nie checkboxy
    TEXT_BLOCK = "text_block"  # statyczny tekst (instrukcja, pouczenie) - bez wprowadzania danych
    INFO_SECTION = "info_section"  # sekcja informacyjna - jedyny typ dozwolony na stronie FINISH


# Pola prezentacyjne: nie zbierają danych, nie mają obligatoryjności ani walidatorów.
PRESENTATION_FIELD_TYPES = frozenset({FieldType.TEXT_BLOCK, FieldType.INFO_SECTION})
# Pola, do których wolno podpiąć słownik (materiał "Słowniki").
CHOICE_FIELD_TYPES = frozenset({FieldType.SELECT, FieldType.RADIO, FieldType.CHECKBOX})


class DataType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"


class CustomValidator(str, Enum):
    """Walidatory z katalogu COI (Reguły techniczne) + dwa historyczne aliasy.

    REQUIRED nie jest tu ujęty - wymagalność opisuje `FieldDefinition.required`.
    """

    EMAIL = "EMAIL"
    PESEL = "PESEL"
    NIP = "NIP"
    REGON = "REGON"
    ZIP_CODE = "ZIP_CODE"
    EPUAP = "EPUAP"  # adres skrytki ePUAP, np. /nazwa_urzedu/SkrytkaESP
    ADE = "ADE"  # adres do e-Doręczeń, np. AE:PL-12345-67890-ABCDE-12
    SERIAL_NUMBER_ID_CARD = "SERIAL_NUMBER_ID_CARD"  # seria i numer dowodu osobistego
    # Historyczne (spoza katalogu COI) - akceptowane dla zgodności wstecz:
    POSTAL_CODE = "POSTAL_CODE"  # = ZIP_CODE; eksport FormDefinition zapisuje ZIP_CODE
    PL_PHONE = "PL_PHONE"  # brak w katalogu COI; eksport pomija (telefon = słownik numerów kierunkowych)


# Walidatory, które istnieją w katalogu COI (eksportowalne do FormDefinition.validation).
COI_VALIDATORS = frozenset(
    {
        CustomValidator.EMAIL,
        CustomValidator.PESEL,
        CustomValidator.NIP,
        CustomValidator.REGON,
        CustomValidator.ZIP_CODE,
        CustomValidator.EPUAP,
        CustomValidator.ADE,
        CustomValidator.SERIAL_NUMBER_ID_CARD,
    }
)


class InitialDataSource(str, Enum):
    """Źródła danych inicjalnych (materiał "Dane inicjalne"); w FormDefinition: EXTERNAL_SYSTEM."""

    PESEL = "PESEL"  # rejestr PESEL
    PESEL_ADDRESS = "PESEL_ADDRESS"  # dane zameldowania z rejestru PESEL
    MLPZ = "MLPZ"  # dane zalogowanego użytkownika (Mój Login / profil zaufany)


class PageType(str, Enum):
    MAIN = "MAIN"
    FINISH = "FINISH"  # strona końcowa: wyłącznie pola info_section


class ConditionOperator(str, Enum):
    EQUALS = "EQUALS"
    NOT_EQUALS = "NOT_EQUALS"
    IN = "IN"
    IS_EMPTY = "IS_EMPTY"
    GREATER_THAN = "GREATER_THAN"


class TargetType(str, Enum):
    FIELD = "FIELD"
    SECTION = "SECTION"
    PAGE = "PAGE"


class FlowAction(str, Enum):
    SHOW = "SHOW"
    HIDE = "HIDE"
    REQUIRE = "REQUIRE"
    OPTIONAL = "OPTIONAL"
    SET_VALUE = "SET_VALUE"


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DictionaryItem(_ContractModel):
    code: str
    label: str


class ConsentItem(_ContractModel):
    """Pojedyncza zgoda/oświadczenie w polu consent_section."""

    description: str
    required: bool = False


class ValidationRules(_ContractModel):
    data_type: DataType | None = None
    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=0)
    min_value: float | None = None
    max_value: float | None = None
    pattern: str | None = None
    custom_validator: CustomValidator | None = None
    error_message: str | None = None


class ConditionalFlow(_ContractModel):
    source_field_id: str
    operator: ConditionOperator
    value: Any = None
    target_type: TargetType
    target_id: str
    action: FlowAction
    action_value: Any = None


class FieldDefinition(_ContractModel):
    field_id: str
    name: str
    label: str
    field_type: FieldType
    required: bool = False
    placeholder: str | None = None
    help_text: str | None = None
    initial_value: Any = None
    dictionary_ref: str | None = None
    options: list[DictionaryItem] | None = None
    validation_rules: ValidationRules | None = None
    dependencies: list[ConditionalFlow] = Field(default_factory=list)
    fields: list["FieldDefinition"] | None = None  # dla repeatable_group (kolumny tabeli)
    # checkbox z `options`/`dictionary_ref` = wielokrotny wybór (initial_value: lista kodów);
    # checkbox bez opcji = pojedyncze pole tak/nie.
    read_only: bool = False
    # Dane inicjalne: źródło + atrybut, np. MLPZ + "name" -> defaultValue "MLPZ.name".
    default_value_type: InitialDataSource | None = None
    default_value_key: str | None = None
    consents: list[ConsentItem] | None = None  # dla consent_section


class SectionDefinition(_ContractModel):
    section_id: str
    title: str
    order: int = Field(ge=1)
    description: str | None = None
    # Identyfikator sekcji predefiniowanej ze Standardów (np. "STD_APPLICANT_DATA"), gdy sekcję
    # wstawiła deterministycznie logika domenowa, a nie LLM.
    predefined: str | None = None
    visibility_rules: list[ConditionalFlow] = Field(default_factory=list)
    fields: list[FieldDefinition] = Field(default_factory=list)


class PageDefinition(_ContractModel):
    page_id: str
    title: str
    order: int = Field(ge=1)
    page_type: PageType = PageType.MAIN
    description: str | None = None
    visibility_rules: list[ConditionalFlow] = Field(default_factory=list)
    sections: list[SectionDefinition] = Field(default_factory=list)


class FormMetadata(_ContractModel):
    form_id: str
    title: str
    version: str = "1.0.0"
    source_filename: str
    source_format: str
    generated_at: datetime
    ai_agent_ready: bool = True


class MappedDictionary(_ContractModel):
    field_id: str
    dictionary_id: str
    dictionary_name: str
    confidence: float = Field(ge=0.0, le=1.0)


class ProposedDictionary(_ContractModel):
    proposed_id: str
    name: str
    target_field_id: str
    reason: str
    items: list[DictionaryItem] = Field(default_factory=list)


class DictionariesBlock(_ContractModel):
    mapped_dictionaries: list[MappedDictionary] = Field(default_factory=list)
    proposed_dictionaries: list[ProposedDictionary] = Field(default_factory=list)


class FormDocumentSchema(_ContractModel):
    metadata: FormMetadata
    pages: list[PageDefinition] = Field(min_length=1)
    dictionaries: DictionariesBlock = Field(default_factory=DictionariesBlock)
    conditional_flows: list[ConditionalFlow] = Field(default_factory=list)


FieldDefinition.model_rebuild()
