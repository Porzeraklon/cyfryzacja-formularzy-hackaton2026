"""Regresje DomainLogicEnricher.enrich — deterministyczne wstawienie sekcji ze
Standardów COI, dedup stron pokrywających się z predefiniowanymi, kolizje tytułów."""

from datetime import UTC, datetime

from backend.app.modules.domain_logic.mapper import (
    APPLICANT_PAGE_TITLE,
    CONTACT_PAGE_TITLE,
    DomainLogicEnricher,
)
from backend.app.schemas.form_schema import (
    FieldDefinition,
    FieldType,
    FormDocumentSchema,
    FormMetadata,
    PageDefinition,
    SectionDefinition,
)


def _raw_form(*pages: PageDefinition) -> FormDocumentSchema:
    return FormDocumentSchema(
        metadata=FormMetadata(
            form_id="form-test",
            title="Wniosek testowy",
            source_filename="wniosek.pdf",
            source_format="pdf",
            generated_at=datetime.now(UTC),
        ),
        pages=list(pages),
    )


def _thematic_page(title: str, field_id: str = "kwota") -> PageDefinition:
    return PageDefinition(
        page_id="page_sprawa",
        title=title,
        order=1,
        sections=[
            SectionDefinition(
                section_id="sec_sprawa",
                title="Przedmiot wniosku",
                order=1,
                fields=[
                    FieldDefinition(
                        field_id=field_id, name=field_id, label="Kwota", field_type=FieldType.NUMBER
                    )
                ],
            )
        ],
    )


def _section_ids(form: FormDocumentSchema) -> set[str]:
    return {s.section_id for p in form.pages for s in p.sections}


class TestEnrich:
    def test_injects_standard_pages(self, settings):
        enricher = DomainLogicEnricher(settings.DICTIONARIES_DIR)
        result = enricher.enrich(_raw_form(_thematic_page("Przedmiot wniosku")))
        titles = [p.title for p in result.pages]
        assert titles[0] == APPLICANT_PAGE_TITLE
        assert titles[-1] == CONTACT_PAGE_TITLE
        assert "Przedmiot wniosku" in titles
        ids = _section_ids(result)
        assert {
            "urzad_do_ktorego_jest_skladany_wniosek",
            "podmiot_wniosku",
            "twoje_dane",
            "dane_do_kontaktu",
            "zgody",
        } <= ids

    def test_deterministic(self, settings):
        enricher = DomainLogicEnricher(settings.DICTIONARIES_DIR)
        raw = _raw_form(_thematic_page("Przedmiot wniosku"))
        first = enricher.enrich(raw).model_dump(mode="json")
        second = enricher.enrich(raw).model_dump(mode="json")
        assert first == second

    def test_dedups_applicant_page(self, settings):
        enricher = DomainLogicEnricher(settings.DICTIONARIES_DIR)
        raw = _raw_form(
            PageDefinition(page_id="page_llm", title=APPLICANT_PAGE_TITLE, order=1),
            _thematic_page("Przedmiot wniosku"),
        )
        result = enricher.enrich(raw)
        assert [p.title for p in result.pages].count(APPLICANT_PAGE_TITLE) == 1

    def test_title_collision_suffix(self, settings):
        enricher = DomainLogicEnricher(settings.DICTIONARIES_DIR)
        raw = _raw_form(_thematic_page("Załączniki", "a"), _thematic_page("Załączniki", "b"))
        result = enricher.enrich(raw)
        middle = [p.title for p in result.pages if p.title not in {APPLICANT_PAGE_TITLE, CONTACT_PAGE_TITLE}]
        assert middle == ["Załączniki", "Załączniki (2)"]


def _section(form: FormDocumentSchema, section_id: str) -> SectionDefinition:
    return next(s for p in form.pages for s in p.sections if s.section_id == section_id)


class TestStandardsLastPage:
    """Przedostatnia strona wg Standardów („Dane do kontaktu”, „Oświadczenie”) i wizualizacji COI."""

    def test_contact_section_matches_standards(self, settings):
        result = DomainLogicEnricher(settings.DICTIONARIES_DIR).enrich(_raw_form(_thematic_page("Sprawa")))
        contact = _section(result, "dane_do_kontaktu")
        assert "Możesz je zmienić lub usunąć." in contact.description
        assert [(f.label, f.required, f.initial_value, f.read_only) for f in contact.fields] == [
            ("Numer telefonu", False, "MLPZ.phoneNumber", False),
            ("Adres e-mail", False, "MLPZ.email", False),
        ]
        assert contact.fields[1].validation_rules.custom_validator.value == "EMAIL"
        consent = _section(result, "zgody")
        assert consent.title == "Zgoda"
        assert [c.description for c in consent.fields[0].consents] == [
            "Wyrażam zgodę na przetwarzanie moich danych osobowych zgodnie z klauzulą informacyjną"
        ]

    def test_document_statements_move_to_consent_section(self, settings):
        page = _thematic_page("Sprawa")
        page.sections.append(
            SectionDefinition(
                section_id="sec_osw",
                title="Oświadczenia",
                order=2,
                fields=[
                    FieldDefinition(
                        field_id="osw_1",
                        name="osw_1",
                        label="Oświadczam, że dane podane we wniosku są zgodne z prawdą.",
                        field_type=FieldType.CHECKBOX,
                        required=True,
                    )
                ],
            )
        )
        result = DomainLogicEnricher(settings.DICTIONARIES_DIR).enrich(_raw_form(page))
        assert "sec_osw" not in _section_ids(result)
        consent = _section(result, "zgody")
        assert consent.title == "Zgody i oświadczenia"
        descriptions = [c.description for c in consent.fields[0].consents]
        assert "Oświadczam, że dane podane we wniosku są zgodne z prawdą." in descriptions
        assert len(descriptions) == 2
        assert result.pages[-1].title == CONTACT_PAGE_TITLE


def test_mock_page_two_matches_reference(client):
    """Strona 2 mocka 1:1 z wizualizacją „Wniosek o stwierdzenie nadpłaty podatku”."""
    import io

    from backend.tests.conftest import PDF_BYTES

    body = client.post(
        "/api/v1/forms/convert", files={"file": ("w.pdf", io.BytesIO(PDF_BYTES), "application/pdf")}
    ).json()
    tab = body["form_definition"]["tabs"][1]
    sections = [q["field"]["title"] for q in tab["questions"]]
    assert sections == ["Informacje o nadpłacie", "Rozliczenie nadpłaty", "Korekta deklaracji"]
    labels = [c["field"].get("title") for q in tab["questions"] for c in q["field"]["repetingSectionGrid"]]
    assert "Wybierz, co mamy zrobić z nadpłatą" in labels and "Plik deklaracji podatkowej" in labels
    shown = {
        c["field"]["title"]
        for q in tab["questions"]
        for c in q["field"]["repetingSectionGrid"]
        if c["field"].get("states")
    }
    assert shown == {"Nazwa podatku", "Numer rachunku"}
