"""Niezmienny kontrakt danych (Pydantic v2) między branchemi.

Główny wyjściowy JSON formularza COI: strony tematyczne -> sekcje -> pola,
walidacje, zależności warunkowe oraz słowniki (zmapowane i proponowane).
Format przystosowany do renderowania przez wizualizator e-Usługi (Branch 2)
i do dalszego przetwarzania przez agentów AI (ai_agent_ready).

UWAGA: pliki w schemas/ oraz interfaces/ są WSPÓLNE - zmiany wymagają uzgodnienia
obu branchy (patrz docs/ARCHITECTURE.md).
"""

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    CONSENT_SECTION = "consent_section"
    TEXT_BLOCK = "text_block"
    INFO_SECTION = "info_section"


class DataType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"


class CustomValidator(str, Enum):
    """Katalog walidatorów COI (Reguły techniczne). PL_PHONE nie występuje w katalogu."""

    PESEL = "PESEL"
    NIP = "NIP"
    REGON = "REGON"
    ZIP_CODE = "ZIP_CODE"
    EMAIL = "EMAIL"
    EPUAP = "EPUAP"
    ADE = "ADE"
    SERIAL_NUMBER_ID_CARD = "SERIAL_NUMBER_ID_CARD"


class ConditionOperator(str, Enum):
    EQUALS = "EQUALS"
    NOT_EQUALS = "NOT_EQUALS"
    IN = "IN"
    IS_EMPTY = "IS_EMPTY"
    GREATER_THAN = "GREATER_THAN"
    # Złożone: value = lista podwarunków {"field_id", "operator", "value"} (koniunkcja/alternatywa).
    ALL = "ALL"
    ANY = "ANY"


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
    READ_ONLY = "READ_ONLY"


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DictionaryItem(_ContractModel):
    code: str
    label: str


class ConsentItem(_ContractModel):
    """Pojedyncza zgoda/oświadczenie w sekcji CONSENT_SECTION (Standardy: nie checkbox)."""

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
    fields: list["FieldDefinition"] | None = None  # dla repeatable_group
    read_only: bool = False
    default_value_type: Literal["", "EXTERNAL_SYSTEM"] = ""
    consents: list[ConsentItem] | None = None  # dla consent_section

    @model_validator(mode="after")
    def _check_initial_data(self) -> "FieldDefinition":
        # Dane inicjalne (PESEL/PESEL_ADDRESS/MLPZ). Tylko do odczytu bywają dane tożsamości
        # („Twoje dane”), ale Standardy („Dane do kontaktu”) zasilają z MLPZ także pola edytowalne
        # (telefon, e-mail: „Możesz je zmienić lub usunąć”), więc read_only nie jest tu wymuszane.
        if self.default_value_type == "EXTERNAL_SYSTEM" and (
            not isinstance(self.initial_value, str)
            or not re.fullmatch(r"(?:PESEL|PESEL_ADDRESS|MLPZ)\.[A-Za-z]+", self.initial_value)
        ):
            raise ValueError(
                "initial_value dla EXTERNAL_SYSTEM musi mieć format ŹRÓDŁO.atrybut (PESEL/PESEL_ADDRESS/MLPZ)."
            )
        return self


class SectionDefinition(_ContractModel):
    section_id: str
    title: str
    order: int = Field(ge=1)
    description: str | None = None
    visibility_rules: list[ConditionalFlow] = Field(default_factory=list)
    fields: list[FieldDefinition] = Field(default_factory=list)


class PageDefinition(_ContractModel):
    page_id: str
    title: str
    order: int = Field(ge=1)
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
    # Adresat wniosku odczytany deterministycznie z nagłówka (np. „Wójt Gminy Elbląg”); None = nieznany.
    office_name: str | None = None


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
