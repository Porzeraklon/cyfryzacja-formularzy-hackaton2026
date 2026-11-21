"""Mock pipeline: zgodność z wzorem COI „Wniosek o stwierdzenie nadpłaty podatku”."""

import json
from pathlib import Path

import pytest

from backend.app.schemas.form_schema import (
    PRESENTATION_FIELD_TYPES,
    ConditionalFlow,
    CustomValidator,
    FieldType,
    FlowAction,
    FormDocumentSchema,
    PageType,
    TargetType,
)
from backend.app.services.dictionary_service import DictionaryService
from backend.app.services.form_consistency import validate_form_consistency
from backend.app.services.mock_form import FORM_TITLE, build_mock_form

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "frontend" / "examples" / "wniosek-o-stwierdzenie-nadplaty-podatku.json"

# Typ pola wewnętrznego -> typ komponentu FormDefinition.
TYPE_MAP = {
    FieldType.TEXT: "TEXT_FIELD",
    FieldType.NUMBER: "NUMBER",
    FieldType.DATE: "DATEPICKER",
    FieldType.SELECT: "SELECT",
    FieldType.RADIO: "RADIO",
    FieldType.CHECKBOX: "CHECKBOX",
    FieldType.TEXTAREA: "TEXTAREA",
    FieldType.FILE: "FILE_UPLOADER",
    FieldType.CONSENT_SECTION: "CONSENT_SECTION",
    FieldType.TEXT_BLOCK: "TEXT_BLOCK",
    FieldType.INFO_SECTION: "INFO_SECTION",
}
DICTIONARY_NAMES = {
    "dict_countries": "Kraje",
    "dict_voivodeships": "Województwo",
    "dict_districts": "Powiat",
    "dict_communities": "Gmina",
    "dict_places": "Miejscowość",
    "dict_dial_codes": "Numery kierunkowe",
}
PREDEFINED_IDS = {
    "STD_OFFICE",
    "STD_APPLICANT_ROLE",
    "STD_APPLICANT_DATA",
    "STD_ADDRESS",
    "STD_CONTACT",
    "STD_CONSENTS",
}


@pytest.fixture(scope="module")
def form() -> FormDocumentSchema:
    return build_mock_form("wniosek.pdf", "application/pdf")


@pytest.fixture(scope="module")
def template() -> dict:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _all_fields(fields):
    for field in fields:
        yield field
        if field.fields:
            yield from _all_fields(field.fields)


def _form_fields(form):
    for page in form.pages:
        for section in page.sections:
            yield from _all_fields(section.fields)


def _flows(form) -> list[ConditionalFlow]:
    return form.conditional_flows


class TestMockFormContract:
    def test_round_trip_validation(self, form):
        dumped = form.model_dump(mode="json")
        assert FormDocumentSchema.model_validate(dumped) == form

    def test_metadata(self, form):
        assert form.metadata.title == FORM_TITLE
        assert form.metadata.source_filename == "wniosek.pdf"
        assert form.metadata.source_format == "application/pdf"

    def test_consistency_without_errors(self, form, settings):
        local = settings.model_copy(update={"DICTIONARIES_DIR": REPO_ROOT / "data" / "dictionaries"})
        known_ids = DictionaryService(local).known_dictionary_ids()
        errors, _warnings = validate_form_consistency(form, known_ids)
        assert errors == []

    def test_pages_structure(self, form):
        assert len(form.pages) >= 2
        assert [p.order for p in form.pages] == list(range(1, len(form.pages) + 1))
        assert form.pages[-1].page_type == PageType.FINISH
        for page in form.pages[:-1]:
            assert page.page_type == PageType.MAIN
            assert any(section.fields for section in page.sections), page.page_id

    def test_finish_page_only_presentation_fields(self, form):
        finish = form.pages[-1]
        fields = [f for s in finish.sections for f in _all_fields(s.fields)]
        assert fields
        assert all(f.field_type in PRESENTATION_FIELD_TYPES for f in fields)

    def test_presentation_fields_have_text_and_no_rules(self, form):
        for field in _form_fields(form):
            if field.field_type in PRESENTATION_FIELD_TYPES:
                assert field.help_text, field.field_id
                assert not field.required and field.validation_rules is None

    def test_no_validators_outside_coi_catalog(self, form):
        used = {
            f.validation_rules.custom_validator
            for f in _form_fields(form)
            if f.validation_rules and f.validation_rules.custom_validator
        }
        assert CustomValidator.PL_PHONE not in used
        assert CustomValidator.POSTAL_CODE not in used
        assert {"PESEL", "NIP", "REGON", "ZIP_CODE", "EMAIL", "EPUAP", "ADE", "SERIAL_NUMBER_ID_CARD"} == {
            v.value for v in used
        }

    def test_names_are_unique_snake_case(self, form):
        names = [f.name for f in _form_fields(form)]
        assert len(names) == len(set(names))
        assert all(name == name.lower() and " " not in name for name in names)

    def test_predefined_sections(self, form):
        predefined = [s.predefined for p in form.pages for s in p.sections if s.predefined]
        assert sorted(predefined) == sorted(PREDEFINED_IDS)

    def test_initial_data_from_mlpz(self, form):
        fields = {f.field_id: f for f in _form_fields(form)}
        for field_id, key in (("imie", "name"), ("nazwisko", "surname"), ("pesel", "pesel")):
            field = fields[field_id]
            assert field.read_only
            assert field.default_value_type.value == "MLPZ"
            assert field.default_value_key == key

    def test_consent_section(self, form):
        consent = next(f for f in _form_fields(form) if f.field_type == FieldType.CONSENT_SECTION)
        assert consent.required
        assert len(consent.consents) == 2
        assert all(c.required and c.description for c in consent.consents)

    def test_repeatable_group_has_columns(self, form):
        group = next(f for f in _form_fields(form) if f.field_type == FieldType.REPEATABLE_GROUP)
        assert [f.name for f in group.fields] == ["okres_rok", "okres_data_deklaracji", "okres_kwota"]

    def test_dictionaries_block(self, form):
        fields = {f.field_id: f for f in _form_fields(form)}
        mapped = form.dictionaries.mapped_dictionaries
        assert mapped
        for entry in mapped:
            assert fields[entry.field_id].dictionary_ref == entry.dictionary_id
            assert entry.dictionary_name == DICTIONARY_NAMES[entry.dictionary_id]
        dictionary_fields = {f.field_id for f in fields.values() if f.dictionary_ref in DICTIONARY_NAMES}
        assert dictionary_fields == {entry.field_id for entry in mapped}
        assert len(form.dictionaries.proposed_dictionaries) == 1
        proposed = form.dictionaries.proposed_dictionaries[0]
        assert fields[proposed.target_field_id].options == proposed.items

    def test_flows_reference_option_labels(self, form):
        fields = {f.field_id: f for f in _form_fields(form)}
        for flow in _flows(form):
            source = fields[flow.source_field_id]
            if source.options:
                assert flow.value in {o.label for o in source.options}, flow
        assert {f.action for f in _flows(form)} <= {FlowAction.SHOW, FlowAction.OPTIONAL}

    def test_flows_cover_rules_and_dependencies(self, form):
        embedded = []
        for page in form.pages:
            for section in page.sections:
                embedded.extend(section.visibility_rules)
                for field in _all_fields(section.fields):
                    embedded.extend(field.dependencies)
        assert embedded == form.conditional_flows


class TestMockMatchesTemplate:
    """Porównanie z gotowym FormDefinition (frontend/examples) - źródło prawdy wzoru."""

    def test_title_and_tabs(self, form, template):
        assert form.metadata.title == template["title"]
        assert [p.title for p in form.pages] == [t["title"] for t in template["tabs"]]
        assert [p.page_type.value for p in form.pages] == [t["type"] for t in template["tabs"]]
        assert [len(p.sections) for p in form.pages] == [len(t["questions"]) for t in template["tabs"]]

    def test_sections_and_fields(self, form, template):
        for page, tab in zip(form.pages, template["tabs"], strict=True):
            for section, question in zip(page.sections, tab["questions"], strict=True):
                qfield = question["field"]
                assert section.title == qfield["title"]
                fields = section.fields
                if qfield["type"] == "REPEATING_SECTION":
                    assert len(fields) == 1 and fields[0].field_type == FieldType.REPEATABLE_GROUP
                    assert fields[0].name == qfield["techName"]
                    fields = fields[0].fields
                else:
                    assert section.section_id == qfield["techName"]
                assert (section.description or "") == qfield.get("description", "")
                grid = [cell["field"] for cell in qfield["repetingSectionGrid"]]
                assert [f.name for f in fields] == [g["techName"] for g in grid]
                for field, expected in zip(fields, grid, strict=True):
                    self._assert_field(field, expected)

    @staticmethod
    def _assert_field(field, expected):
        name = field.name
        assert TYPE_MAP[field.field_type] == expected["type"], name
        assert field.label == expected.get("title", ""), name
        assert (field.help_text or "") == expected.get("description", ""), name
        assert (field.placeholder or "") == expected.get("tooltipMessage", ""), name
        validation = set(expected.get("validation", []))
        assert field.required == ("REQUIRED" in validation), name
        custom = field.validation_rules.custom_validator if field.validation_rules else None
        assert ({custom.value} if custom else set()) == validation - {"REQUIRED"}, name
        assert field.read_only == expected.get("readOnly", False), name
        if expected.get("defaultValueType") == "EXTERNAL_SYSTEM":
            assert f"{field.default_value_type.value}.{field.default_value_key}" == expected["defaultValue"]
        else:
            assert field.default_value_type is None
            assert (field.initial_value or "") == expected.get("defaultValue", ""), name
        if expected.get("dictionaryName"):
            assert DICTIONARY_NAMES[field.dictionary_ref] == expected["dictionaryName"], name
        if expected.get("options"):
            assert [o.label for o in field.options] == [o["name"] for o in expected["options"]], name
            assert [o.code for o in field.options] == [o["techName"] for o in expected["options"]], name
        if expected["type"] == "CONSENT_SECTION":
            consents = expected["consentSectionConfig"]["consents"]
            assert [c.model_dump() for c in field.consents] == consents

    def test_show_states_have_flows(self, form, template):
        """Każdy element ze stanem SHOW we wzorze ma regułę SHOW w mocku (i odwrotnie)."""
        expected: set[str] = set()
        for tab in template["tabs"]:
            for question in tab["questions"]:
                qfield = question["field"]
                if any(s["type"] == "SHOW" for s in qfield.get("states", [])):
                    expected.add(qfield["techName"])
                for cell in qfield["repetingSectionGrid"]:
                    if any(s["type"] == "SHOW" for s in cell["field"].get("states", [])):
                        expected.add(cell["field"]["techName"])
        targets = set()
        for flow in form.conditional_flows:
            if flow.action != FlowAction.SHOW:
                continue
            assert flow.target_type in (TargetType.FIELD, TargetType.SECTION)
            targets.add(flow.target_id)
        assert targets == expected
