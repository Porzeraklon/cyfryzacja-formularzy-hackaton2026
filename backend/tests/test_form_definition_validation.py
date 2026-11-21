"""Walidator strukturalny FormDefinition (reguły 1–12 z Reguł technicznych + Standardy)."""

import copy
import io
import json

import pytest

from backend.app.core.config import REPO_ROOT, Settings
from backend.app.schemas.form_definition import FormDefinition
from backend.app.services.form_definition_validation import (
    check_jsonata_syntax,
    has_errors,
    validate_form_definition,
)
from backend.tests.conftest import PDF_BYTES

EXAMPLE = REPO_ROOT / "frontend" / "examples" / "wniosek-o-stwierdzenie-nadplaty-podatku.json"


@pytest.fixture()
def example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _children(fd: dict):
    for tab in fd["tabs"]:
        for question in tab["questions"]:
            for child in question["field"].get("repetingSectionGrid", []):
                yield child["field"]


def _first(fd: dict, field_type: str) -> dict:
    return next(f for f in _children(fd) if f["type"] == field_type)


def _error_rules(fd: dict) -> set[str]:
    return {issue.rule for issue in validate_form_definition(fd) if issue.level == "error"}


def _break_jsonata(fd: dict) -> None:
    field = next(f for f in _children(fd) if f.get("states"))
    field["states"][0]["query"] += " ("


def _nest_section(fd: dict) -> None:
    questions = fd["tabs"][0]["questions"]
    questions[0]["field"]["repetingSectionGrid"].append(copy.deepcopy(questions[1]))


def _field_outside_section(fd: dict) -> None:
    field = copy.deepcopy(_first(fd, "TEXT_FIELD"))
    fd["tabs"][0]["questions"].append({"x": 0, "y": 99, "rows": 1, "cols": 1, "field": field})


def _duplicate_id(fd: dict) -> None:
    _first(fd, "SELECT")["id"] = _first(fd, "TEXT_FIELD")["id"]


class TestReference:
    def test_example_is_valid(self, example):
        issues = validate_form_definition(example)
        assert not has_errors(issues), issues
        FormDefinition.model_validate(example)

    def test_mock_conversion_has_no_issues(self, client):
        response = client.post(
            "/api/v1/forms/convert", files={"file": ("wniosek.pdf", io.BytesIO(PDF_BYTES), "application/pdf")}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["form_definition_issues"] == []
        assert not has_errors(validate_form_definition(body["form_definition"]))


@pytest.mark.parametrize(
    ("mutation", "rule"),
    [
        (lambda fd: fd.update(title="W" * 300), "Reguła 1"),
        (lambda fd: (fd.update(tabs=fd["tabs"][:1]), fd["flow"].update(links=[])), "Reguła 2a"),
        (lambda fd: fd["tabs"][0].update(type="START"), "Reguła 2c"),
        (lambda fd: fd["tabs"][1].update(questions=[]), "Reguła 3"),
        (_field_outside_section, "Reguła 4/5"),
        (_nest_section, "Reguła 6"),
        (
            lambda fd: fd["tabs"][-1]["questions"][0]["field"]["repetingSectionGrid"][0]["field"].update(
                type="TEXT_FIELD"
            ),
            "Reguła 7",
        ),
        (lambda fd: fd["flow"].update(links=[]), "Reguła 9"),
        (_break_jsonata, "Reguła 10"),
        (lambda fd: _first(fd, "TEXT_FIELD").update(dictionaryName="Kraje"), "Reguła 11"),
        (lambda fd: _first(fd, "NUMBER").update(validation=["REQUIRED", "PESEL"]), "Walidatory"),
        (lambda fd: _first(fd, "TEXT_FIELD").update(validation=["PL_PHONE"]), "Walidatory"),
        (_duplicate_id, "Opis JSON"),
    ],
)
def test_broken_variants_are_reported(example, mutation, rule):
    mutation(example)
    rules = _error_rules(example)
    assert rule in rules, rules


def test_finish_not_last_in_array_is_warning(example):
    example["tabs"].insert(0, example["tabs"].pop())
    issues = validate_form_definition(example)
    assert any(i.level == "warn" and "ostatnia" in i.message for i in issues)


def test_jsonata_syntax_check():
    assert check_jsonata_syntax("\"Polska\" in tabs['Dane' in title].questions") is None
    assert check_jsonata_syntax("(\"a\" in x") is not None
    assert check_jsonata_syntax("\"niezamknięty in x") is not None


def test_validate_endpoint(client, example):
    ok = client.post("/api/v1/forms/validate-formdefinition", json={"form_definition": example})
    assert ok.status_code == 200 and ok.json()["valid"] is True
    example["title"] = ""
    bad = client.post("/api/v1/forms/validate-formdefinition", json={"form_definition": example})
    body = bad.json()
    assert body["valid"] is False and any(i["rule"] == "Reguła 1" for i in body["issues"])


def test_export_endpoint_returns_issues(client):
    from backend.app.services.pipeline_service import build_mock_form

    form = build_mock_form("wniosek.pdf", "application/pdf").model_dump(mode="json")
    response = client.post("/api/v1/forms/export-formdefinition", json={"form": form})
    assert response.status_code == 200
    assert response.json()["issues"] == []


@pytest.mark.parametrize(
    "value",
    ['["http://a.pl","http://b.pl"]', "http://a.pl, http://b.pl"],
)
def test_cors_origins_accepts_json_and_comma_list(value):
    settings = Settings(_env_file=None, CORS_ORIGINS=value)
    assert settings.CORS_ORIGINS == ["http://a.pl", "http://b.pl"]
