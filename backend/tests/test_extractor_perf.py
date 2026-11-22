"""Wydajność CPU: skracanie promptu (compact_text) i konfiguracja backendu lokalnego."""

from backend.app.core.config import Settings
from backend.app.modules.ai_extractor.extractor import compact_text
from backend.app.modules.ai_extractor.llm_backend import LocalLlamaBackend, build_llm_backend


def test_compact_text_keeps_content_and_marks_fields():
    text = (
        "Imię i nazwisko: " + "." * 42 + "\n"
        "PESEL: " + "_ " * 11 + "\n\n\n\n"
        "Adres     zamieszkania: " + "…" * 10 + "\n"
        "Kwota: 1.000,00 zł  Data: 2026-01-01  Tel. 600-100-200   \n"
    )
    out = compact_text(text)
    assert out == (
        "Imię i nazwisko: ____\n"
        "PESEL: ____\n\n"
        "Adres zamieszkania: ____\n"
        "Kwota: 1.000,00 zł Data: 2026-01-01 Tel. 600-100-200"
    )
    assert len(out) < len(text) / 1.5


def test_compact_text_on_typical_paper_form_is_much_shorter():
    line = "Podpis wnioskodawcy " + "." * 80 + "   Data " + "_" * 30 + "\n"
    text = line * 40
    assert len(compact_text(text)) < len(text) / 3


def test_local_backend_threads_and_ctx_from_settings(tmp_path):
    settings = Settings(_env_file=None, MODELS_DIR=tmp_path, LLM_THREADS=6, LLM_CTX=4096)
    backend = build_llm_backend(settings)
    assert isinstance(backend, LocalLlamaBackend)
    assert backend._n_threads == 6 and backend._n_ctx == 4096


def test_cpu_threads_default_uses_physical_cores_for_generation(monkeypatch):
    from backend.app.modules.ai_extractor import llm_backend

    monkeypatch.setattr(llm_backend, "_logical_cpus", lambda: 12)
    assert llm_backend.cpu_threads(0) == (6, 12)
    assert llm_backend.cpu_threads(4) == (4, 12)
    monkeypatch.setattr(llm_backend, "_logical_cpus", lambda: 1)
    assert llm_backend.cpu_threads(0) == (1, 1)


def test_llm_schema_has_no_name_and_ids_come_from_labels():
    import json

    from backend.app.modules.ai_extractor.extractor import LlmStructureExtractor, _LlmDocument, _LlmField

    assert '"name"' not in json.dumps(_LlmDocument.model_json_schema())
    field = _LlmField.model_validate({"label": "Kwota nadpłaty", "field_type": "number"})
    result = LlmStructureExtractor(None)._to_field_definition(field, set())
    assert result.field_id == "kwota_nadplaty" and result.name == "kwota_nadplaty"
