"""FormDefinition (oficjalny format importu e-Usług COI): model, eksport, walidacja i endpointy."""

import io
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.exceptions import PipelineNotReadyError
from backend.app.schemas.form_definition import FormDefinition
from backend.app.schemas.form_schema import (
    ConditionalFlow,
    ConditionOperator,
    ConsentItem,
    CustomValidator,
    DictionaryItem,
    FieldDefinition,
    FieldType,
    FlowAction,
    FormDocumentSchema,
    FormMetadata,
    InitialDataSource,
    PageDefinition,
    PageType,
    SectionDefinition,
    TargetType,
    ValidationRules,
)
from backend.app.services.dictionary_service import DictionaryService
from backend.app.services.form_consistency import validate_form_consistency
from backend.app.services.form_definition_export import (
    INITIAL_DATA_ATTRIBUTES,
    export_form_definition,
    find_official_dictionary,
    slugify,
)
from backend.app.services.form_definition_validation import (
    check_jsonata_syntax,
    has_errors,
    validate_form_definition,
)
from backend.app.services.pipeline_service import RealFormPipeline
from backend.tests.conftest import PDF_BYTES

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PATH = REPO_ROOT / "frontend" / "examples" / "wniosek-o-stwierdzenie-nadplaty-podatku.json"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

KNOWN_DICTIONARIES = {
    "dict_countries": [DictionaryItem(code="PL", label="Polska"), DictionaryItem(code="DE", label="Niemcy")],
    "dict_gender": [DictionaryItem(code="K", label="Kobieta"), DictionaryItem(code="M", label="Mężczyzna")],
}


# ---------------------------------------------------------------------------------------------
# Pomocnicze
# ---------------------------------------------------------------------------------------------


def _field(field_id: str, label: str, field_type: FieldType = FieldType.TEXT, **kwargs) -> FieldDefinition:
    return FieldDefinition(field_id=field_id, name=field_id, label=label, field_type=field_type, **kwargs)


def _metadata(form_id: str = "form-test", title: str = "Wniosek o testowy formularz") -> FormMetadata:
    return FormMetadata(
        form_id=form_id,
        title=title,
        source_filename="wniosek.pdf",
        source_format="pdf",
        generated_at=datetime(2026, 11, 21, tzinfo=UTC),
    )


def build_small_form(form_id: str = "form-test", with_finish: bool = True, title: str | None = None):
    own_data = SectionDefinition(
        section_id="sec_own",
        title="Twoje dane",
        order=1,
        fields=[
            _field("imie", "Imię", required=True),
            _field("nazwisko", "Nazwisko", required=True),
            _field(
                "pesel",
                "Numer PESEL",
                required=True,
                validation_rules=ValidationRules(custom_validator=CustomValidator.PESEL),
            ),
            _field("plec", "Płeć", FieldType.RADIO, dictionary_ref="dict_gender", initial_value="K"),
        ],
    )
    address = SectionDefinition(
        section_id="sec_address",
        title="Adres zamieszkania",
        order=2,
        fields=[
            _field("kraj", "Kraj", FieldType.SELECT, required=True, dictionary_ref="dict_countries"),
            _field(
                "wojewodztwo",
                "Województwo",
                FieldType.SELECT,
                required=True,
                dictionary_ref="dict_voivodeships",
                dependencies=[
                    ConditionalFlow(
                        source_field_id="kraj",
                        operator=ConditionOperator.EQUALS,
                        value="PL",
                        target_type=TargetType.FIELD,
                        target_id="wojewodztwo",
                        action=FlowAction.SHOW,
                    )
                ],
            ),
            _field("ulica", "Ulica", required=True),
            _field("brak_ulicy", "Brak nazwy ulicy", FieldType.CHECKBOX),
            _field(
                "kod",
                "Kod pocztowy",
                required=True,
                placeholder="np. 00-950",
                validation_rules=ValidationRules(custom_validator=CustomValidator.POSTAL_CODE),
            ),
            _field(
                "telefon",
                "Telefon",
                validation_rules=ValidationRules(custom_validator=CustomValidator.PL_PHONE, max_length=15),
            ),
            _field("email", "Adres e-mail"),
            _field(
                "ulica_meldunek",
                "Ulica (meldunek)",
                default_value_type=InitialDataSource.PESEL_ADDRESS,
                default_value_key="permanentAddressStreet",
                read_only=True,
            ),
        ],
    )
    details = SectionDefinition(
        section_id="sec_details",
        title="Szczegóły",
        order=1,
        fields=[
            _field("instrukcja", "Podaj okresy nadpłaty.", FieldType.TEXT_BLOCK),
            _field("kwota", "Kwota nadpłaty", FieldType.NUMBER, required=True),
            _field(
                "okresy",
                "Okresy nadpłaty",
                FieldType.REPEATABLE_GROUP,
                fields=[
                    _field("okres_rok", "Rok", FieldType.NUMBER, required=True),
                    _field("okres_kwota", "Kwota", FieldType.NUMBER),
                ],
            ),
            _field(
                "zgody",
                "Zgody i oświadczenia",
                FieldType.CONSENT_SECTION,
                required=True,
                consents=[
                    ConsentItem(description="Oświadczam, że dane są prawdziwe.", required=True),
                    ConsentItem(description="Wyrażam zgodę na kontakt e-mail.", required=False),
                ],
            ),
        ],
    )
    pages = [
        PageDefinition(page_id="page_applicant", title="Dane wnioskodawcy", order=1, sections=[own_data, address]),
        PageDefinition(page_id="page_details", title="Nadpłata", order=2, sections=[details]),
    ]
    if with_finish:
        pages.append(
            PageDefinition(
                page_id="page_finish",
                title="Podsumowanie",
                order=3,
                page_type=PageType.FINISH,
                sections=[
                    SectionDefinition(
                        section_id="sec_summary",
                        title="Co dalej",
                        order=1,
                        fields=[_field("co_dalej", "Urząd rozpatrzy wniosek.", FieldType.INFO_SECTION)],
                    )
                ],
            )
        )
    return FormDocumentSchema(
        metadata=_metadata(form_id, title or "Wniosek o testowy formularz"),
        pages=pages,
        conditional_flows=[
            ConditionalFlow(
                source_field_id="brak_ulicy",
                operator=ConditionOperator.EQUALS,
                value=True,
                target_type=TargetType.FIELD,
                target_id="ulica",
                action=FlowAction.HIDE,
            ),
            ConditionalFlow(
                source_field_id="brak_ulicy",
                operator=ConditionOperator.EQUALS,
                value=True,
                target_type=TargetType.FIELD,
                target_id="ulica",
                action=FlowAction.OPTIONAL,
            ),
        ],
    )


def _fields(fd: FormDefinition) -> dict[str, dict]:
    """techName -> pole (dict JSON) dla wszystkich pól i sekcji."""
    out: dict[str, dict] = {}
    for tab in fd.model_dump(mode="json")["tabs"]:
        for question in tab["questions"]:
            out[question["field"]["techName"]] = question["field"]
            for child in question["field"].get("repetingSectionGrid", []):
                out[child["field"]["techName"]] = child["field"]
    return out


def _all_ids(data: dict) -> list[str]:
    ids = [tab["id"] for tab in data["tabs"]] + [link["id"] for link in data["flow"]["links"]]
    for tab in data["tabs"]:
        for question in tab["questions"]:
            ids.append(question["field"]["id"])
            for child in question["field"].get("repetingSectionGrid", []):
                ids.append(child["field"]["id"])
                ids.extend(option["id"] for option in child["field"].get("options", []))
    return ids


@pytest.fixture()
def example() -> dict:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def exported():
    return export_form_definition(build_small_form(), KNOWN_DICTIONARIES)


# ---------------------------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------------------------


class TestModel:
    def test_official_example_validates_and_roundtrips(self, example):
        fd = FormDefinition.model_validate(example)
        assert fd.model_dump(mode="json") == example
        assert json.loads(fd.model_dump_json()) == example

    def test_official_example_has_no_structural_issues(self, example):
        assert validate_form_definition(example) == []
        assert validate_form_definition(FormDefinition.model_validate(example)) == []

    def test_extra_keys_forbidden(self, example):
        example["tabs"][0]["questions"][0]["field"]["nieznany"] = 1
        with pytest.raises(ValueError):
            FormDefinition.model_validate(example)

    def test_accepts_repeating_spelling_and_emits_schema_spelling(self):
        fd = FormDefinition.model_validate(
            {"tabs": [{"questions": [{"field": {"type": "SECTION", "repeatingSectionGrid": []}}]}]}
        )
        dumped = fd.model_dump(mode="json")
        assert "repetingSectionGrid" in dumped["tabs"][0]["questions"][0]["field"]
        assert "repeatingSectionGrid" not in dumped["tabs"][0]["questions"][0]["field"]

    def test_none_values_omitted(self):
        dumped = FormDefinition(title="Wniosek").model_dump(mode="json")
        assert dumped == {"title": "Wniosek", "tabs": []}

    def test_default_value_may_be_list(self):
        fd = FormDefinition.model_validate(
            {"tabs": [{"questions": [{"field": {"type": "SELECT", "defaultValue": ["a", "b"]}}]}]}
        )
        assert fd.tabs[0].questions[0].field.default_value == ["a", "b"]


# ---------------------------------------------------------------------------------------------
# Eksport
# ---------------------------------------------------------------------------------------------


class TestExport:
    def test_structure_tabs_and_links(self, exported):
        fd, _notes = exported
        assert [tab.type for tab in fd.tabs] == ["MAIN", "MAIN", "FINISH"]
        assert fd.title == "Wniosek o testowy formularz"
        links = fd.flow.links
        assert [(link.source, link.target) for link in links] == [
            (fd.tabs[0].id, fd.tabs[1].id),
            (fd.tabs[1].id, fd.tabs[2].id),
        ]
        for tab in fd.tabs:
            for question in tab.questions:
                assert question.field.type in ("SECTION", "REPEATING_SECTION")

    def test_exported_form_has_no_structural_errors(self, exported):
        fd, _notes = exported
        issues = validate_form_definition(fd)
        assert not has_errors(issues), [i.message for i in issues if i.level == "error"]

    def test_type_mapping(self, exported):
        fields = _fields(exported[0])
        assert fields["imie"]["type"] == "TEXT_FIELD"
        assert fields["kraj"]["type"] == "SELECT"
        assert fields["plec"]["type"] == "RADIO"
        assert fields["brak_ulicy"]["type"] == "CHECKBOX"
        assert fields["kwota"]["type"] == "NUMBER"
        assert fields["instrukcja"]["type"] == "TEXT_BLOCK"
        assert fields["zgody"]["type"] == "CONSENT_SECTION"

    def test_validators(self, exported):
        fd, notes = exported
        fields = _fields(fd)
        assert fields["pesel"]["validation"] == ["REQUIRED", "PESEL"]
        assert fields["kod"]["validation"] == ["REQUIRED", "ZIP_CODE"]
        assert fields["telefon"]["validation"] == []
        assert any("PL_PHONE" in note for note in notes)
        assert fields["email"]["validation"] == ["EMAIL"]  # heurystyka etykiety (TEXT_FIELD)
        assert fields["kwota"]["validation"] == ["REQUIRED"]
        assert any("długości/regex" in note for note in notes)
        assert fields["kod"]["tooltipMessage"] == "Np. 00-950"

    def test_presentation_fields_have_no_validation(self, exported):
        fields = _fields(exported[0])
        assert "validation" not in fields["instrukcja"]
        assert fields["instrukcja"]["description"] == "Podaj okresy nadpłaty."
        assert "validation" not in fields["co_dalej"]

    def test_dictionaries(self, exported):
        fields = _fields(exported[0])
        assert fields["kraj"]["dictionaryName"] == "Kraje"
        assert fields["kraj"]["options"] == []
        assert fields["kraj"]["searchEnabled"] is True
        assert fields["kraj"]["defaultValue"] == "Polska"
        assert fields["wojewodztwo"]["dictionaryName"] == "Województwo"
        # Słownik spoza katalogu COI -> opcje pola; initial_value (kod) -> nazwa opcji.
        assert "dictionaryName" not in fields["plec"]
        assert [option["name"] for option in fields["plec"]["options"]] == ["Kobieta", "Mężczyzna"]
        assert [option["techName"] for option in fields["plec"]["options"]] == ["k", "m"]
        assert fields["plec"]["defaultValue"] == "Kobieta"

    def test_non_coi_dictionary_without_items_noted(self):
        _fd, notes = export_form_definition(build_small_form(), {})
        assert any("dict_gender" in note and "nie ma pozycji" in note for note in notes)

    def test_official_dictionary_on_radio_becomes_select(self):
        form = build_small_form()
        field = form.pages[0].sections[1].fields[1]
        form.pages[0].sections[1].fields[1] = field.model_copy(update={"field_type": FieldType.RADIO})
        fd, notes = export_form_definition(form, KNOWN_DICTIONARIES)
        assert _fields(fd)["wojewodztwo"]["type"] == "SELECT"
        assert any("Województwo" in note and "SELECT" in note for note in notes)

    def test_label_dictionary_heuristic_on_text_field(self):
        form = build_small_form()
        form.pages[0].sections[1].fields.append(_field("powiat", "Powiat"))
        fd, _notes = export_form_definition(form, KNOWN_DICTIONARIES)
        powiat = _fields(fd)["powiat"]
        assert powiat["type"] == "SELECT"
        assert powiat["dictionaryName"] == "Powiat"

    def test_initial_data(self, exported):
        fields = _fields(exported[0])
        assert fields["ulica_meldunek"]["readOnly"] is True
        assert fields["ulica_meldunek"]["defaultValue"] == "PESEL_ADDRESS.permanentAddressStreet"
        assert fields["ulica_meldunek"]["defaultValueType"] == "EXTERNAL_SYSTEM"
        # Heurystyka "Twoje dane" -> MLPZ, tylko do odczytu.
        assert fields["imie"]["defaultValue"] == "MLPZ.name"
        assert fields["imie"]["readOnly"] is True
        assert fields["pesel"]["defaultValue"] == "MLPZ.pesel"
        assert fields["ulica"]["readOnly"] is False
        assert fields["ulica"]["defaultValueType"] == ""

    def test_explicit_initial_data_not_overridden_by_heuristic(self):
        form = build_small_form()
        own = form.pages[0].sections[0]
        own.fields[0] = own.fields[0].model_copy(
            update={"default_value_type": InitialDataSource.PESEL, "default_value_key": "name"}
        )
        fd, _notes = export_form_definition(form, KNOWN_DICTIONARIES)
        assert _fields(fd)["imie"]["defaultValue"] == "PESEL.name"

    def test_repeating_section_at_page_level(self, exported):
        fd, notes = exported
        tab = fd.tabs[1]
        repeating = [q.field for q in tab.questions if q.field.type == "REPEATING_SECTION"]
        assert len(repeating) == 1
        assert repeating[0].title == "Okresy nadpłaty"
        assert [c.field.tech_name for c in repeating[0].repeting_section_grid] == ["okres_rok", "okres_kwota"]
        assert any("REPEATING_SECTION" in note for note in notes)

    def test_consent_section(self, exported):
        consent = _fields(exported[0])["zgody"]
        assert consent["validation"] == ["REQUIRED"]
        assert consent["selectAllEnabled"] is True
        assert consent["consentSectionConfig"] == {
            "consents": [
                {"description": "Oświadczam, że dane są prawdziwe.", "required": True},
                {"description": "Wyrażam zgodę na kontakt e-mail.", "required": False},
            ],
            "required": True,
            "files": [],
        }

    def test_consent_like_checkboxes_become_consent_section(self):
        form = build_small_form()
        form.pages[1].sections.append(
            SectionDefinition(
                section_id="sec_oswiadczenia",
                title="Oświadczenia",
                order=2,
                fields=[
                    _field("osw1", "Oświadczam, że dane są prawdziwe", FieldType.CHECKBOX, required=True),
                    _field("osw2", "Wyrażam zgodę na przetwarzanie danych", FieldType.CHECKBOX),
                ],
            )
        )
        fd, notes = export_form_definition(form, KNOWN_DICTIONARIES)
        consent = _fields(fd)["sec_oswiadczenia_zgody"]
        assert consent["type"] == "CONSENT_SECTION"
        assert len(consent["consentSectionConfig"]["consents"]) == 2
        assert any("CONSENT_SECTION" in note for note in notes)

    def test_states_jsonata(self, exported):
        fd, notes = exported
        fields = _fields(fd)
        show = fields["wojewodztwo"]["states"]
        assert show == [
            {
                "type": "SHOW",
                "query": "\"Polska\" in tabs['Dane wnioskodawcy' in title]"
                ".questions['adres_zamieszkania' in field.techName]"
                ".field.repetingSectionGrid['kraj' in field.techName].field.__userAnswer",
            }
        ]
        hide = fields["ulica"]["states"]
        assert len(hide) == 1
        assert hide[0]["query"].startswith("$not(tabs['Dane wnioskodawcy' in title]")
        assert hide[0]["query"].endswith("['brak_ulicy' in field.techName].field.__userAnswer = true)")
        assert any("OPTIONAL" in note for note in notes)
        assert all(check_jsonata_syntax(state["query"]) is None for state in show + hide)

    def test_operators(self):
        form = build_small_form()
        flows = [
            (ConditionOperator.NOT_EQUALS, "K", '$not("Kobieta" in '),
            (ConditionOperator.IN, ["K", "M"], '("Kobieta" in '),
            (ConditionOperator.IS_EMPTY, None, "($not($exists("),
        ]
        for operator, value, _prefix in flows:
            form.conditional_flows.append(
                ConditionalFlow(
                    source_field_id="plec",
                    operator=operator,
                    value=value,
                    target_type=TargetType.SECTION,
                    target_id="sec_address",
                    action=FlowAction.SHOW,
                )
            )
        form.conditional_flows.append(
            ConditionalFlow(
                source_field_id="kwota",
                operator=ConditionOperator.GREATER_THAN,
                value="1000",
                target_type=TargetType.FIELD,
                target_id="okresy",
                action=FlowAction.SHOW,
            )
        )
        fd, _notes = export_form_definition(form, KNOWN_DICTIONARIES)
        fields = _fields(fd)
        queries = [state["query"] for state in fields["adres_zamieszkania"]["states"]]
        for (_op, _value, prefix), query in zip(flows, queries, strict=True):
            assert query.startswith(prefix)
        assert " or " in queries[1] and '"Mężczyzna" in' in queries[1]
        assert fields["okresy"]["states"][0]["query"].endswith(".field.__userAnswer) > 1000")

    def test_repeating_source_uses_repeating_index_path(self):
        form = build_small_form()
        form.conditional_flows.append(
            ConditionalFlow(
                source_field_id="okres_rok",
                operator=ConditionOperator.GREATER_THAN,
                value=2020,
                target_type=TargetType.FIELD,
                target_id="okres_kwota",
                action=FlowAction.SHOW,
            )
        )
        fd, _notes = export_form_definition(form, KNOWN_DICTIONARIES)
        query = _fields(fd)["okres_kwota"]["states"][0]["query"]
        assert "__userAnswer[$repeatingSectionIndex]['okres_rok' in fieldTechName].answers" in query

    def test_finish_page(self, exported):
        finish = exported[0].tabs[-1]
        assert finish.type == "FINISH"
        assert finish.title == "Podsumowanie"
        info = finish.questions[0].field.repeting_section_grid[0].field
        assert info.type == "INFO_SECTION"
        assert info.description == "Urząd rozpatrzy wniosek."

    def test_finish_page_drops_inputs(self):
        form = build_small_form()
        form.pages[2].sections[0].fields.append(_field("uwagi", "Uwagi", FieldType.TEXTAREA))
        fd, notes = export_form_definition(form, KNOWN_DICTIONARIES)
        assert "uwagi" not in _fields(fd)
        assert any("reguła 7" in note for note in notes)

    def test_default_finish_added(self):
        fd, notes = export_form_definition(build_small_form(with_finish=False), KNOWN_DICTIONARIES)
        assert [tab.type for tab in fd.tabs] == ["MAIN", "MAIN", "FINISH"]
        finish_types = {
            child.field.type for q in fd.tabs[-1].questions for child in q.field.repeting_section_grid
        }
        assert finish_types == {"INFO_SECTION"}
        assert any("FINISH" in note for note in notes)
        assert not has_errors(validate_form_definition(fd))

    def test_title_truncated(self):
        fd, notes = export_form_definition(build_small_form(title="Wniosek " + "x" * 300))
        assert len(fd.title) == 255
        assert any("255" in note for note in notes)

    def test_uuids_deterministic_unique_and_seeded(self):
        first = export_form_definition(build_small_form(), KNOWN_DICTIONARIES)[0].model_dump(mode="json")
        second = export_form_definition(build_small_form(), KNOWN_DICTIONARIES)[0].model_dump(mode="json")
        other = export_form_definition(build_small_form("inny-form"), KNOWN_DICTIONARIES)[0]
        assert first == second
        ids = _all_ids(first)
        assert all(UUID_RE.match(value) for value in ids)
        assert len(ids) == len(set(ids))
        assert set(ids).isdisjoint(_all_ids(other.model_dump(mode="json")))

    def test_tech_names_unique_snake_case(self, exported):
        fields = _fields(exported[0])
        assert all(re.fullmatch(r"[a-z0-9_]+", tech) for tech in fields)
        assert slugify("Adres zamieszkania – ulica") == "adres_zamieszkania_ulica"
        assert slugify("Łódź") == "lodz"

    def test_find_official_dictionary(self):
        assert find_official_dictionary("dict_dial_codes") == "Numery kierunkowe"
        assert find_official_dictionary("WOJEWODZTWA") == "Województwo"
        assert find_official_dictionary("miejscowość") == "Miejscowość"
        assert find_official_dictionary("dict_gender") is None

    def test_mock_exports_without_structural_errors(self):
        from backend.app.services.mock_form import build_mock_form

        fd, _notes = export_form_definition(build_mock_form("wniosek.pdf", "application/pdf"))
        issues = validate_form_definition(fd)
        assert not has_errors(issues), [i.message for i in issues if i.level == "error"]


# ---------------------------------------------------------------------------------------------
# Walidator strukturalny
# ---------------------------------------------------------------------------------------------


class TestStructuralValidation:
    def test_not_a_form_definition(self):
        issues = validate_form_definition({"pages": []})
        assert [issue.rule for issue in issues] == ["Format"]

    def test_detects_rule_violations(self):
        broken = {
            "title": "Formularz",
            "tabs": [
                {
                    "id": "nie-uuid",
                    "title": "Strona",
                    "type": "MAIN",
                    "questions": [
                        {"x": 0, "y": 0, "rows": 1, "cols": 1, "field": {
                            "id": "a", "type": "TEXT_FIELD", "techName": "luzne", "validation": ["PL_PHONE"],
                        }},
                        {"x": 0, "y": 1, "rows": 1, "cols": 1, "field": {
                            "id": "b", "type": "SECTION", "techName": "sekcja", "title": "Sekcja",
                            "repetingSectionGrid": [
                                {"field": {
                                    "id": "c", "type": "TEXT_FIELD", "techName": "kraj", "dictionaryName": "Kraje",
                                    "validation": [],
                                }},
                                {"field": {
                                    "id": "d", "type": "TEXT_FIELD", "techName": "imie", "validation": [],
                                    "defaultValue": "MLPZ.imie", "defaultValueType": "EXTERNAL_SYSTEM",
                                    "states": [{"type": "SHOW", "query": "(\"x\" in tabs['S' in title]"},
                                               {"type": "HIDE", "query": "'brak' in field.techName"}],
                                }},
                                {"field": {"id": "e", "type": "NUMBER", "techName": "imie"}},
                            ],
                        }},
                    ],
                }
            ],
        }
        issues = validate_form_definition(broken)
        rules = {issue.rule for issue in issues if issue.level == "error"}
        assert {"Reguła 2a", "Reguła 2b", "Reguła 4/5", "Walidatory", "Reguła 11", "Dane inicjalne",
                "Reguła 10", "Reguła 12", "Reguła 8"} <= rules
        messages = " ".join(issue.message for issue in issues)
        assert "nieistniejącego techName „brak”" in messages
        assert "niezamknięty nawias" in messages
        assert any(issue.rule == "Standardy: Tytuł" for issue in issues)
        assert any(issue.rule == "Opis JSON" and issue.level == "warn" for issue in issues)

    def test_finish_with_input_and_broken_flow(self, example):
        finish_section = example["tabs"][-1]["questions"][0]["field"]
        finish_section["repetingSectionGrid"].append(
            {"x": 0, "y": 5, "rows": 1, "cols": 1, "field": {
                "id": "6b6f0b3e-7a10-4f4e-9e4a-0c7f5a3d2b11", "type": "TEXT_FIELD", "techName": "uwagi_koncowe",
                "validation": [],
            }}
        )
        example["flow"]["links"].pop()
        issues = validate_form_definition(example)
        assert any(issue.rule == "Reguła 7" and issue.level == "error" for issue in issues)
        assert any(issue.rule == "Reguła 9" for issue in issues)

    def test_schema_errors_reported_for_raw_dict(self, example):
        example["tabs"][0]["type"] = "OTHER"
        issues = validate_form_definition(example)
        assert any(issue.rule == "Schemat JSON" for issue in issues)
        assert any(issue.rule == "Reguła 2c" for issue in issues)

    def test_jsonata_syntax_check(self):
        assert check_jsonata_syntax("$not(\"a\" in tabs['x' in title].f)") is None
        assert check_jsonata_syntax("(\"a\" in x") is not None
        assert check_jsonata_syntax("\"a) in x") is not None
        assert check_jsonata_syntax("x]") is not None


# ---------------------------------------------------------------------------------------------
# Spójność wewnętrznego JSON-a (nowe reguły kontraktu)
# ---------------------------------------------------------------------------------------------


class TestConsistency:
    def test_small_form_is_consistent(self):
        errors, warnings = validate_form_consistency(build_small_form(), {"dict_gender", "dict_countries",
                                                                           "dict_voivodeships"})
        assert errors == []
        assert warnings == []

    def test_contract_violations(self):
        form = build_small_form()
        finish = form.pages[2].sections[0]
        finish.fields.append(_field("uwagi", "Uwagi", FieldType.TEXTAREA))
        details = form.pages[1].sections[0]
        details.fields.extend(
            [
                _field("zgody_puste", "Zgody", FieldType.CONSENT_SECTION),
                _field("blok", "Tekst", FieldType.TEXT_BLOCK, required=True),
                _field("tekst_slownik", "Tekst", dictionary_ref="dict_gender"),
                _field("bez_zrodla", "Pole", default_value_key="name"),
                _field("bez_klucza", "Pole", default_value_type=InitialDataSource.MLPZ),
                _field("zly_klucz", "Pole", default_value_type=InitialDataSource.MLPZ,
                       default_value_key="nieistniejacy", read_only=True),
            ]
        )
        errors, warnings = validate_form_consistency(form, {"dict_gender", "dict_countries", "dict_voivodeships"})
        joined_errors = " ".join(errors)
        joined_warnings = " ".join(warnings)
        assert "pole uwagi: strona końcowa" in joined_errors
        assert "pole zgody_puste: consent_section" in joined_errors
        assert "pole tekst_slownik: dictionary_ref" in joined_errors
        assert "pole bez_zrodla: default_value_key wymaga" in joined_errors
        assert "pole bez_klucza: default_value_type MLPZ wymaga" in joined_errors
        assert "pole blok: pole prezentacyjne" in joined_warnings
        assert "'nieistniejacy'" in joined_warnings

    def test_requires_main_page(self):
        form = build_small_form()
        only_finish = form.model_copy(update={"pages": [form.pages[2]], "conditional_flows": []})
        errors, _warnings = validate_form_consistency(only_finish, set())
        assert any("MAIN" in error for error in errors)

    def test_initial_data_attributes_match_material(self):
        assert len(INITIAL_DATA_ATTRIBUTES["PESEL"]) == 16
        assert len(INITIAL_DATA_ATTRIBUTES["PESEL_ADDRESS"]) == 18
        assert len(INITIAL_DATA_ATTRIBUTES["MLPZ"]) == 17


# ---------------------------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------------------------


def _upload(name: str = "wniosek.pdf"):
    return {"file": (name, io.BytesIO(PDF_BYTES), "application/pdf")}


class TestApi:
    def test_convert_returns_form_definition(self, client):
        response = client.post("/api/v1/forms/convert", files=_upload())
        assert response.status_code == 200
        body = response.json()
        assert body["form_definition"] is not None
        fd = FormDefinition.model_validate(body["form_definition"])
        assert fd.tabs[-1].type == "FINISH"
        assert isinstance(body["form_definition_notes"], list)
        assert not has_errors(validate_form_definition(body["form_definition"]))
        # Klucze o wartości None nie trafiają do JSON-a (jak w oficjalnym przykładzie).
        assert "null" not in json.dumps(body["form_definition"])

    def test_convert_survives_dictionary_errors(self, client, monkeypatch):
        def broken(self, dictionary_id):
            raise OSError("dysk niedostępny")

        monkeypatch.setattr(DictionaryService, "get_dictionary", broken)
        response = client.post("/api/v1/forms/convert", files=_upload())
        assert response.status_code == 200
        assert response.json()["form_definition"] is not None

    def test_batch_items_have_form_definition(self, client):
        files = [("files", ("a.pdf", io.BytesIO(PDF_BYTES), "application/pdf"))]
        body = client.post("/api/v1/forms/convert-batch", files=files).json()
        assert body["results"][0]["form_definition"]["tabs"]

    def test_export_definition_endpoint(self, client):
        form = build_small_form().model_dump(mode="json")
        response = client.post("/api/v1/forms/export-definition", json={"form": form})
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert body["schema_errors"] == []
        FormDefinition.model_validate(body["form_definition"])
        assert body["notes"]
        assert all(issue["level"] == "warn" for issue in body["issues"])

    def test_export_definition_schema_errors(self, client):
        response = client.post("/api/v1/forms/export-definition", json={"form": {"metadata": {}}})
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is False
        assert body["schema_errors"]
        assert body["form_definition"] is None

    def test_validate_definition_endpoint(self, client, example):
        response = client.post("/api/v1/forms/validate-definition", json={"form_definition": example})
        assert response.status_code == 200
        assert response.json() == {"valid": True, "issues": []}

        example["tabs"] = example["tabs"][:1]
        body = client.post("/api/v1/forms/validate-definition", json={"form_definition": example}).json()
        assert body["valid"] is False
        assert any(issue["rule"] == "Reguła 2a" for issue in body["issues"])


class TestRealPipelineImportErrors:
    def test_missing_optional_library_at_init_gives_503(self, make_app, monkeypatch):
        monkeypatch.setitem(sys.modules, "backend.app.modules.ai_extractor.extractor", None)
        app = make_app(ENV="development", USE_MOCK_PIPELINE="false", JWT_AUTH_ENABLED="false")
        with TestClient(app) as real_client:
            response = real_client.post("/api/v1/forms/convert", files=_upload())
            health = real_client.get("/api/v1/health")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "PIPELINE_NOT_READY"
        assert "[ai,llm]" in response.json()["error"]["message"]
        assert health.status_code == 200
        assert health.json()["status"] == "degraded"

    def test_lazy_import_error_during_convert_gives_503(self, settings):
        class _Parser:
            def parse(self, **_kwargs):
                raise ModuleNotFoundError("No module named 'rapidocr_onnxruntime'", name="rapidocr_onnxruntime")

        pipeline = RealFormPipeline.__new__(RealFormPipeline)
        pipeline._settings = settings
        pipeline._import_error = None
        pipeline._parser = _Parser()
        with pytest.raises(PipelineNotReadyError) as excinfo:
            pipeline.convert(filename="a.pdf", content=PDF_BYTES, mime_type="application/pdf")
        assert excinfo.value.details["missing_module"] == "rapidocr_onnxruntime"
