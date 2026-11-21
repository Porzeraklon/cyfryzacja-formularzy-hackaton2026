"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Sekcje predefiniowane ze Standardów projektowania formularzy, wstawiane deterministycznie
(bez LLM). Treść, kolejność, typy i wymagalność odwzorowują wzorcowy FormDefinition
z materiałów COI („Wniosek o stwierdzenie nadpłaty podatku”).

Każda fabryka przyjmuje `uid` - funkcję rezerwującą unikalny identyfikator na bazie podanego
(np. "imie" -> "imie_2", gdy "imie" jest już zajęte przez pole z LLM). Zależności warunkowe
wewnątrz sekcji używają identyfikatorów zwróconych przez `uid`, więc pozostają spójne.
"""

from collections.abc import Callable

from backend.app.schemas.form_schema import (
    ConditionalFlow,
    ConditionOperator,
    ConsentItem,
    CustomValidator,
    DictionaryItem,
    FieldDefinition,
    FieldType,
    FlowAction,
    InitialDataSource,
    SectionDefinition,
    TargetType,
    ValidationRules,
)

STD_OFFICE = "STD_OFFICE"
STD_APPLICANT_ROLE = "STD_APPLICANT_ROLE"
STD_APPLICANT_DATA = "STD_APPLICANT_DATA"
STD_ADDRESS = "STD_ADDRESS"
STD_CONTACT = "STD_CONTACT"
STD_CONSENTS = "STD_CONSENTS"

POLAND = "Polska"
OWN = "Swoim"
OTHER_PERSON = "Innej osoby"
LEGAL_ENTITY = "Osoby prawnej lub jednostki organizacyjnej"

DEFAULT_CONSENTS = (
    ConsentItem(description="Oświadczam, że dane podane we wniosku są zgodne z prawdą.", required=True),
    ConsentItem(
        description=(
            "Zapoznaję się z klauzulą informacyjną o przetwarzaniu danych osobowych (dostępna w BIP urzędu)."
        ),
        required=True,
    ),
)

FINISH_TEXTS = (
    (
        "podsumowanie",
        "Podsumowanie",
        "Urząd rozpatrzy wniosek. Jeśli będą potrzebne dodatkowe informacje, urzędnik skontaktuje się z Tobą.",
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

Uid = Callable[[str], str]


def _field(
    field_id: str,
    label: str,
    field_type: FieldType = FieldType.TEXT,
    *,
    required: bool = False,
    validator: CustomValidator | None = None,
    **extra: object,
) -> FieldDefinition:
    return FieldDefinition(
        field_id=field_id,
        name=field_id,
        label=label,
        field_type=field_type,
        required=required,
        validation_rules=ValidationRules(custom_validator=validator) if validator else None,
        **extra,
    )


def _flow(
    source: str,
    value: object,
    target: str,
    *,
    operator: ConditionOperator = ConditionOperator.EQUALS,
    action: FlowAction = FlowAction.SHOW,
) -> ConditionalFlow:
    return ConditionalFlow(
        source_field_id=source,
        operator=operator,
        value=value,
        target_type=TargetType.FIELD,
        target_id=target,
        action=action,
    )


def _options(tech: str, labels: list[str]) -> list[DictionaryItem]:
    return [DictionaryItem(code=f"{tech}_{i}", label=label) for i, label in enumerate(labels, 1)]


def office_section(uid: Uid) -> SectionDefinition:
    return SectionDefinition(
        section_id=uid("urzad"),
        title="Urząd, do którego jest składany wniosek",
        order=1,
        predefined=STD_OFFICE,
        fields=[_field(uid("nazwa_urzedu"), "Nazwa urzędu", required=True)],
    )


def applicant_role_section(uid: Uid) -> SectionDefinition:
    role = uid("w_czyim_imieniu")
    as_who = uid("jako_kto")
    return SectionDefinition(
        section_id=uid("podmiot_wniosku"),
        title="Podmiot wniosku",
        order=1,
        predefined=STD_APPLICANT_ROLE,
        fields=[
            _field(
                role,
                "Wybierz, w czyim imieniu wnioskujesz",
                FieldType.RADIO,
                required=True,
                options=_options(role, [OWN, OTHER_PERSON, LEGAL_ENTITY]),
                initial_value=OWN,
            ),
            _field(
                as_who,
                "Wybierz, jako kto wnioskujesz",
                FieldType.RADIO,
                required=True,
                options=_options(as_who, ["Pełnomocnik", "Reprezentant"]),
                dependencies=[
                    _flow(role, OWN, as_who, operator=ConditionOperator.NOT_EQUALS),
                    _flow(role, OWN, as_who, operator=ConditionOperator.NOT_EQUALS, action=FlowAction.REQUIRE),
                ],
            ),
        ],
    )


def applicant_data_section(uid: Uid) -> SectionDefinition:
    """„Twoje dane”: dane zalogowanego użytkownika z MLPZ, tylko do odczytu."""
    fields = [
        _field(
            uid(field_id),
            label,
            required=True,
            validator=validator,
            read_only=True,
            default_value_type=InitialDataSource.MLPZ,
            default_value_key=key,
        )
        for field_id, label, key, validator in (
            ("imie", "Imię", "name", None),
            ("nazwisko", "Nazwisko", "surname", None),
            ("pesel", "Numer PESEL", "pesel", CustomValidator.PESEL),
        )
    ]
    return SectionDefinition(
        section_id=uid("twoje_dane"),
        title="Twoje dane",
        order=1,
        predefined=STD_APPLICANT_DATA,
        description="Dane pobraliśmy z Twojego profilu. Jeśli są nieaktualne, zaktualizuj je w profilu.",
        fields=fields,
    )


def address_section(uid: Uid, prefix: str = "adres_zamieszkania", title: str = "Adres zamieszkania") -> SectionDefinition:
    """Adres wg Standardów: kraj (domyślnie Polska) -> województwo i kod PL albo kod zagraniczny."""
    country = uid(f"{prefix}_kraj")
    voivodeship = uid(f"{prefix}_wojewodztwo")
    zip_pl = uid(f"{prefix}_kod_pocztowy")
    zip_foreign = uid(f"{prefix}_kod_zagraniczny")
    street = uid(f"{prefix}_ulica")
    no_street = uid(f"{prefix}_brak_ulicy")
    return SectionDefinition(
        section_id=uid(prefix),
        title=title,
        order=1,
        predefined=STD_ADDRESS,
        fields=[
            _field(
                country, "Kraj", FieldType.SELECT, required=True, dictionary_ref="dict_countries",
                initial_value=POLAND,
            ),
            _field(
                voivodeship, "Województwo", FieldType.SELECT, required=True,
                dictionary_ref="dict_voivodeships", dependencies=[_flow(country, POLAND, voivodeship)],
            ),
            _field(uid(f"{prefix}_miejscowosc"), "Miejscowość", required=True),
            _field(
                zip_pl, "Kod pocztowy", required=True, validator=CustomValidator.ZIP_CODE,
                dependencies=[_flow(country, POLAND, zip_pl)],
            ),
            _field(
                zip_foreign, "Kod pocztowy", required=True,
                dependencies=[_flow(country, POLAND, zip_foreign, operator=ConditionOperator.NOT_EQUALS)],
            ),
            _field(
                street, "Ulica", required=True,
                dependencies=[_flow(no_street, True, street, action=FlowAction.OPTIONAL)],
            ),
            _field(no_street, "Brak nazwy ulicy", FieldType.CHECKBOX),
            _field(uid(f"{prefix}_nr_budynku"), "Numer budynku", required=True),
            _field(uid(f"{prefix}_nr_lokalu"), "Numer lokalu"),
            _field(uid(f"{prefix}_brak_lokalu"), "Brak numeru lokalu", FieldType.CHECKBOX),
        ],
    )


def contact_section(uid: Uid) -> SectionDefinition:
    return SectionDefinition(
        section_id=uid("dane_do_kontaktu"),
        title="Dane do kontaktu",
        order=1,
        predefined=STD_CONTACT,
        description=(
            "Wpisanie danych do kontaktu może przyspieszyć załatwienie sprawy. Urzędnik będzie mógł "
            "skontaktować się z Tobą w przypadku pytań lub wątpliwości."
        ),
        fields=[
            _field(
                uid("numer_kierunkowy"), "Numer kierunkowy", FieldType.SELECT,
                dictionary_ref="dict_dial_codes", initial_value="+48 Polska",
            ),
            _field(uid("telefon"), "Numer telefonu"),
            _field(uid("email"), "Adres e-mail", validator=CustomValidator.EMAIL),
        ],
    )


def consents_section(uid: Uid, consents: list[ConsentItem] | None = None) -> SectionDefinition:
    items = list(consents or DEFAULT_CONSENTS)
    return SectionDefinition(
        section_id=uid("zgody"),
        title="Zgody i oświadczenia",
        order=1,
        predefined=STD_CONSENTS,
        fields=[
            _field(
                uid("zgody_oswiadczenia"),
                "Zgody i oświadczenia",
                FieldType.CONSENT_SECTION,
                required=any(item.required for item in items),
                consents=items,
            )
        ],
    )


def finish_sections(uid: Uid) -> list[SectionDefinition]:
    """Strona końcowa (reguła 7): wyłącznie sekcje informacyjne INFO_SECTION."""
    return [
        SectionDefinition(
            section_id=uid(tech),
            title=title,
            order=order,
            fields=[_field(uid(f"{tech}_info"), "", FieldType.INFO_SECTION, help_text=text)],
        )
        for order, (tech, title, text) in enumerate(FINISH_TEXTS, start=1)
    ]
