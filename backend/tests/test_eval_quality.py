"""Narzędzie pomiaru jakości: porównanie z referencją i licznik wywołań LLM (bez modelu)."""

import json

from backend.app.core.config import REPO_ROOT
from backend.app.modules.ai_extractor.eval_quality import (
    _CallStats,
    _CountingBackend,
    compare,
    render_markdown,
)

EXAMPLE = REPO_ROOT / "frontend" / "examples" / "wniosek-o-stwierdzenie-nadplaty-podatku.json"


def test_compare_identical_is_perfect():
    reference = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    result = compare(reference, reference)
    assert result["recall"] == 1.0
    assert result["type"] == result["validator"] == result["dictionary"] == result["required"] == 1.0
    assert result["extra_fields"] == 0


def test_compare_detects_missing_and_wrong_type():
    reference = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    result = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    grid = result["tabs"][0]["questions"][2]["field"]["repetingSectionGrid"]
    removed = grid.pop()["field"]["title"]
    grid[0]["field"]["type"] = "TEXTAREA"
    report = compare(result, reference)
    assert removed in report["missing"]
    assert report["recall"] < 1.0 and report["type"] < 1.0


def test_counting_backend_and_report():
    class Fake:
        def complete(self, messages, json_schema):
            return "{}"

        def ready(self):
            return True

    stats = _CallStats()
    backend = _CountingBackend(Fake(), stats)
    backend.complete([{"role": "user", "content": "abc"}], {})
    assert stats.calls == 1 and stats.prompt_chars == 3 and backend.ready()
    table = render_markdown([{"file": "a.pdf", "failure": "brak modelu"}])
    assert "BŁĄD: brak modelu" in table
