"""Ekstraktor na backendzie LLM: delegacja, walidacja JSON i ścieżki bez modelu."""

import json

import pytest

from backend.app.core.exceptions import PipelineNotReadyError
from backend.app.interfaces.pipeline import ParsedDocument, ParsedPage
from backend.app.modules.ai_extractor.extractor import MAX_PAGE_CHARS, LlmStructureExtractor


class FakeBackend:
    def __init__(self, *payloads: str, ready: bool = True) -> None:
        self.payloads = list(payloads)
        self.calls: list[list[dict[str, str]]] = []
        self.schemas: list[dict] = []
        self._ready = ready

    @property
    def model_name(self) -> str:
        return "fake-model"

    def ready(self) -> bool:
        return self._ready

    def complete(self, messages, json_schema) -> str:
        self.calls.append(messages)
        self.schemas.append(json_schema)
        return self.payloads.pop(0)


def document_payload() -> dict:
    return {
        "title": "Wniosek o zwrot nadpłaty podatku",
        "pages": [
            {
                "title": "Przedmiot wniosku",
                "sections": [
                    {
                        "title": "Dane wniosku",
                        "fields": [
                            {"name": "kwota_nadplaty", "label": "Kwota nadpłaty", "field_type": "number"},
                            {"name": "rok_podatkowy", "label": "Rok podatkowy", "field_type": "number"},
                        ],
                    },
                    {
                        "title": "Uzasadnienie",
                        "fields": [
                            {"name": "uzasadnienie", "label": "Uzasadnienie", "field_type": "textarea"}
                        ],
                    },
                ],
            }
        ],
    }


def parsed_document(text: str = "Kwota nadpłaty: 120 zł\nRok podatkowy: 2025") -> ParsedDocument:
    return ParsedDocument(
        source_filename="wniosek_o_zwrot.docx",
        source_format="docx",
        pages=[ParsedPage(page_number=1, text=text)],
    )


def test_delegates_to_backend_and_maps_document():
    backend = FakeBackend(json.dumps(document_payload()))
    form = LlmStructureExtractor(backend).extract(parsed_document())
    assert form.metadata.title == "Wniosek o zwrot nadpłaty podatku"
    assert len(form.pages) == 2, "jedna strona z dwiema sekcjami jest dzielona tematycznie"
    assert len(backend.calls) == 1
    assert backend.calls[0][0]["role"] == "system"
    assert backend.calls[0][1] == {"role": "user", "content": "Kwota nadpłaty: 120 zł\nRok podatkowy: 2025"}
    assert "pages" in backend.schemas[0]["properties"]
    field_ids = [f.field_id for p in form.pages for s in p.sections for f in s.fields]
    assert len(field_ids) == len(set(field_ids))


def test_strips_json_code_fence():
    fenced = f"```json\n{json.dumps(document_payload())}\n```"
    form = LlmStructureExtractor(FakeBackend(fenced)).extract(parsed_document())
    assert form.metadata.title == "Wniosek o zwrot nadpłaty podatku"


def test_extra_field_is_rejected():
    payload = document_payload() | {"niespodziewane": True}
    with pytest.raises(PipelineNotReadyError) as exc:
        LlmStructureExtractor(FakeBackend(json.dumps(payload))).extract(parsed_document())
    assert exc.value.status_code == 503
    assert exc.value.details["model"] == "fake-model"


def test_invalid_json_is_rejected():
    with pytest.raises(PipelineNotReadyError) as exc:
        LlmStructureExtractor(FakeBackend("to nie jest JSON")).extract(parsed_document())
    assert exc.value.status_code == 503
    assert "niepoprawną strukturę JSON" in exc.value.message


def test_one_call_per_chunk():
    backend = FakeBackend(json.dumps(document_payload()), json.dumps(document_payload()))
    form = LlmStructureExtractor(backend).extract(parsed_document("A" * (MAX_PAGE_CHARS + 10)))
    assert len(backend.calls) == 2
    assert form.metadata.title == "Wniosek o zwrot nadpłaty podatku"


def test_not_ready_backend_is_503_even_without_text():
    backend = FakeBackend(ready=False)
    with pytest.raises(PipelineNotReadyError) as exc:
        LlmStructureExtractor(backend).extract(parsed_document(""))
    assert exc.value.status_code == 503
    assert backend.calls == []


def test_empty_document_with_ready_backend_uses_filename_fallback():
    backend = FakeBackend()
    form = LlmStructureExtractor(backend).extract(parsed_document(""))
    assert backend.calls == []
    assert form.metadata.title == "wniosek o zwrot"
