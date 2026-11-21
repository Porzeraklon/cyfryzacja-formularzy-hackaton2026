"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Predefiniowane sekcje ze Standardów COI (HackNation), odwzorowane na
referencyjnych konfiguracjach konkursowych (testowe-formularze). Wstawiane
deterministycznie przez DomainLogicEnricher — nigdy przez LLM.

Strona „Dane wnioskodawcy” (12 sekcji, kolejność jak w referencji COI):
Urząd, Podmiot wniosku, Twoje dane (MLPZ, tylko do odczytu), Adres
zamieszkania, Dane podmiotu, Adres siedziby, Potwierdzenie reprezentanta,
Dane innej osoby, Adres zamieszkania innej osoby, Pełnomocnictwo,
Opłata skarbowa, Opłata skarbowa za pełnomocnictwo. Strona „Kontakt
i zgody”: Dane do kontaktu, Zgoda / Zgody i oświadczenia (CONSENT_SECTION,
nie checkboxy).

Zależności warunkowe są osadzone w sekcjach (visibility_rules) i polach
(dependencies); eksporter FormDefinition zamienia je na stany jsonata.
"""

from __future__ import annotations

from typing import Any

from backend.app.schemas.form_schema import (
    ConditionalFlow,
    ConditionOperator,
    ConsentItem,
    CustomValidator,
    DictionaryItem,
    FieldDefinition,
    FieldType,
    FlowAction,
    SectionDefinition,
    TargetType,
    ValidationRules,
)

INNA_OSOBA = "Innej osoby"
OSOBA_PRAWNA = "Osoby prawnej lub jednostki organizacyjnej"
PELNOMOCNIK = "Pełnomocnik"
REPREZENTANT = "Reprezentant"
POLSKA = "Polska"

_OPLATA_INFO = (
    "Opłata skarbowa wynosi 17 zł.\n"
    "Przelej ją na konto urzędu miasta lub gminy właściwego dla adresu zamieszkania osoby, "
    "która udzieliła pełnomocnictwa. Jeśli adres nie jest w Polsce, opłatę przelej na konto "
    "Urzędu Miasta Stołecznego Warszawy – Centrum Obsługi Podatnika.\n"
    "W tytule przelewu wpisz „{tytul} – opłata skarbowa za pełnomocnictwo”.\n"
    "Dodaj plik z potwierdzeniem przelewu. Znajdziesz go na stronie lub w aplikacji swojego banku."
)
_OPLATA_OPIS = (
    "Nie musisz płacić za złożenie pełnomocnictwa wystawionego przez najbliższą rodzinę: "
    "małżonka, dzieci, rodziców, dziadków, wnuków, rodzeństwo. Więcej informacji na stronie biznes.gov.pl"
)


def _field(
    field_id: str,
    label: str,
    field_type: FieldType = FieldType.TEXT,
    *,
    required: bool = False,
    placeholder: str | None = None,
    help_text: str | None = None,
    initial_value: Any = None,
    dictionary_ref: str | None = None,
    options: list[DictionaryItem] | None = None,
    validator: CustomValidator | None = None,
    rules: ValidationRules | None = None,
    dependencies: list[ConditionalFlow] | None = None,
    read_only: bool = False,
    default_value_type: str = "",
    consents: list[ConsentItem] | None = None,
) -> FieldDefinition:
    return FieldDefinition(
        field_id=field_id,
        name=field_id,
        label=label,
        field_type=field_type,
        required=required,
        placeholder=placeholder,
        help_text=help_text,
        initial_value=initial_value,
        dictionary_ref=dictionary_ref,
        options=options,
        validation_rules=(
            rules
            if rules is not None
            else (ValidationRules(custom_validator=validator) if validator else None)
        ),
        dependencies=dependencies or [],
        read_only=read_only,
        default_value_type=default_value_type,
        consents=consents,
    )


def _show(
    source_field_id: str,
    value: Any,
    target_id: str,
    target_type: TargetType = TargetType.SECTION,
) -> ConditionalFlow:
    return ConditionalFlow(
        source_field_id=source_field_id,
        operator=ConditionOperator.EQUALS,
        value=value,
        target_type=target_type,
        target_id=target_id,
        action=FlowAction.SHOW,
    )


def _read_only(source_field_id: str, target_id: str) -> ConditionalFlow:
    return ConditionalFlow(
        source_field_id=source_field_id,
        operator=ConditionOperator.EQUALS,
        value=True,
        target_type=TargetType.FIELD,
        target_id=target_id,
        action=FlowAction.READ_ONLY,
    )


def _sub(field_id: str, value: Any) -> dict[str, Any]:
    return {"field_id": field_id, "operator": ConditionOperator.EQUALS.value, "value": value}


def _first_source(children: list[dict[str, Any]]) -> str:
    if not children:
        raise ValueError("Pusta lista podwarunków.")
    child = children[0]
    if "field_id" in child:
        return child["field_id"]
    return _first_source(child["value"])


def _compound(
    operator: ConditionOperator,
    children: list[dict[str, Any]],
    target_id: str,
) -> ConditionalFlow:
    return ConditionalFlow(
        source_field_id=_first_source(children),
        operator=operator,
        value=children,
        target_type=TargetType.SECTION,
        target_id=target_id,
        action=FlowAction.SHOW,
    )


def _registry_field(field_id: str, label: str, attribute: str, *, pesel: bool = False) -> FieldDefinition:
    return _field(
        field_id,
        label,
        required=True,
        initial_value=f"MLPZ.{attribute}",
        read_only=True,
        default_value_type="EXTERNAL_SYSTEM",
        validator=CustomValidator.PESEL if pesel else None,
    )


def _address_fields(prefix: str) -> list[FieldDefinition]:
    kraj = f"{prefix}kraj"

    def widoczne_polska(target: str) -> list[ConditionalFlow]:
        return [_show(kraj, POLSKA, target, TargetType.FIELD)]

    return [
        _field(
            kraj,
            "Kraj",
            FieldType.SELECT,
            required=True,
            dictionary_ref="dict_countries",
            initial_value=POLSKA,
        ),
        _field(
            f"{prefix}wojewodztwo",
            "Województwo",
            FieldType.SELECT,
            required=True,
            dictionary_ref="dict_voivodeships",
            dependencies=widoczne_polska(f"{prefix}wojewodztwo"),
        ),
        _field(
            f"{prefix}miejscowosc",
            "Miejscowość",
            FieldType.SELECT,
            required=True,
            dictionary_ref="dict_places",
            dependencies=widoczne_polska(f"{prefix}miejscowosc"),
        ),
        _field(
            f"{prefix}miejscowosc_zagranica",
            "Miejscowość",
            required=True,
            rules=ValidationRules(),
            dependencies=[
                ConditionalFlow(
                    source_field_id=kraj,
                    operator=ConditionOperator.NOT_EQUALS,
                    value=POLSKA,
                    target_type=TargetType.FIELD,
                    target_id=f"{prefix}miejscowosc_zagranica",
                    action=FlowAction.SHOW,
                )
            ],
        ),
        _field(
            f"{prefix}kod_pocztowy",
            "Kod pocztowy",
            required=True,
            validator=CustomValidator.ZIP_CODE,
            dependencies=widoczne_polska(f"{prefix}kod_pocztowy"),
        ),
        _field(
            f"{prefix}kod_zagraniczny",
            "Kod pocztowy",
            required=True,
            rules=ValidationRules(),
            dependencies=[
                ConditionalFlow(
                    source_field_id=kraj,
                    operator=ConditionOperator.NOT_EQUALS,
                    value=POLSKA,
                    target_type=TargetType.FIELD,
                    target_id=f"{prefix}kod_zagraniczny",
                    action=FlowAction.SHOW,
                )
            ],
        ),
        _field(
            f"{prefix}ulica",
            "Ulica",
            required=True,
            placeholder="Np. Polna, Aleja Leśna, Plac Lipowy, Skwer Słoneczny",
            dependencies=[_read_only(f"{prefix}brak_ulicy", f"{prefix}ulica")],
        ),
        _field(f"{prefix}brak_ulicy", "Brak nazwy ulicy", FieldType.CHECKBOX, options=[]),
        _field(f"{prefix}nr_budynku", "Numer budynku", required=True),
        _field(
            f"{prefix}nr_lokalu",
            "Numer lokalu",
            dependencies=[_read_only(f"{prefix}brak_lokalu", f"{prefix}nr_lokalu")],
        ),
        _field(f"{prefix}brak_lokalu", "Brak numeru lokalu", FieldType.CHECKBOX, options=[]),
    ]


def _address_section(
    section_id: str,
    title: str,
    prefix: str,
    order: int,
    show_value: str | None = None,
) -> SectionDefinition:
    rules = [_show("w_czyim_imieniu", show_value, section_id)] if show_value else []
    return SectionDefinition(
        section_id=section_id,
        title=title,
        order=order,
        visibility_rules=rules,
        fields=_address_fields(prefix),
    )


def applicant_sections(
    office_name: str = "Urząd Gminy Ulan-Majorat",
    form_title: str | None = None,
) -> list[SectionDefinition]:
    """Sekcje strony „Dane wnioskodawcy” — dokładnie jak we wzorcu COI."""
    oplata_tekst = _OPLATA_INFO.format(tytul=form_title or "Wniosek o (...)")
    legal_i_pelnomocnik = [
        _sub("w_czyim_imieniu", OSOBA_PRAWNA),
        _sub("jako_kto", PELNOMOCNIK),
    ]
    return [
        SectionDefinition(
            section_id="urzad_do_ktorego_jest_skladany_wniosek",
            title="Urząd, do którego jest składany wniosek",
            order=1,
            fields=[
                _field(
                    "nazwa_urzedu", "Nazwa urzędu", required=True, initial_value=office_name, read_only=True
                ),
            ],
        ),
        SectionDefinition(
            section_id="podmiot_wniosku",
            title="Podmiot wniosku",
            order=2,
            fields=[
                _field(
                    "w_czyim_imieniu",
                    "Wybierz, w czyim imieniu wnioskujesz",
                    FieldType.RADIO,
                    required=True,
                    initial_value="Swoim",
                    options=[
                        DictionaryItem(code="swoim", label="Swoim"),
                        DictionaryItem(code="innej_osoby", label=INNA_OSOBA),
                        DictionaryItem(code="osoba_prawna", label=OSOBA_PRAWNA),
                    ],
                ),
                _field(
                    "jako_kto",
                    "Wybierz, jako kto wnioskujesz",
                    FieldType.RADIO,
                    required=True,
                    options=[
                        DictionaryItem(code="pelnomocnik", label=PELNOMOCNIK),
                        DictionaryItem(code="reprezentant", label=REPREZENTANT),
                    ],
                    dependencies=[_show("w_czyim_imieniu", OSOBA_PRAWNA, "jako_kto", TargetType.FIELD)],
                ),
            ],
        ),
        SectionDefinition(
            section_id="twoje_dane",
            title="Twoje dane",
            order=3,
            fields=[
                _registry_field("imie", "Imię", "name"),
                _registry_field("nazwisko", "Nazwisko", "surname"),
                _registry_field("pesel", "Numer PESEL", "pesel", pesel=True),
            ],
        ),
        _address_section("adres_zamieszkania", "Adres zamieszkania", "adres_zamieszkania_", 4),
        SectionDefinition(
            section_id="dane_podmiotu",
            title="Dane podmiotu",
            order=5,
            visibility_rules=[_show("w_czyim_imieniu", OSOBA_PRAWNA, "dane_podmiotu")],
            fields=[
                _field("podmiot_nazwa", "Nazwa", required=True),
                _field("podmiot_nip", "NIP", required=True, validator=CustomValidator.NIP),
                _field("podmiot_regon", "REGON", required=True, validator=CustomValidator.REGON),
                _field("podmiot_krs", "Numer KRS", help_text="Wpisz 10 cyfr."),
            ],
        ),
        _address_section("adres_siedziby", "Adres siedziby", "adres_siedziby_", 6, OSOBA_PRAWNA),
        SectionDefinition(
            section_id="potwierdzenie_ze_jestes_reprezentantem",
            title="Potwierdzenie, że jesteś reprezentantem",
            order=7,
            visibility_rules=[
                _compound(
                    ConditionOperator.ALL,
                    [_sub("w_czyim_imieniu", OSOBA_PRAWNA), _sub("jako_kto", REPREZENTANT)],
                    "potwierdzenie_ze_jestes_reprezentantem",
                )
            ],
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
        SectionDefinition(
            section_id="dane_innej_osoby",
            title="Dane innej osoby",
            order=8,
            visibility_rules=[_show("w_czyim_imieniu", INNA_OSOBA, "dane_innej_osoby")],
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
            "adres_zamieszkania_osoby_ktorej_jestes_pelnomocnikiem",
            "Adres zamieszkania innej osoby",
            "adres_innej_osoby_",
            9,
            INNA_OSOBA,
        ),
        SectionDefinition(
            section_id="pelnomocnictwo",
            title="Pełnomocnictwo",
            order=10,
            description="Dodaj dokument potwierdzający, że jesteś pełnomocnikiem.",
            visibility_rules=[
                _compound(
                    ConditionOperator.ANY,
                    [
                        _sub("w_czyim_imieniu", INNA_OSOBA),
                        {"operator": ConditionOperator.ALL.value, "value": legal_i_pelnomocnik},
                    ],
                    "pelnomocnictwo",
                )
            ],
            fields=[
                _field(
                    "plik_pelnomocnictwa", "Plik dokumentu z pełnomocnictwem", FieldType.FILE, required=True
                ),
            ],
        ),
        SectionDefinition(
            section_id="oplata_skarbowa_pelnomocnictwo_1",
            title="Opłata skarbowa",
            order=11,
            description=_OPLATA_OPIS,
            visibility_rules=[_show("w_czyim_imieniu", INNA_OSOBA, "oplata_skarbowa_pelnomocnictwo_1")],
            fields=[
                _field(
                    "czy_oplata",
                    "Czy złożenie pełnomocnictwa wymaga opłaty skarbowej?",
                    FieldType.RADIO,
                    required=True,
                    options=[
                        DictionaryItem(code="tak", label="Tak"),
                        DictionaryItem(code="nie", label="Nie"),
                    ],
                ),
                _field(
                    "oplata_info",
                    "",
                    FieldType.TEXT_BLOCK,
                    help_text=oplata_tekst,
                    dependencies=[_show("czy_oplata", "Tak", "oplata_info", TargetType.FIELD)],
                ),
                _field(
                    "plik_przelewu",
                    "Plik z potwierdzeniem przelewu",
                    FieldType.FILE,
                    required=True,
                    dependencies=[_show("czy_oplata", "Tak", "plik_przelewu", TargetType.FIELD)],
                ),
            ],
        ),
        SectionDefinition(
            section_id="oplata_skarbowa_pelnomocnictwo_2",
            title="Opłata skarbowa za pełnomocnictwo",
            order=12,
            visibility_rules=[
                _compound(ConditionOperator.ALL, legal_i_pelnomocnik, "oplata_skarbowa_pelnomocnictwo_2")
            ],
            fields=[
                _field("oplata_podmiot_info", "", FieldType.TEXT_BLOCK, help_text=oplata_tekst),
                _field(
                    "plik_przelewu_podmiot", "Plik z potwierdzeniem przelewu", FieldType.FILE, required=True
                ),
            ],
        ),
    ]


CONSENT_PERSONAL_DATA = (
    "Wyrażam zgodę na przetwarzanie moich danych osobowych zgodnie z klauzulą informacyjną"
)


def contact_sections() -> list[SectionDefinition]:
    """Sekcje przedostatniej strony wg Standardów (rozdz. „Dane do kontaktu”, „Oświadczenie”)
    i wizualizacji referencyjnej COI: dane kontaktowe z MLPZ (edytowalne) i sekcja „Zgoda”.

    Bez numeru kierunkowego, ePUAP i e-Doręczeń - konfiguracja sekcji w Standardach przewiduje
    wyłącznie „Numer telefonu” i „Adres e-mail” (oba niewymagane, zasilane danymi inicjalnymi).
    Oświadczenia wyekstrahowane z dokumentu enricher dokleja do tej samej sekcji zgód.
    """
    return [
        SectionDefinition(
            section_id="dane_do_kontaktu",
            title="Dane do kontaktu",
            order=1,
            description=(
                "Wpisanie danych do kontaktu może przyspieszyć załatwienie sprawy. Urzędnik będzie mógł "
                "skontaktować się z Tobą w przypadku pytań lub wątpliwości.\n"
                "Jeśli widzisz uzupełnione dane, sprawdź, czy są poprawne. Możesz je zmienić lub usunąć."
            ),
            fields=[
                _field(
                    "telefon",
                    "Numer telefonu",
                    initial_value="MLPZ.phoneNumber",
                    default_value_type="EXTERNAL_SYSTEM",
                ),
                _field(
                    "email",
                    "Adres e-mail",
                    validator=CustomValidator.EMAIL,
                    initial_value="MLPZ.email",
                    default_value_type="EXTERNAL_SYSTEM",
                ),
            ],
        ),
        SectionDefinition(
            section_id="zgody",
            title="Zgoda",
            order=2,
            fields=[
                _field(
                    "zgody_oswiadczenia",
                    "Zgoda",
                    FieldType.CONSENT_SECTION,
                    required=True,
                    consents=[ConsentItem(description=CONSENT_PERSONAL_DATA, required=True)],
                ),
            ],
        ),
    ]
