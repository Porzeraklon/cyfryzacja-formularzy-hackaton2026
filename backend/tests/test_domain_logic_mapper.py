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
        assert {"urzad", "podmiot_wniosku", "twoje_dane", "dane_do_kontaktu", "zgody"} <= ids

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
