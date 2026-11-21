"""Logika domenowa: DomainLogicEnricher (#1) i sekcje predefiniowane ze Standardów (#8)."""

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import REPO_ROOT
from backend.app.modules.domain_logic import standard_sections as std
from backend.app.modules.domain_logic.mapper import DomainLogicEnricher
from backend.app.schemas.form_schema import (
    CustomValidator,
    DictionaryItem,
    FieldDefinition,
    FieldType,
    FlowAction,
    FormDocumentSchema,
    FormMetadata,
    PageDefinition,
    PageType,
    SectionDefinition,
)
from backend.app.services.dictionary_service import DictionaryService
from backend.app.services.form_consistency import validate_form_consistency
from backend.app.services.mock_form import build_mock_form
from backend.tests.conftest import PDF_BYTES

DICTIONARIES_DIR = REPO_ROOT / "data" / "dictionaries"


def _f(field_id: str, label: str, field_type: FieldType = FieldType.TEXT, **extra) -> FieldDefinition:
    return FieldDefinition(field_id=field_id, name=field_id, label=label, field_type=field_type, **extra)


def _opts(*labels: str) -> list[DictionaryItem]:
    return [DictionaryItem(code=f"o{i}", label=label) for i, label in enumerate(labels, 1)]


def llm_form() -> FormDocumentSchema:
    """Typowy wynik ekstraktora: 2 strony, dane wnioskodawcy, adres, sprawa z „inne”, oświadczenia."""
    return FormDocumentSchema(
        metadata=FormMetadata(
            form_id="wniosek",
            title="Wniosek o wydanie zaświadczenia",
            source_filename="wniosek.pdf",
            source_format="pdf",
            generated_at=datetime.now(UTC),
        ),
        pages=[
            PageDefinition(
                page_id="page_1",
                title="Strona 1",
                order=1,
                sections=[
                    SectionDefinition(
                        section_id="sec_1_1",
                        title="Dane wnioskodawcy",
                        order=1,
                        fields=[
                            _f("imie", "Imię", required=True),
                            _f("nazwisko", "Nazwisko", required=True),
                            _f("pesel", "PESEL"),
                            _f("data_urodzenia", "Data urodzenia"),
                        ],
                    ),
                    SectionDefinition(
                        section_id="sec_1_2",
                        title="Adres zamieszkania",
                        order=2,
                        fields=[
                            _f("wojewodztwo", "Województwo"),
                            _f("kod", "Kod pocztowy"),
                            _f("ulica", "Ulica"),
                        ],
                    ),
                ],
            ),
            PageDefinition(
                page_id="page_2",
                title="Strona 2",
                order=2,
                sections=[
                    SectionDefinition(
                        section_id="sec_2_1",
                        title="Przedmiot wniosku",
                        order=1,
                        fields=[
                            _f("cel", "Cel wydania", FieldType.SELECT, options=_opts("Sąd", "Bank", "Inny")),
                            _f("cel_inny", "Jaki inny cel?"),
                            _f("plec", "Płeć", FieldType.RADIO, options=_opts("Kobieta", "Mężczyzna")),
                            _f("kwota", "Kwota opłaty"),
                            _f("nip", "NIP pracodawcy"),
                            _f("kod_old", "Kod", validation_rules={"custom_validator": "POSTAL_CODE"}),
                            _f("tel_old", "Tel.", validation_rules={"custom_validator": "PL_PHONE"}),
                            _f("o1", "Oświadczam, że dane są prawdziwe.", FieldType.CHECKBOX, required=True),
                            _f("o2", "Wyrażam zgodę na kontakt telefoniczny.", FieldType.CHECKBOX),
                        ],
                    )
                ],
            ),
        ],
    )


@pytest.fixture(scope="module")
def enricher() -> DomainLogicEnricher:
    return DomainLogicEnricher(DICTIONARIES_DIR)


@pytest.fixture(scope="module")
def known_ids(settings_module) -> set[str]:
    return DictionaryService(settings_module).known_dictionary_ids()


@pytest.fixture(scope="module")
def settings_module():
    from backend.app.core.config import Settings

    return Settings(DICTIONARIES_DIR=DICTIONARIES_DIR, USE_MOCK_PIPELINE=True)


@pytest.fixture(scope="module")
def enriched(enricher) -> FormDocumentSchema:
    return enricher.enrich(llm_form())


def _fields(form: FormDocumentSchema) -> dict[str, FieldDefinition]:
    return {f.field_id: f for p in form.pages for s in p.sections for f in s.fields}


def _predefined(form: FormDocumentSchema) -> list[str]:
    return [s.predefined for p in form.pages for s in p.sections if s.predefined]


class TestStructure:
    def test_consistent(self, enriched, known_ids):
        errors, _warnings = validate_form_consistency(enriched, known_ids)
        assert errors == []

    def test_standard_sections_once_in_standards_order(self, enriched):
        assert _predefined(enriched) == [
            std.STD_OFFICE, std.STD_APPLICANT_ROLE, std.STD_APPLICANT_DATA, std.STD_ADDRESS,
            std.STD_CONTACT, std.STD_CONSENTS,
        ]
        first = [s.predefined for s in enriched.pages[0].sections]
        assert first[:4] == [std.STD_OFFICE, std.STD_APPLICANT_ROLE, std.STD_APPLICANT_DATA, std.STD_ADDRESS]

    def test_finish_page_last_with_info_only(self, enriched):
        assert [p.page_type for p in enriched.pages] == [PageType.MAIN, PageType.MAIN, PageType.FINISH]
        finish = enriched.pages[-1]
        assert all(f.field_type == FieldType.INFO_SECTION for s in finish.sections for f in s.fields)
        assert [p.order for p in enriched.pages] == [1, 2, 3]

    def test_applicant_data_from_mlpz_keeps_extra_fields(self, enriched):
        section = next(s for s in enriched.pages[0].sections if s.predefined == std.STD_APPLICANT_DATA)
        assert section.section_id == "sec_1_1"  # referencje do sekcji pozostają ważne
        mlpz = [f for f in section.fields if f.default_value_type]
        assert [f.default_value_key for f in mlpz] == ["name", "surname", "pesel"]
        assert all(f.read_only and f.default_value_type.value == "MLPZ" for f in mlpz)
        extra = section.fields[-1]
        assert extra.label == "Data urodzenia" and extra.field_type == FieldType.DATE
        assert "imie" not in _fields(enriched)  # zdublowane pole LLM zastąpione kanonicznym

    def test_address_canonical(self, enriched):
        section = next(s for p in enriched.pages for s in p.sections if s.predefined == std.STD_ADDRESS)
        refs = {f.label: f.dictionary_ref for f in section.fields}
        assert refs["Kraj"] == "dict_countries" and refs["Województwo"] == "dict_voivodeships"
        foreign = next(f for f in section.fields if f.field_id.endswith("_kod_zagraniczny"))
        assert foreign.validation_rules is None  # kanoniczne pola nie dostają heurystyk etykiet

    def test_input_not_mutated(self, enricher):
        form = llm_form()
        before = form.model_dump()
        enricher.enrich(form)
        assert form.model_dump() == before

    def test_idempotent(self, enricher, enriched):
        again = enricher.enrich(enriched)
        assert again.model_dump(exclude={"metadata"}) == enriched.model_dump(exclude={"metadata"})


class TestFields:
    def test_validators_and_types(self, enriched):
        fields = _fields(enriched)
        assert fields["nip"].validation_rules.custom_validator == CustomValidator.NIP
        assert fields["kod_old"].validation_rules.custom_validator == CustomValidator.ZIP_CODE
        assert fields["tel_old"].validation_rules.custom_validator is None
        assert fields["kwota"].field_type == FieldType.NUMBER

    def test_consents_merged(self, enriched):
        fields = _fields(enriched)
        assert "o1" not in fields and "o2" not in fields
        consent = fields["o1_zgody"]
        assert consent.field_type == FieldType.CONSENT_SECTION
        assert [c.required for c in consent.consents] == [True, False]

    def test_dictionaries_mapped_and_proposed(self, enriched):
        fields = _fields(enriched)
        assert fields["plec"].dictionary_ref == "dict_gender"
        mapped = {m.field_id: m.dictionary_id for m in enriched.dictionaries.mapped_dictionaries}
        assert mapped["plec"] == "dict_gender"
        assert set(mapped.values()) >= {"dict_countries", "dict_voivodeships", "dict_dial_codes"}
        proposed = {p.target_field_id: p for p in enriched.dictionaries.proposed_dictionaries}
        assert fields["cel"].dictionary_ref == proposed["cel"].proposed_id == "prop_cel"
        assert [i.label for i in proposed["cel"].items] == ["Sąd", "Bank", "Inny"]
        # Pola sekcji standardowych nie generują propozycji słowników.
        assert all(not p.target_field_id.startswith("w_czyim") for p in proposed.values())

    def test_other_option_flows(self, enriched):
        flows = [(f.source_field_id, f.value, f.target_id, f.action) for f in enriched.conditional_flows]
        assert ("cel", "Inny", "cel_inny", FlowAction.SHOW) in flows
        assert ("cel", "Inny", "cel_inny", FlowAction.REQUIRE) in flows

    def test_long_instruction_becomes_text_block(self, enricher):
        form = llm_form()
        form.pages[1].sections[0].fields.append(_f("pouczenie", "Pouczenie. " + "Treść pouczenia " * 20))
        out = enricher.enrich(form)
        field = _fields(out)["pouczenie"]
        assert field.field_type == FieldType.TEXT_BLOCK and field.label == "" and not field.required


class TestEdgeCases:
    def test_finish_with_inputs_moved_to_main(self, enricher, known_ids):
        form = llm_form()
        form.pages.append(
            PageDefinition(
                page_id="page_end",
                title="Koniec",
                order=3,
                page_type=PageType.FINISH,
                sections=[
                    SectionDefinition(
                        section_id="sec_end",
                        title="Uwagi",
                        order=1,
                        fields=[_f("uwagi", "Uwagi", FieldType.TEXTAREA), _f("info", "", FieldType.TEXT_BLOCK)],
                    )
                ],
            )
        )
        out = enricher.enrich(form)
        assert validate_form_consistency(out, known_ids)[0] == []
        assert out.pages[-1].page_id == "page_end"
        main_ids = {f.field_id for p in out.pages[:-1] for s in p.sections for f in s.fields}
        assert "uwagi" in main_ids

    def test_empty_form_gets_standard_pages(self, enricher, known_ids):
        form = llm_form()
        form.pages = [PageDefinition(page_id="page_1", title="Pusta", order=1)]
        out = enricher.enrich(form)
        assert len(out.pages) == 2 and out.pages[-1].page_type == PageType.FINISH
        assert validate_form_consistency(out, known_ids)[0] == []

    def test_mock_unchanged_in_size(self, enricher, known_ids):
        mock = build_mock_form("wniosek.pdf", "application/pdf")
        out = enricher.enrich(mock)
        assert validate_form_consistency(out, known_ids)[0] == []
        count = lambda form: sum(len(s.fields) for p in form.pages for s in p.sections)
        assert count(out) == count(mock)
        # Mock ma komplet sekcji standardowych - nic nie dochodzi; pozostałe adresy (innej osoby,
        # siedziby) zostają rozpoznane jako adresy kanoniczne z zachowaniem liczby pól.
        assert set(_predefined(out)) == set(_predefined(mock))
        assert _predefined(out).count(std.STD_ADDRESS) == 3

    def test_missing_dictionaries_dir(self, tmp_path: Path):
        out = DomainLogicEnricher(tmp_path / "brak").enrich(llm_form())
        assert out.pages[-1].page_type == PageType.FINISH


class TestRealPipeline:
    def test_convert_returns_200_with_fake_extractor(self, make_app, monkeypatch):
        from backend.app.modules.ai_extractor import extractor

        monkeypatch.setattr(extractor.LocalStructureExtractor, "extract", lambda self, parsed: llm_form())
        app = make_app(ENV="development", USE_MOCK_PIPELINE="false", JWT_AUTH_ENABLED="false")
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/forms/convert", files={"file": ("wniosek.pdf", io.BytesIO(PDF_BYTES), "application/pdf")}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["form"]["pages"][-1]["page_type"] == "FINISH"
        assert body["form_definition"]["tabs"][-1]["type"] == "FINISH"
        assert len(body["form_definition"]["tabs"]) >= 2
