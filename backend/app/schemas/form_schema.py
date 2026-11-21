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


class DataType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"


class CustomValidator(str, Enum):
    PESEL = "PESEL"
    PL_PHONE = "PL_PHONE"
    NIP = "NIP"
    REGON = "REGON"
    POSTAL_CODE = "POSTAL_CODE"


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
