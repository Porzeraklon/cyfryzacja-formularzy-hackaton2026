"""Regresje eksportera FormDefinition (port fdConvertInternal) — pisownia schematu,
jsonata z __userAnswer, tooltip bez podwójnego „Np.", typy CONSENT_SECTION/TEXT_BLOCK."""

import io
import re

from backend.app.services.formdefinition_export import (
    _Ref,
    convert_formdefinition,
    fd_field_path,
    fd_normalize,
    fd_slug,
    fd_uuid,
)
from backend.tests.conftest import PDF_BYTES

_UUID_V4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _walk_question(question):
    field = question.get("field")
    if not field:
        return
    yield field
    for child in field.get("repetingSectionGrid") or []:
        yield from _walk_question(child)


def _iter_fields(fd):
    for tab in fd["tabs"]:
        for question in tab["questions"]:
            yield from _walk_question(question)


class TestFdHelpers:
    def test_uuid_deterministic_and_v4(self):
        first = fd_uuid("field:pesel")
        assert first == fd_uuid("field:pesel")
        assert _UUID_V4_RE.fullmatch(first)
        assert first != fd_uuid("field:pesel2")

    def test_uuid_unicode_seed(self):
        assert _UUID_V4_RE.fullmatch(fd_uuid("sekcja:Twoje dane — Łódź"))

    def test_normalize(self):
        assert fd_normalize("Województwo") == "WOJEWODZTWO"
        assert fd_normalize("Numery kierunkowe") == "NUMERY_KIERUNKOWE"
        assert fd_normalize("  Łódź  ") == "LODZ"

    def test_slug(self):
        assert fd_slug("Kwota nadpłaty") == "kwota_nadplaty"
        assert fd_slug("Łódź") == "lodz"
        assert fd_slug("") == "pole"

    def test_field_path_schema_spelling(self):
        ref = _Ref(
            tab_title="Strona",
            section_tech="SEKCJA",
            tech_name="POLE",
            type="TEXT_FIELD",
            options=[],
            dict_ref=None,
            out={},
        )
        path = fd_field_path(ref)
        assert "repetingSectionGrid" in path
        assert "repeatingSectionGrid" not in path
        assert path.endswith(".field")
        assert "__userAnswer" not in path


class TestExportFromMock:
    def _export(self, client):
        response = client.post(
            "/api/v1/forms/convert", files={"file": ("wniosek.pdf", io.BytesIO(PDF_BYTES), "application/pdf")}
        )
        body = response.json()
        return body["form"], body["form_definition"]

    def test_tabs_and_finish(self, client):
        _, fd = self._export(client)
        types = [tab["type"] for tab in fd["tabs"]]
        assert types == ["MAIN", "MAIN", "MAIN", "FINISH"]
        finish = fd["tabs"][-1]
        finish_types = {f["type"] for q in finish["questions"] for f in _walk_question(q)}
        assert finish_types <= {"INFO_SECTION", "SECTION"}

    def test_queries_have_user_answer(self, client):
        _, fd = self._export(client)
        queries = [
            state["query"]
            for field in _iter_fields(fd)
            for state in field.get("states") or []
            if state.get("query")
        ]
        assert queries, "mock musi generować stany warunkowe"
        assert all(".__userAnswer" in query for query in queries)

    def test_tooltip_single_np(self, client):
        _, fd = self._export(client)
        tooltips = [f.get("tooltipMessage", "") for f in _iter_fields(fd)]
        assert any(t.startswith("Np. Polna") for t in tooltips)
        assert not any("Np. Np." in t for t in tooltips)

    def test_repeting_spelling_in_output(self, client):
        _, fd = self._export(client)
        groups = [f for f in _iter_fields(fd) if f.get("repetingSectionGrid")]
        assert groups, "mock musi zawierać sekcję powtarzalną (okresy nadpłaty)"
        assert all("repeatingSectionGrid" not in f for f in _iter_fields(fd))

    def test_consent_and_textblock_types(self, client):
        _, fd = self._export(client)
        types = {f["type"] for f in _iter_fields(fd)}
        assert "CONSENT_SECTION" in types
        assert "TEXT_BLOCK" in types

    def test_convert_formdefinition_direct(self, client):
        internal, _ = self._export(client)
        exported = convert_formdefinition(internal)
        assert set(exported) == {"form", "notes"}
        assert [t["type"] for t in exported["form"]["tabs"]] == ["MAIN", "MAIN", "MAIN", "FINISH"]
