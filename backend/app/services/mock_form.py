"""Mock pipeline: formularz demonstracyjny (USE_MOCK_PIPELINE=true).

Odwzorowuje w formacie WEWNĘTRZNYM (FormDocumentSchema) wzorcowy FormDefinition z materiałów
konkursu COI: „Wniosek o stwierdzenie nadpłaty podatku” (frontend/examples/
wniosek-o-stwierdzenie-nadplaty-podatku.json). Zachowane są: kolejność stron (3 strony MAIN
+ strona FINISH), sekcje, pola i ich techName (= `name`), typy, wymagalność, walidatory COI,
słowniki, dane inicjalne, sekcja powtarzająca się oraz stany dynamiczne (jsonata SHOW).

Konwencje odwzorowania:
- `name` / `field_id` = techName pola ze wzoru; `section_id` = techName sekcji (poza sekcją
  z grupą powtarzającą się, w której techName nosi pole `repeatable_group`);
- `placeholder` = tooltipMessage wzoru, `help_text` = description pola;
- treść pól text_block / info_section jest w `help_text` (description), `label` jest pusty jak
  title we wzorze;
- wartości pól wyboru (initial_value, ConditionalFlow.value) to ETYKIETY opcji/pozycji słownika,
  bo FormDefinition przechowuje w defaultValue/__userAnswer nazwę (np. "Polska");
- stany READ_ONLY wzoru (pole „Ulica” przy zaznaczonym „Brak nazwy ulicy”) nie mają odpowiednika
  w FlowAction - wymagalność ulicy zdejmuje reguła OPTIONAL.
"""

from datetime import UTC, datetime
from uuid import uuid4

from backend.app.schemas.form_schema import (
    ConditionalFlow,
    ConditionOperator,
    ConsentItem,
    CustomValidator,
    DataType,
    DictionariesBlock,
    DictionaryItem,
    FieldDefinition,
    FieldType,
    FlowAction,
    FormDocumentSchema,
    FormMetadata,
    InitialDataSource,
    MappedDictionary,
    PageDefinition,
    PageType,
    ProposedDictionary,
    SectionDefinition,
    TargetType,
    ValidationRules,
)

FORM_TITLE = "Wniosek o stwierdzenie nadpłaty podatku"

# Identyfikatory sekcji predefiniowanych ze Standardów (wspólne z logiką domenową).
STD_OFFICE = "STD_OFFICE"
STD_APPLICANT_ROLE = "STD_APPLICANT_ROLE"
STD_APPLICANT_DATA = "STD_APPLICANT_DATA"
STD_ADDRESS = "STD_ADDRESS"
STD_CONTACT = "STD_CONTACT"
STD_CONSENTS = "STD_CONSENTS"

# Słowniki COI: identyfikator w lokalnej bazie -> oficjalny dictionaryName w FormDefinition.
_COI_DICTIONARY_NAMES = {
    "dict_countries": "Kraje",
    "dict_voivodeships": "Województwo",
    "dict_dial_codes": "Numery kierunkowe",
}

_POLAND = "Polska"
_OWN = "Swoim"
_OTHER_PERSON = "Innej osoby"
_LEGAL_ENTITY = "Osoby prawnej lub jednostki organizacyjnej"
_PROXY = "Pełnomocnik"
_REPRESENTATIVE = "Reprezentant"
_YES = "Tak"
_SETTLE_FUTURE = "Zaliczyć na poczet przyszłego zobowiązania podatkowego"
_SETTLE_REFUND = "Zwrócić przelewem na rachunek bankowy"

_PROPOSED_SETTLEMENT_ID = "prop_overpayment_settlement"

_STAMP_DUTY_INFO = (
    "Opłata skarbowa wynosi 17 zł.\n"
    "Przelej ją na konto urzędu miasta lub gminy właściwego dla adresu zamieszkania osoby, "
    "która udzieliła pełnomocnictwa. Jeśli adres nie jest w Polsce, opłatę przelej na konto "
    "Urzędu Miasta Stołecznego Warszawy – Centrum Obsługi Podatnika.\n"
    "W tytule przelewu wpisz „Wniosek o stwierdzenie nadpłaty podatku – opłata skarbowa "
    "za pełnomocnictwo”.\n"
    "Dodaj plik z potwierdzeniem przelewu. Znajdziesz go na stronie lub w aplikacji swojego banku."
)


def _options(tech_name: str, labels: list[str]) -> list[DictionaryItem]:
    """Opcje pola wyboru; kod = techName opcji ze wzoru (np. w_czyim_imieniu_1)."""
    return [DictionaryItem(code=f"{tech_name}_{i}", label=label) for i, label in enumerate(labels, 1)]


def _flow(
    source: str,
    value: object,
    target_id: str,
    *,
    target_type: TargetType = TargetType.FIELD,
    operator: ConditionOperator = ConditionOperator.EQUALS,
    action: FlowAction = FlowAction.SHOW,
) -> ConditionalFlow:
    return ConditionalFlow(
        source_field_id=source,
        operator=operator,
        value=value,
        target_type=target_type,
        target_id=target_id,
        action=action,
    )


def _section_show(source: str, value: str, section_id: str) -> ConditionalFlow:
    return _flow(source, value, section_id, target_type=TargetType.SECTION)


def _field(
    field_id: str,
    label: str,
    field_type: FieldType = FieldType.TEXT,
    *,
    required: bool = False,
    validator: CustomValidator | None = None,
    data_type: DataType | None = None,
    **extra: object,
) -> FieldDefinition:
    rules = None
    if validator or data_type:
        rules = ValidationRules(custom_validator=validator, data_type=data_type)
    return FieldDefinition(
        field_id=field_id,
        name=field_id,
        label=label,
        field_type=field_type,
        required=required,
        validation_rules=rules,
        **extra,
    )


def _static_text(
    field_id: str, text: str, field_type: FieldType = FieldType.TEXT_BLOCK, **extra: object
) -> FieldDefinition:
    """Pole prezentacyjne (text_block / info_section): treść w help_text, bez etykiety."""
    return _field(field_id, "", field_type, help_text=text, **extra)


def _address_section(
    prefix: str,
    title: str,
    order: int,
    *,
    predefined: str | None = None,
    visibility_rules: list[ConditionalFlow] | None = None,
) -> SectionDefinition:
    """Sekcja adresowa wg Standardów (Kraj -> Województwo/kod PL lub kod zagraniczny, ulica, numery)."""
    country = f"{prefix}_kraj"
    return SectionDefinition(
        section_id=prefix,
        title=title,
        order=order,
        predefined=predefined,
        visibility_rules=visibility_rules or [],
        fields=[
            _field(
                country,
                "Kraj",
                FieldType.SELECT,
                required=True,
                dictionary_ref="dict_countries",
                initial_value=_POLAND,
            ),
            _field(
                f"{prefix}_wojewodztwo",
                "Województwo",
                FieldType.SELECT,
                required=True,
                dictionary_ref="dict_voivodeships",
                dependencies=[_flow(country, _POLAND, f"{prefix}_wojewodztwo")],
            ),
            _field(f"{prefix}_miejscowosc", "Miejscowość", required=True),
            _field(
                f"{prefix}_kod_pocztowy",
                "Kod pocztowy",
                required=True,
                validator=CustomValidator.ZIP_CODE,
                dependencies=[_flow(country, _POLAND, f"{prefix}_kod_pocztowy")],
            ),
            _field(
                f"{prefix}_kod_zagraniczny",
                "Kod pocztowy",
                required=True,
                dependencies=[
                    _flow(
                        country,
                        _POLAND,
                        f"{prefix}_kod_zagraniczny",
                        operator=ConditionOperator.NOT_EQUALS,
                    )
                ],
            ),
            _field(
                f"{prefix}_ulica",
                "Ulica",
                required=True,
                dependencies=[
                    _flow(f"{prefix}_brak_ulicy", True, f"{prefix}_ulica", action=FlowAction.OPTIONAL)
                ],
            ),
            _field(f"{prefix}_brak_ulicy", "Brak nazwy ulicy", FieldType.CHECKBOX),
            _field(f"{prefix}_nr_budynku", "Numer budynku", required=True),
            _field(f"{prefix}_nr_lokalu", "Numer lokalu"),
            _field(f"{prefix}_brak_lokalu", "Brak numeru lokalu", FieldType.CHECKBOX),
        ],
    )


def _page_applicant() -> PageDefinition:
    other = _OTHER_PERSON
    entity = _LEGAL_ENTITY
    return PageDefinition(
        page_id="page_applicant",
        title="Dane wnioskodawcy",
        order=1,
        description="Urząd, podmiot wniosku oraz dane osoby lub podmiotu, którego dotyczy wniosek.",
        sections=[
            SectionDefinition(
                section_id="urzad",
                title="Urząd, do którego jest składany wniosek",
                order=1,
                predefined=STD_OFFICE,
                fields=[
                    _field(
                        "nazwa_urzedu",
                        "Nazwa urzędu",
                        required=True,
                        read_only=True,
                        initial_value="Urząd Gminy Ulan-Majorat",
                    ),
                ],
            ),
            SectionDefinition(
                section_id="podmiot_wniosku",
                title="Podmiot wniosku",
                order=2,
                predefined=STD_APPLICANT_ROLE,
                fields=[
                    _field(
                        "w_czyim_imieniu",
                        "Wybierz, w czyim imieniu wnioskujesz",
                        FieldType.RADIO,
                        required=True,
                        options=_options("w_czyim_imieniu", [_OWN, other, entity]),
                        initial_value=_OWN,
                    ),
                    _field(
                        "jako_kto",
                        "Wybierz, jako kto wnioskujesz",
                        FieldType.RADIO,
                        required=True,
                        options=_options("jako_kto", [_PROXY, _REPRESENTATIVE]),
                        dependencies=[_flow("w_czyim_imieniu", entity, "jako_kto")],
                    ),
                ],
            ),
            SectionDefinition(
                section_id="twoje_dane",
                title="Twoje dane",
                order=3,
                predefined=STD_APPLICANT_DATA,
                fields=[
                    _field(
                        field_id,
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
                ],
            ),
            _address_section("adres_zamieszkania", "Adres zamieszkania", 4, predefined=STD_ADDRESS),
            SectionDefinition(
                section_id="dane_innej_osoby",
                title="Dane innej osoby",
                order=5,
                visibility_rules=[_section_show("w_czyim_imieniu", other, "dane_innej_osoby")],
                fields=[
                    _field("inna_imie", "Imię", required=True),
                    _field("inna_nazwisko", "Nazwisko", required=True),
                    _field("inna_pesel", "Numer PESEL", required=True, validator=CustomValidator.PESEL),
                    _field(
                        "inna_dowod",
                        "Seria i numer dowodu osobistego",
                        validator=CustomValidator.SERIAL_NUMBER_ID_CARD,
                        placeholder="3 litery i 6 cyfr, np. ABA300000",
                    ),
                ],
            ),
            _address_section(
                "adres_innej_osoby",
                "Adres zamieszkania innej osoby",
                6,
                visibility_rules=[_section_show("w_czyim_imieniu", other, "adres_innej_osoby")],
            ),
            SectionDefinition(
                section_id="dane_podmiotu",
                title="Dane podmiotu",
                order=7,
                visibility_rules=[_section_show("w_czyim_imieniu", entity, "dane_podmiotu")],
                fields=[
                    _field("podmiot_nazwa", "Nazwa", required=True),
                    _field("podmiot_nip", "NIP", required=True, validator=CustomValidator.NIP),
                    _field("podmiot_regon", "REGON", required=True, validator=CustomValidator.REGON),
                    _field("podmiot_krs", "Numer KRS", help_text="Wpisz 10 cyfr."),
                ],
            ),
            _address_section(
                "adres_siedziby",
                "Adres siedziby",
                8,
                visibility_rules=[_section_show("w_czyim_imieniu", entity, "adres_siedziby")],
            ),
            SectionDefinition(
                section_id="pelnomocnictwo",
                title="Pełnomocnictwo",
                order=9,
                description="Dodaj dokument potwierdzający, że jesteś pełnomocnikiem.",
                # Wzór: "Innej osoby" LUB (osoba prawna I pełnomocnik). Kilka reguł SHOW = alternatywa;
                # pole jako_kto jest widoczne tylko dla osoby prawnej, więc druga reguła oznacza koniunkcję.
                visibility_rules=[
                    _section_show("w_czyim_imieniu", other, "pelnomocnictwo"),
                    _section_show("jako_kto", _PROXY, "pelnomocnictwo"),
                ],
                fields=[
                    _field(
                        "plik_pelnomocnictwa",
                        "Plik dokumentu z pełnomocnictwem",
                        FieldType.FILE,
                        required=True,
                    ),
                ],
            ),
            SectionDefinition(
                section_id="oplata_skarbowa",
                title="Opłata skarbowa",
                order=10,
                description=(
                    "Nie musisz płacić za złożenie pełnomocnictwa wystawionego przez najbliższą rodzinę: "
                    "małżonka, dzieci, rodziców, dziadków, wnuków, rodzeństwo. Więcej informacji "
                    "na stronie biznes.gov.pl"
                ),
                visibility_rules=[_section_show("w_czyim_imieniu", other, "oplata_skarbowa")],
                fields=[
                    _field(
                        "czy_oplata",
                        "Czy złożenie pełnomocnictwa wymaga opłaty skarbowej?",
                        FieldType.RADIO,
                        required=True,
                        options=_options("czy_oplata", [_YES, "Nie"]),
                    ),
                    _static_text(
                        "oplata_info",
                        _STAMP_DUTY_INFO,
                        dependencies=[_flow("czy_oplata", _YES, "oplata_info")],
                    ),
                    _field(
                        "plik_przelewu",
                        "Plik z potwierdzeniem przelewu",
                        FieldType.FILE,
                        required=True,
                        dependencies=[_flow("czy_oplata", _YES, "plik_przelewu")],
                    ),
                ],
            ),
            SectionDefinition(
                section_id="oplata_skarbowa_podmiot",
                title="Opłata skarbowa za pełnomocnictwo",
                order=11,
                # Wzór: osoba prawna I pełnomocnik - jako_kto jest widoczne tylko dla osoby prawnej.
                visibility_rules=[_section_show("jako_kto", _PROXY, "oplata_skarbowa_podmiot")],
                fields=[
                    _static_text("oplata_podmiot_info", _STAMP_DUTY_INFO),
                    _field(
                        "plik_przelewu_podmiot",
                        "Plik z potwierdzeniem przelewu",
                        FieldType.FILE,
                        required=True,
                    ),
                ],
            ),
            SectionDefinition(
                section_id="potwierdzenie_reprezentanta",
                title="Potwierdzenie, że jesteś reprezentantem",
                order=12,
                visibility_rules=[_section_show("jako_kto", _REPRESENTATIVE, "potwierdzenie_reprezentanta")],
                fields=[
                    _field(
                        "plik_reprezentacji",
                        "Dokument potwierdzający prawo do reprezentacji",
                        FieldType.FILE,
                        required=True,
                        help_text="Na przykład odpis z KRS lub uchwała o powołaniu.",
                    ),
                ],
            ),
        ],
    )


_SETTLEMENT_OPTIONS = _options("co_z_nadplata", [_SETTLE_FUTURE, _SETTLE_REFUND])


def _page_overpayment() -> PageDefinition:
    periods_description = "Dodaj każdy rok, w którym powstała nadpłata."
    return PageDefinition(
        page_id="page_overpayment",
        title="Nadpłata i deklaracja",
        order=2,
        description="Strona tematyczna: informacje o nadpłacie, jej rozliczenie i korekta deklaracji.",
        sections=[
            SectionDefinition(
                section_id="informacje_o_nadplacie",
                title="Informacje o nadpłacie",
                order=1,
                fields=[
                    _field("podatek", "Podatek, którego dotyczy nadpłata", required=True),
                    _field(
                        "kwota",
                        "Kwota",
                        FieldType.NUMBER,
                        required=True,
                        data_type=DataType.NUMBER,
                        placeholder="Kwota w złotych, np. 120,50",
                    ),
                    _static_text(
                        "uzasadnienie_info",
                        "Uzasadnij dlaczego Twoim zdaniem powstała nadpłata i z czego wynika jej wysokość.",
                    ),
                    _field("uzasadnienie", "Uzasadnienie", FieldType.TEXTAREA, required=True),
                ],
            ),
            SectionDefinition(
                section_id="sekcja_okresy_nadplaty",
                title="Okresy, których dotyczy nadpłata",
                order=2,
                description=periods_description,
                fields=[
                    _field(
                        "okresy_nadplaty",
                        "Okresy, których dotyczy nadpłata",
                        FieldType.REPEATABLE_GROUP,
                        help_text=periods_description,
                        fields=[
                            _field(
                                "okres_rok",
                                "Rok podatkowy",
                                FieldType.NUMBER,
                                required=True,
                                data_type=DataType.INTEGER,
                            ),
                            _field("okres_data_deklaracji", "Data złożenia deklaracji", FieldType.DATE),
                            _field(
                                "okres_kwota",
                                "Kwota nadpłaty w tym okresie",
                                FieldType.NUMBER,
                                required=True,
                                data_type=DataType.NUMBER,
                            ),
                        ],
                    ),
                ],
            ),
            SectionDefinition(
                section_id="rozliczenie_nadplaty",
                title="Rozliczenie nadpłaty",
                order=3,
                fields=[
                    _field(
                        "co_z_nadplata",
                        "Wybierz, co mamy zrobić z nadpłatą",
                        FieldType.RADIO,
                        required=True,
                        dictionary_ref=_PROPOSED_SETTLEMENT_ID,
                        options=_SETTLEMENT_OPTIONS,
                    ),
                    _field(
                        "nazwa_podatku",
                        "Nazwa podatku",
                        required=True,
                        dependencies=[_flow("co_z_nadplata", _SETTLE_FUTURE, "nazwa_podatku")],
                    ),
                    _field(
                        "numer_rachunku",
                        "Numer rachunku",
                        required=True,
                        dependencies=[_flow("co_z_nadplata", _SETTLE_REFUND, "numer_rachunku")],
                    ),
                ],
            ),
            SectionDefinition(
                section_id="korekta_deklaracji",
                title="Korekta deklaracji",
                order=4,
                fields=[
                    _static_text(
                        "korekta_info",
                        "Załącz poprawioną deklarację podatkową, z której wynika prawidłowa, niższa "
                        "wysokość zobowiązania podatkowego, a w konsekwencji nadpłata.",
                    ),
                    _field("plik_deklaracji", "Plik deklaracji podatkowej", FieldType.FILE, required=True),
                ],
            ),
        ],
    )


def _page_contact() -> PageDefinition:
    return PageDefinition(
        page_id="page_contact",
        title="Kontakt i zgody",
        order=3,
        description="Strona tematyczna: dane do kontaktu oraz zgody i oświadczenia.",
        sections=[
            SectionDefinition(
                section_id="dane_do_kontaktu",
                title="Dane do kontaktu",
                order=1,
                predefined=STD_CONTACT,
                description=(
                    "Wpisanie danych do kontaktu może przyspieszyć załatwienie sprawy. Urzędnik będzie "
                    "mógł skontaktować się z Tobą w przypadku pytań lub wątpliwości."
                ),
                fields=[
                    _field(
                        "numer_kierunkowy",
                        "Numer kierunkowy",
                        FieldType.SELECT,
                        dictionary_ref="dict_dial_codes",
                        initial_value="+48 Polska",
                    ),
                    _field("telefon", "Numer telefonu"),
                    _field("email", "Adres e-mail", validator=CustomValidator.EMAIL),
                    _field(
                        "adres_ade",
                        "Adres do e-Doręczeń",
                        validator=CustomValidator.ADE,
                        placeholder="Format: AE:PL-12345-67890-ABCDE-12",
                    ),
                    _field(
                        "skrytka_epuap",
                        "Adres skrytki ePUAP",
                        validator=CustomValidator.EPUAP,
                        placeholder="Format: /nazwa_podmiotu/SkrytkaESP",
                    ),
                ],
            ),
            SectionDefinition(
                section_id="zgody",
                title="Zgody i oświadczenia",
                order=2,
                predefined=STD_CONSENTS,
                fields=[
                    _field(
                        "zgody_oswiadczenia",
                        "Zgody i oświadczenia",
                        FieldType.CONSENT_SECTION,
                        required=True,
                        consents=[
                            ConsentItem(
                                description="Oświadczam, że dane podane we wniosku są zgodne z prawdą.",
                                required=True,
                            ),
                            ConsentItem(
                                description=(
                                    "Zapoznaję się z klauzulą informacyjną o przetwarzaniu danych "
                                    "osobowych (dostępna w BIP urzędu)."
                                ),
                                required=True,
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _page_finish() -> PageDefinition:
    """Strona końcowa FINISH: wyłącznie sekcje informacyjne (reguła 7, Standardy: Podsumowanie)."""
    return PageDefinition(
        page_id="page_finish",
        title="Podsumowanie",
        order=4,
        page_type=PageType.FINISH,
        sections=[
            SectionDefinition(
                section_id="podsumowanie",
                title="Podsumowanie",
                order=1,
                fields=[
                    _static_text(
                        "podsumowanie_info",
                        "Urząd rozpatrzy wniosek i wyda decyzję. Jeśli będą potrzebne dodatkowe "
                        "informacje, urzędnik skontaktuje się z Tobą.",
                        FieldType.INFO_SECTION,
                    ),
                ],
            ),
            SectionDefinition(
                section_id="podglad",
                title="Podgląd",
                order=2,
                fields=[
                    _static_text(
                        "podglad_info",
                        "Zanim przejdziesz do wyboru sposobu podpisu, sprawdź dane na podglądzie. "
                        "Jeśli zobaczysz błędy, wróć do edycji.",
                        FieldType.INFO_SECTION,
                    ),
                ],
            ),
        ],
    )


def _iter_fields(fields: list[FieldDefinition]):
    for field in fields:
        yield field
        if field.fields:
            yield from _iter_fields(field.fields)


def _collect_flows(pages: list[PageDefinition]) -> list[ConditionalFlow]:
    """Wszystkie reguły (widoczność sekcji + zależności pól) w jednej liście conditional_flows."""
    flows: list[ConditionalFlow] = []
    for page in pages:
        flows.extend(page.visibility_rules)
        for section in page.sections:
            flows.extend(section.visibility_rules)
            for field in _iter_fields(section.fields):
                flows.extend(field.dependencies)
    return flows


def _mapped_dictionaries(pages: list[PageDefinition]) -> list[MappedDictionary]:
    mapped: list[MappedDictionary] = []
    for page in pages:
        for section in page.sections:
            for field in _iter_fields(section.fields):
                name = _COI_DICTIONARY_NAMES.get(field.dictionary_ref or "")
                if name:
                    mapped.append(
                        MappedDictionary(
                            field_id=field.field_id,
                            dictionary_id=field.dictionary_ref,
                            dictionary_name=name,
                            confidence=0.97 if field.dictionary_ref == "dict_dial_codes" else 0.99,
                        )
                    )
    return mapped


def build_mock_form(filename: str, mime_type: str) -> FormDocumentSchema:
    """„Wniosek o stwierdzenie nadpłaty podatku” wg wzoru COI: 3 strony MAIN + strona FINISH."""
    pages = [_page_applicant(), _page_overpayment(), _page_contact(), _page_finish()]
    return FormDocumentSchema(
        metadata=FormMetadata(
            form_id=f"form-{uuid4().hex[:12]}",
            title=FORM_TITLE,
            version="1.0.0",
            source_filename=filename,
            source_format=mime_type,
            generated_at=datetime.now(UTC),
            ai_agent_ready=True,
        ),
        pages=pages,
        dictionaries=DictionariesBlock(
            mapped_dictionaries=_mapped_dictionaries(pages),
            proposed_dictionaries=[
                ProposedDictionary(
                    proposed_id=_PROPOSED_SETTLEMENT_ID,
                    name="Sposób rozliczenia nadpłaty (propozycja)",
                    target_field_id="co_z_nadplata",
                    reason=(
                        "Lista wyboru wywnioskowana z treści formularza; brak odpowiedniego słownika "
                        "w katalogu słowników COI - w FormDefinition trafia jako opcje pola."
                    ),
                    items=_SETTLEMENT_OPTIONS,
                ),
            ],
        ),
        conditional_flows=_collect_flows(pages),
    )
