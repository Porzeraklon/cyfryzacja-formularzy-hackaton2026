"""Testy ekstraktora struktury (zadania #3 i #4) z atrapą lokalnego LLM oraz parsera PDF.

Testy nie wymagają modelu GGUF ani llama_cpp: LLM jest wstrzykiwany do konstruktora
(`llm_complete`) jako funkcja zwracająca przygotowany JSON.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from backend.app.core.exceptions import PipelineNotReadyError
from backend.app.core.security import MIME_DOCX, MIME_PDF
from backend.app.interfaces.pipeline import ParsedDocument, ParsedPage
from backend.app.modules.ai_extractor.extractor import (
    MAX_FIELDS_PER_PAGE,
    LocalStructureExtractor,
    _form_table_spec,
)
from backend.app.modules.ingestion.parsers import MultiFormatParser, _pdf_page_tables
from backend.app.schemas.form_schema import FieldType, FormDocumentSchema, PageType

# --------------------------------------------------------------------------------------------
# Atrapa LLM
# --------------------------------------------------------------------------------------------


def _field(name: str, label: str, field_type: str = "text", **extra: Any) -> dict[str, Any]:
    return {"name": name, "label": label, "field_type": field_type, **extra}


def _section(title: str, *fields: dict[str, Any]) -> dict[str, Any]:
    return {"title": title, "fields": list(fields)}


def _page(title: str, *sections: dict[str, Any], document_title: str | None = None) -> dict[str, Any]:
    return {"title": title, "document_title": document_title, "sections": list(sections)}


class FakeLlm:
    """Zwraca odpowiedź wg znacznika występującego w treści promptu użytkownika."""

    def __init__(
        self,
        pages: dict[str, dict[str, Any] | str],
        titles: list[str] | None = None,
    ) -> None:
        self.pages = pages
        self.titles = titles
        self.page_prompts: list[str] = []
        self.title_prompts: list[str] = []

    def __call__(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> str:
        user = messages[-1]["content"]
        if schema.get("title") == "_LlmPageTitles":
            self.title_prompts.append(user)
            return json.dumps({"titles": self.titles or []})
        assert schema.get("title") == "_LlmPage"
        self.page_prompts.append(user)
        for marker, response in self.pages.items():
            if marker in user:
                return response if isinstance(response, str) else json.dumps(response)
        return json.dumps(_page("Pusta"))


def _extract(
    tmp_path, llm: Callable[..., str], pages: list[ParsedPage], tables=None, filename="wniosek_testowy.pdf"
) -> FormDocumentSchema:
    parsed = ParsedDocument(
        source_filename=filename,
        source_format=MIME_PDF,
        pages=pages,
        tables=tables or [],
    )
    form = LocalStructureExtractor(models_dir=tmp_path, llm_complete=llm).extract(parsed)
    # Wynik zawsze musi przejść walidację kontraktu (extra="forbid").
    return FormDocumentSchema.model_validate(form.model_dump(mode="json"))


def _all_fields(form: FormDocumentSchema):
    return [item for page in form.pages for section in page.sections for item in section.fields]


def _all_ids(form: FormDocumentSchema) -> list[str]:
    ids = []
    for item in _all_fields(form):
        ids.append(item.field_id)
        ids.extend(sub.field_id for sub in item.fields or [])
    return ids


FAMILY_TABLE = [
    ["Lp.", "Imię i nazwisko", "PESEL", "Data urodzenia", "Stopień pokrewieństwa"],
    ["1.", "", "", "", ""],
    ["2.", "", "", "", ""],
    ["3.", "", "", "", ""],
]
PRICE_TABLE = [
    ["Usługa", "Opłata"],
    ["Wydanie zaświadczenia", "17 zł"],
    ["Wydanie odpisu", "5 zł"],
]

APPLICANT_SECTION = _section(
    "Dane wnioskodawcy",
    _field("imie", "Imię", required=True),
    _field("nazwisko", "Nazwisko", required=True),
    _field("pesel", "Numer PESEL"),
)
ADDRESS_SECTION = _section(
    "Adres zamieszkania",
    _field("miejscowosc", "Miejscowość"),
    _field("ulica", "Ulica"),
    _field("kod_pocztowy", "Kod pocztowy"),
    _field("nr_domu", "Numer domu"),
)
SUBJECT_SECTION = _section(
    "Przedmiot wniosku",
    _field("rodzaj_sprawy", "Rodzaj sprawy", "radio", options=[{"code": "a", "label": "A"}]),
    _field("uzasadnienie", "Uzasadnienie", "textarea"),
    _field("kwota", "Kwota", "number"),
    _field("data_zdarzenia", "Data zdarzenia", "date"),
    _field("opis", "Opis nieruchomości", "textarea"),
)


# --------------------------------------------------------------------------------------------
# #3 Tabele -> sekcje powtarzające się
# --------------------------------------------------------------------------------------------


class TestTables:
    def test_llm_group_with_columns_maps_to_repeatable_group(self, tmp_path):
        group = _field(
            "czlonkowie_rodziny",
            "Członkowie rodziny",
            "repeatable_group",
            fields=[
                {"name": "lp", "label": "Lp.", "field_type": "number"},
                {"name": "imie_nazwisko", "label": "Imię i nazwisko", "field_type": "text", "required": True},
                {"name": "pesel", "label": "PESEL", "field_type": "text"},
                {"name": "data_urodzenia", "label": "Data urodzenia", "field_type": "date"},
                {
                    "name": "stopien",
                    "label": "Stopień pokrewieństwa",
                    "field_type": "select",
                    "options": [{"code": "syn", "label": "Syn"}, {"code": "corka", "label": "Córka"}],
                },
            ],
        )
        llm = FakeLlm({"STRONA1": _page("Rodzina", _section("Członkowie rodziny", group))})
        form = _extract(tmp_path, llm, [ParsedPage(page_number=1, text="STRONA1", tables=[FAMILY_TABLE])])

        groups = [f for f in _all_fields(form) if f.field_type == FieldType.REPEATABLE_GROUP]
        assert len(groups) == 1  # LLM pokrył tabelę - fallback nie dubluje grupy
        columns = groups[0].fields
        assert [c.label for c in columns] == ["Imię i nazwisko", "PESEL", "Data urodzenia", "Stopień pokrewieństwa"]
        assert [c.field_type for c in columns] == [
            FieldType.TEXT,
            FieldType.TEXT,
            FieldType.DATE,
            FieldType.SELECT,
        ]
        assert columns[0].required is True
        assert [o.code for o in columns[3].options] == ["syn", "corka"]
        ids = _all_ids(form)
        assert len(ids) == len(set(ids))
        assert all(c.field_id.startswith(groups[0].field_id) for c in columns)

    def test_page_tables_go_to_their_own_page_prompt(self, tmp_path):
        llm = FakeLlm({})
        pages = [
            ParsedPage(page_number=1, text="Strona pierwsza bez tabel"),
            ParsedPage(page_number=2, text="Strona druga", tables=[FAMILY_TABLE]),
            ParsedPage(page_number=3, text="Strona trzecia", tables=[PRICE_TABLE]),
        ]
        _extract(tmp_path, llm, pages)
        first, second, third = llm.page_prompts
        assert "Stopień pokrewieństwa" not in first and "Tabele" not in first
        assert "Stopień pokrewieństwa" in second and "do wypełnienia" in second
        assert "Wydanie zaświadczenia" in third and "z danymi" in third
        assert "Stopień pokrewieństwa" not in third

    def test_document_tables_follow_header_text_or_separate_call(self, tmp_path):
        # Długi DOCX dzielony na fragmenty; tabela z nagłówkiem w 2. fragmencie.
        filler = "\n".join(f"Linia wypełniająca numer {i} " + "x" * 80 for i in range(120))
        table_text = " | ".join(FAMILY_TABLE[0])
        text = f"POCZATEK\n{filler}\nDRUGA_CZESC\nCzłonkowie rodziny:\n{table_text}\n | | | | "
        orphan = [["Nr działki", "Obręb", "Powierzchnia"], ["", "", ""]]
        llm = FakeLlm({})
        parsed = ParsedDocument(
            source_filename="dlugi.docx",
            source_format=MIME_DOCX,
            pages=[ParsedPage(page_number=1, text=text)],
            tables=[FAMILY_TABLE, orphan],
        )
        form = LocalStructureExtractor(models_dir=tmp_path, llm_complete=llm).extract(parsed)

        assert len(llm.page_prompts) == 3  # 2 fragmenty tekstu + osobne wywołanie dla tabeli-sieroty
        first, second, orphan_prompt = llm.page_prompts
        assert "POCZATEK" in first and "Tabele rozpoznane" not in first
        assert "DRUGA_CZESC" in second and "Tabele rozpoznane" in second
        assert "Nr działki" in orphan_prompt and "Imię i nazwisko" not in orphan_prompt
        # Obie tabele-formularze dostały deterministyczne grupy (atrapa nie zwróciła pól).
        groups = [f for f in _all_fields(form) if f.field_type == FieldType.REPEATABLE_GROUP]
        assert len(groups) == 2
        family = next(g for g in groups if g.label == "Członkowie rodziny")
        assert family.fields[0].label == "Imię i nazwisko"

    def test_deterministic_fallback_for_form_table(self, tmp_path):
        llm = FakeLlm({"STRONA": _page("Wniosek", APPLICANT_SECTION)})
        text = "STRONA\nOsoby pozostające we wspólnym gospodarstwie:\nLp. Imię i nazwisko PESEL Data urodzenia"
        form = _extract(
            tmp_path, llm, [ParsedPage(page_number=1, text=text, tables=[FAMILY_TABLE, PRICE_TABLE])]
        )
        groups = [f for f in _all_fields(form) if f.field_type == FieldType.REPEATABLE_GROUP]
        assert len(groups) == 1  # cennik (tabela z danymi) nie jest polem
        group = groups[0]
        assert group.label == "Osoby pozostające we wspólnym gospodarstwie"
        assert [c.label for c in group.fields] == [
            "Imię i nazwisko",
            "PESEL",
            "Data urodzenia",
            "Stopień pokrewieństwa",
        ]
        assert [c.field_type for c in group.fields] == [
            FieldType.TEXT,
            FieldType.TEXT,
            FieldType.DATE,
            FieldType.TEXT,
        ]
        assert "Lp." not in [c.label for c in group.fields]

    def test_fallback_replaces_columns_split_into_plain_fields(self, tmp_path):
        split = _section(
            "Członkowie rodziny",
            _field("imie_nazwisko", "Imię i nazwisko"),
            _field("pesel_czlonka", "PESEL"),
            _field("data_ur", "Data urodzenia", "date"),
            _field("uwagi_dodatkowe", "Inne informacje"),
        )
        llm = FakeLlm({"STRONA": _page("Rodzina", split)})
        form = _extract(tmp_path, llm, [ParsedPage(page_number=1, text="STRONA", tables=[FAMILY_TABLE])])
        fields = _all_fields(form)
        assert [f.field_type for f in fields] == [FieldType.REPEATABLE_GROUP, FieldType.TEXT]
        assert fields[1].label == "Inne informacje"

    def test_llm_group_without_columns_is_filled_from_table(self, tmp_path):
        group = _field("osoby", "Osoby", "repeatable_group")
        llm = FakeLlm({"STRONA": _page("Rodzina", _section("Osoby", group))})
        form = _extract(tmp_path, llm, [ParsedPage(page_number=1, text="STRONA", tables=[FAMILY_TABLE])])
        (only,) = _all_fields(form)
        assert only.field_type == FieldType.REPEATABLE_GROUP
        assert len(only.fields) == 4

    def test_group_without_columns_and_without_table_degrades_to_textarea(self, tmp_path):
        llm = FakeLlm({"STRONA": _page("X", _section("Osoby", _field("osoby", "Osoby", "repeatable_group")))})
        form = _extract(tmp_path, llm, [ParsedPage(page_number=1, text="STRONA")])
        (only,) = _all_fields(form)
        assert only.field_type == FieldType.TEXTAREA and only.fields is None

    def test_form_table_detection(self):
        assert _form_table_spec(FAMILY_TABLE).columns[0] == "Imię i nazwisko"
        assert _form_table_spec(PRICE_TABLE) is None
        assert _form_table_spec([["Imię", ""], ["Nazwisko", ""]]) is None  # układ etykieta/wartość
        assert _form_table_spec([["A", "B"]]) is None  # sam nagłówek


# --------------------------------------------------------------------------------------------
# #4 Strony tematyczne
# --------------------------------------------------------------------------------------------


class TestThematicPages:
    def test_three_pdf_pages_two_topics_give_two_pages(self, tmp_path):
        llm = FakeLlm(
            {
                "P1": _page("Strona 1", APPLICANT_SECTION, document_title="Wniosek o wydanie zaświadczenia"),
                "P2": _page("Strona 2", ADDRESS_SECTION),
                "P3": _page("Strona 3", SUBJECT_SECTION),
            }
        )
        pages = [ParsedPage(page_number=i, text=f"P{i}") for i in (1, 2, 3)]
        form = _extract(tmp_path, llm, pages)

        assert len(form.pages) == 2
        assert [p.title for p in form.pages] == ["Dane wnioskodawcy", "Przedmiot wniosku"]
        assert [s.title for s in form.pages[0].sections] == ["Dane wnioskodawcy", "Adres zamieszkania"]
        assert all(p.page_type == PageType.MAIN for p in form.pages)
        assert [p.page_id for p in form.pages] == ["page_1", "page_2"]
        assert [s.section_id for s in form.pages[0].sections] == ["sec_1_1", "sec_1_2"]
        assert form.metadata.title == "Wniosek o wydanie zaświadczenia"
        assert llm.title_prompts == []  # strona sprawy z 1 sekcją - bez wywołania o tytuły

    def test_topics_follow_standards_order(self, tmp_path):
        consents = _section(
            "Oświadczenia",
            _field(
                "oswiadczenia",
                "Oświadczenia",
                "consent_section",
                required=True,
                options=[
                    {"code": "1", "label": "Oświadczam, że dane są prawdziwe."},
                    {"code": "2", "label": "Wyrażam zgodę na kontakt telefoniczny."},
                ],
            ),
        )
        attachments = _section("Załączniki", _field("zal_1", "Kopia aktu notarialnego", "file"))
        llm = FakeLlm({"P1": _page("1", SUBJECT_SECTION, consents), "P2": _page("2", attachments, APPLICANT_SECTION)})
        form = _extract(tmp_path, llm, [ParsedPage(page_number=1, text="P1"), ParsedPage(page_number=2, text="P2")])

        assert [p.title for p in form.pages] == ["Dane wnioskodawcy", "Przedmiot wniosku", "Załączniki, oświadczenia i zgody"]
        consent = form.pages[2].sections[1].fields[0]
        assert consent.field_type == FieldType.CONSENT_SECTION
        assert [c.description for c in consent.consents] == [
            "Oświadczam, że dane są prawdziwe.",
            "Wyrażam zgodę na kontakt telefoniczny.",
        ]
        assert all(c.required for c in consent.consents) and consent.options is None

    def test_section_continued_across_pages_is_merged(self, tmp_path):
        llm = FakeLlm(
            {
                "P1": _page(
                    "1",
                    APPLICANT_SECTION,
                    _section("Dane nieruchomości", _field("obreb", "Obręb"), _field("dzialka", "Nr działki")),
                ),
                "P2": _page(
                    "2",
                    _section("Dane nieruchomości (cd.)", _field("kw", "Numer księgi wieczystej")),
                    _section("Opis", _field("opis_sprawy", "Opis sprawy", "textarea")),
                ),
                "P3": _page("3", _section("", _field("uwagi", "Uwagi", "textarea"))),
            }
        )
        pages = [ParsedPage(page_number=i, text=f"P{i}") for i in (1, 2, 3)]
        form = _extract(tmp_path, llm, pages)
        sections = [s for p in form.pages for s in p.sections]
        titles = [s.title for s in sections]
        assert titles.count("Dane nieruchomości") == 1
        assert not any("cd." in t for t in titles)
        estate = next(s for s in sections if s.title == "Dane nieruchomości")
        assert [f.name for f in estate.fields] == ["obreb", "dzialka", "kw"]
        opis = next(s for s in sections if s.title == "Opis")
        assert [f.name for f in opis.fields] == ["opis_sprawy", "uwagi"]  # sekcja bez tytułu = ciąg dalszy

    def test_long_topic_is_split_and_titles_from_llm(self, tmp_path):
        many = [_field(f"pole_{i}", f"Informacja o sprawie {i}") for i in range(10)]
        llm = FakeLlm(
            {
                "P1": _page("1", _section("Szczegóły sprawy", *many[:5]), _section("Okoliczności", *many[5:])),
                "P2": _page("2", _section("Dodatkowe dane sprawy", *[_field(f"x_{i}", f"Dane {i}") for i in range(8)])),
            },
            titles=["Szczegóły i okoliczności"],
        )
        form = _extract(tmp_path, llm, [ParsedPage(page_number=1, text="P1"), ParsedPage(page_number=2, text="P2")])
        assert len(form.pages) == 2
        for page in form.pages:
            assert sum(len(s.fields) for s in page.sections) <= MAX_FIELDS_PER_PAGE
        assert [p.title for p in form.pages] == ["Szczegóły i okoliczności", "Dodatkowe dane sprawy"]
        assert len(llm.title_prompts) == 1

    def test_llm_titles_with_wrong_count_fall_back_to_first_section(self, tmp_path):
        llm = FakeLlm(
            {"P1": _page("1", _section("Szczegóły sprawy", _field("a", "A")), _section("Inne", _field("b", "B")))},
            titles=["Jeden", "Dwa", "Trzy"],
        )
        form = _extract(tmp_path, llm, [ParsedPage(page_number=1, text="P1")])
        assert [p.title for p in form.pages] == ["Szczegóły sprawy"]

    def test_empty_document_yields_single_page_and_filename_title(self, tmp_path):
        llm = FakeLlm({})
        form = _extract(tmp_path, llm, [ParsedPage(page_number=1, text="")], filename="wniosek-o_zasilek.pdf")
        assert len(form.pages) == 1 and form.pages[0].sections == []
        assert form.metadata.title == "wniosek o zasilek"
        assert llm.page_prompts == []  # pusta strona nie trafia do LLM

    def test_document_title_truncated_to_255(self, tmp_path):
        llm = FakeLlm({"P1": _page("1", APPLICANT_SECTION, document_title="Wniosek " + "a" * 400)})
        form = _extract(tmp_path, llm, [ParsedPage(page_number=1, text="P1")])
        assert len(form.metadata.title) == 255

    def test_text_block_has_content_and_no_requirement(self, tmp_path):
        block = _field("pouczenie", "Pouczenie", "text_block", required=True, help_text="Za fałszywe zeznania...")
        llm = FakeLlm({"P1": _page("1", _section("Przedmiot", _field("a", "A"), block))})
        form = _extract(tmp_path, llm, [ParsedPage(page_number=1, text="P1")])
        text_block = _all_fields(form)[1]
        assert text_block.field_type == FieldType.TEXT_BLOCK
        assert text_block.required is False and text_block.help_text == "Za fałszywe zeznania..."


# --------------------------------------------------------------------------------------------
# Gotowość modelu / błędy LLM
# --------------------------------------------------------------------------------------------


class TestReadiness:
    def test_missing_model_raises_not_ready_even_for_empty_document(self, tmp_path):
        parsed = ParsedDocument(source_filename="a.pdf", source_format=MIME_PDF, pages=[])
        with pytest.raises(PipelineNotReadyError):
            LocalStructureExtractor(models_dir=tmp_path).extract(parsed)

    def test_invalid_llm_json_raises_not_ready(self, tmp_path):
        llm = FakeLlm({"P1": "to nie jest JSON"})
        with pytest.raises(PipelineNotReadyError):
            _extract(tmp_path, llm, [ParsedPage(page_number=1, text="P1")])

    def test_info_section_is_not_allowed_from_llm(self, tmp_path):
        llm = FakeLlm({"P1": _page("1", _section("X", _field("info", "Info", "info_section")))})
        with pytest.raises(PipelineNotReadyError):
            _extract(tmp_path, llm, [ParsedPage(page_number=1, text="P1")])


# --------------------------------------------------------------------------------------------
# Parser PDF: tabele przypisane do stron
# --------------------------------------------------------------------------------------------


def _build_pdf(page_streams: list[str]) -> bytes:
    """Minimalny PDF (Helvetica, bez zależności) z podanymi strumieniami treści stron."""
    objects: list[bytes] = []
    n_pages = len(page_streams)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, stream in enumerate(page_streams):
        content_id = 5 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        data = stream.encode("latin-1")
        objects.append(f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def _text(x: int, y: int, value: str) -> str:
    return f"BT /F1 10 Tf {x} {y} Td ({value}) Tj ET\n"


def _grid_stream() -> str:
    columns = [50, 100, 300, 450]
    rows = [700, 680, 660, 640]
    parts = [_text(50, 740, "Czlonkowie rodziny:"), "1 w\n"]
    for y in rows:
        parts.append(f"{columns[0]} {y} m {columns[-1]} {y} l S\n")
    for x in columns:
        parts.append(f"{x} {rows[0]} m {x} {rows[-1]} l S\n")
    for x, label in zip(columns, ["Lp.", "Imie i nazwisko", "Data urodzenia"], strict=False):
        parts.append(_text(x + 4, 686, label))
    return "".join(parts)


class TestPdfParserTables:
    def test_pdf_table_on_second_page_goes_to_parsed_page(self):
        content = _build_pdf([_text(50, 780, "Wniosek o cos - strona pierwsza"), _grid_stream()])
        parsed = MultiFormatParser().parse(filename="w.pdf", content=content, mime_type=MIME_PDF)

        assert len(parsed.pages) == 2
        assert parsed.tables == []  # PDF: brak tabel bez strony
        assert parsed.pages[0].tables == []
        assert "strona pierwsza" in parsed.pages[0].text
        (table,) = parsed.pages[1].tables
        assert table[0] == ["Lp.", "Imie i nazwisko", "Data urodzenia"]
        assert len(table) == 3 and all(cell == "" for row in table[1:] for cell in row)
        spec = _form_table_spec(table)
        assert spec is not None and spec.columns == ["Imie i nazwisko", "Data urodzenia"]

    def test_pdf_page_tables_helper_normalizes_cells(self):
        class FakePage:
            def extract_tables(self):
                return [[["A", None], [None, None]], [[None, None]], []]

        assert _pdf_page_tables(FakePage()) == [[["A", ""], ["", ""]]]
