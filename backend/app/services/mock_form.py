"""Mock pipeline: realistyczny formularz demonstracyjny (USE_MOCK_PIPELINE=true).

Wydzielone z pipeline_service.py, aby treść demo dało się rozwijać niezależnie od orkiestratora.
"""

from datetime import UTC, datetime
from uuid import uuid4

from backend.app.schemas.form_schema import (
    ConditionalFlow,
    ConditionOperator,
    CustomValidator,
    DataType,
    DictionariesBlock,
    DictionaryItem,
    FieldDefinition,
    FieldType,
    FlowAction,
    FormDocumentSchema,
    FormMetadata,
    MappedDictionary,
    PageDefinition,
    ProposedDictionary,
    SectionDefinition,
    TargetType,
    ValidationRules,
)

_CASE_TYPE_ITEMS = [
    DictionaryItem(code="zaswiadczenie", label="Wydanie zaświadczenia"),
    DictionaryItem(code="odpis", label="Wydanie odpisu dokumentu"),
    DictionaryItem(code="zmiana_danych", label="Zmiana danych w ewidencji"),
    DictionaryItem(code="inna", label="Inna sprawa (opisz poniżej)"),
]


def _show_other_case_section() -> ConditionalFlow:
    return ConditionalFlow(
        source_field_id="case_type",
        operator=ConditionOperator.EQUALS,
        value="inna",
        target_type=TargetType.SECTION,
        target_id="sec_case_other",
        action=FlowAction.SHOW,
    )


def _require_other_details() -> ConditionalFlow:
    return ConditionalFlow(
        source_field_id="case_type",
        operator=ConditionOperator.EQUALS,
        value="inna",
        target_type=TargetType.FIELD,
        target_id="other_case_details",
        action=FlowAction.REQUIRE,
    )


def build_mock_form(filename: str, mime_type: str) -> FormDocumentSchema:
    """Wielostronicowy wniosek urzędowy: 3 strony, słowniki, zależności, PESEL/telefon."""
    show_other = _show_other_case_section()
    require_other = _require_other_details()

    page_personal = PageDefinition(
        page_id="page_applicant",
        title="Dane wnioskodawcy",
        order=1,
        description="Dane identyfikacyjne osoby składającej wniosek.",
        sections=[
            SectionDefinition(
                section_id="sec_personal",
                title="Dane osobowe",
                order=1,
                fields=[
                    FieldDefinition(
                        field_id="first_name",
                        name="first_name",
                        label="Imię",
                        field_type=FieldType.TEXT,
                        required=True,
                        placeholder="np. Jan",
                        validation_rules=ValidationRules(
                            data_type=DataType.STRING, min_length=2, max_length=50
                        ),
                    ),
                    FieldDefinition(
                        field_id="last_name",
                        name="last_name",
                        label="Nazwisko",
                        field_type=FieldType.TEXT,
                        required=True,
                        placeholder="np. Kowalski",
                        validation_rules=ValidationRules(
                            data_type=DataType.STRING, min_length=2, max_length=80
                        ),
                    ),
                    FieldDefinition(
                        field_id="pesel",
                        name="pesel",
                        label="Numer PESEL",
                        field_type=FieldType.TEXT,
                        required=True,
                        placeholder="11 cyfr",
                        help_text="Numer PESEL jest weryfikowany sumą kontrolną.",
                        validation_rules=ValidationRules(
                            data_type=DataType.STRING,
                            pattern=r"\d{11}",
                            custom_validator=CustomValidator.PESEL,
                            error_message="Podaj poprawny numer PESEL (11 cyfr, suma kontrolna).",
                        ),
                    ),
                    FieldDefinition(
                        field_id="phone",
                        name="phone",
                        label="Telefon kontaktowy",
                        field_type=FieldType.TEXT,
                        required=False,
                        placeholder="np. +48 600 100 200",
                        validation_rules=ValidationRules(
                            custom_validator=CustomValidator.PL_PHONE,
                            error_message="Podaj poprawny polski numer telefonu (9 cyfr, opcjonalnie +48).",
                        ),
                    ),
                    FieldDefinition(
                        field_id="gender",
                        name="gender",
                        label="Płeć",
                        field_type=FieldType.RADIO,
                        dictionary_ref="dict_gender",
                    ),
                ],
            ),
            SectionDefinition(
                section_id="sec_document",
                title="Dokument tożsamości",
                order=2,
                fields=[
                    FieldDefinition(
                        field_id="document_type",
                        name="document_type",
                        label="Rodzaj dokumentu tożsamości",
                        field_type=FieldType.SELECT,
                        required=True,
                        dictionary_ref="dict_document_types",
                    ),
                    FieldDefinition(
                        field_id="document_number",
                        name="document_number",
                        label="Seria i numer dokumentu",
                        field_type=FieldType.TEXT,
                        required=True,
                        validation_rules=ValidationRules(
                            data_type=DataType.STRING, min_length=5, max_length=20
                        ),
                    ),
                ],
            ),
        ],
    )

    page_case = PageDefinition(
        page_id="page_case",
        title="Adres i szczegóły sprawy",
        order=2,
        description="Adres zamieszkania oraz przedmiot wniosku.",
        sections=[
            SectionDefinition(
                section_id="sec_address",
                title="Adres zamieszkania",
                order=1,
                fields=[
                    FieldDefinition(
                        field_id="street",
                        name="street",
                        label="Ulica",
                        field_type=FieldType.TEXT,
                        required=True,
                        validation_rules=ValidationRules(max_length=100),
                    ),
                    FieldDefinition(
                        field_id="building_number",
                        name="building_number",
                        label="Numer budynku",
                        field_type=FieldType.TEXT,
                        required=True,
                        validation_rules=ValidationRules(max_length=10),
                    ),
                    FieldDefinition(
                        field_id="postal_code",
                        name="postal_code",
                        label="Kod pocztowy",
                        field_type=FieldType.TEXT,
                        required=True,
                        placeholder="00-950",
                        validation_rules=ValidationRules(
                            custom_validator=CustomValidator.POSTAL_CODE
                        ),
                    ),
                    FieldDefinition(
                        field_id="city",
                        name="city",
                        label="Miejscowość",
                        field_type=FieldType.TEXT,
                        required=True,
                        validation_rules=ValidationRules(max_length=80),
                    ),
                    FieldDefinition(
                        field_id="voivodeship",
                        name="voivodeship",
                        label="Województwo",
                        field_type=FieldType.SELECT,
                        required=True,
                        dictionary_ref="dict_voivodeships",
                    ),
                ],
            ),
            SectionDefinition(
                section_id="sec_case",
                title="Przedmiot sprawy",
                order=2,
                fields=[
                    FieldDefinition(
                        field_id="case_type",
                        name="case_type",
                        label="Rodzaj sprawy",
                        field_type=FieldType.SELECT,
                        required=True,
                        dictionary_ref="prop_case_types",
                        options=_CASE_TYPE_ITEMS,
                        help_text="Słownik zaproponowany automatycznie na podstawie treści formularza.",
                        dependencies=[show_other, require_other],
                    ),
                    FieldDefinition(
                        field_id="case_description",
                        name="case_description",
                        label="Opis sprawy",
                        field_type=FieldType.TEXTAREA,
                        placeholder="Krótko opisz, czego dotyczy wniosek.",
                        validation_rules=ValidationRules(max_length=2000),
                    ),
                ],
            ),
            SectionDefinition(
                section_id="sec_case_other",
                title="Szczegóły innej sprawy",
                order=3,
                description="Sekcja widoczna tylko dla rodzaju sprawy 'Inna sprawa'.",
                visibility_rules=[show_other],
                fields=[
                    FieldDefinition(
                        field_id="other_case_details",
                        name="other_case_details",
                        label="Szczegółowy opis innej sprawy",
                        field_type=FieldType.TEXTAREA,
                        validation_rules=ValidationRules(min_length=10, max_length=2000),
                        dependencies=[require_other],
                    ),
                ],
            ),
        ],
    )

    page_declarations = PageDefinition(
        page_id="page_declarations",
        title="Oświadczenia i załączniki",
        order=3,
        description="Wymagane oświadczenia oraz dokumenty dodatkowe.",
        sections=[
            SectionDefinition(
                section_id="sec_attachments",
                title="Załączniki",
                order=1,
                fields=[
                    FieldDefinition(
                        field_id="attachment",
                        name="attachment",
                        label="Skan dokumentu potwierdzającego",
                        field_type=FieldType.FILE,
                        help_text="Dopuszczalne formaty: PDF, JPG, PNG.",
                    ),
                ],
            ),
            SectionDefinition(
                section_id="sec_declarations",
                title="Oświadczenia",
                order=2,
                fields=[
                    FieldDefinition(
                        field_id="rodo_consent",
                        name="rodo_consent",
                        label="Wyrażam zgodę na przetwarzanie danych osobowych (RODO).",
                        field_type=FieldType.CHECKBOX,
                        required=True,
                    ),
                    FieldDefinition(
                        field_id="truth_consent",
                        name="truth_consent",
                        label="Oświadczam, że podane dane są zgodne z prawdą.",
                        field_type=FieldType.CHECKBOX,
                        required=True,
                    ),
                ],
            ),
        ],
    )

    return FormDocumentSchema(
        metadata=FormMetadata(
            form_id=f"form-{uuid4().hex[:12]}",
            title="Wniosek o załatwienie sprawy urzędowej",
            version="1.0.0",
            source_filename=filename,
            source_format=mime_type,
            generated_at=datetime.now(UTC),
            ai_agent_ready=True,
        ),
        pages=[page_personal, page_case, page_declarations],
        dictionaries=DictionariesBlock(
            mapped_dictionaries=[
                MappedDictionary(
                    field_id="voivodeship",
                    dictionary_id="dict_voivodeships",
                    dictionary_name="Województwa",
                    confidence=0.98,
                ),
                MappedDictionary(
                    field_id="document_type",
                    dictionary_id="dict_document_types",
                    dictionary_name="Rodzaje dokumentów tożsamości",
                    confidence=0.95,
                ),
                MappedDictionary(
                    field_id="gender",
                    dictionary_id="dict_gender",
                    dictionary_name="Płeć",
                    confidence=0.9,
                ),
            ],
            proposed_dictionaries=[
                ProposedDictionary(
                    proposed_id="prop_case_types",
                    name="Rodzaje spraw (propozycja)",
                    target_field_id="case_type",
                    reason=(
                        "Lista wyboru wywnioskowana z treści formularza; "
                        "brak odpowiedniego słownika w lokalnej bazie COI."
                    ),
                    items=_CASE_TYPE_ITEMS,
                ),
            ],
        ),
        conditional_flows=[show_other, require_other],
    )
