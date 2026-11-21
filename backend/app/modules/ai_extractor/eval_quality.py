"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Pomiar jakości i czasu realnego pipeline na prawdziwych formularzach (narzędzie deweloperskie,
uruchamiane ręcznie - NIE część runtime API).

Dla każdego pliku z katalogu: parser -> ekstraktor LLM -> enricher -> eksport FormDefinition ->
walidator reguł 1–12. Mierzy czas całości i każdego wywołania LLM (liczba wywołań, długość
promptów), a gdy obok pliku leży referencja `<nazwa>.reference.json` (oficjalny FormDefinition),
porównuje wynik z referencją: pokrycie pól po etykietach, zgodność typów, walidatorów
i słowników na dopasowanych polach, liczbę stron.

Konfiguracja modelu z ENV jak w aplikacji (LLM_BACKEND=local + MODELS_DIR albo LLM_BACKEND=api
z otwartym modelem na własnym serwerze). Uruchomienie:

    python -m backend.app.modules.ai_extractor.eval_quality testowe-formularze --report docs/jakosc-ekstrakcji.md
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import time
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.core.config import ALLOWED_EXTENSIONS, get_settings

INPUT_TYPES = {"TEXT_FIELD", "TEXTAREA", "NUMBER", "DATEPICKER", "SELECT", "RADIO", "CHECKBOX", "FILE_UPLOADER"}


def _norm(text: str | None) -> str:
    value = unicodedata.normalize("NFKD", (text or "").replace("ł", "l").replace("Ł", "L"))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    value = re.sub(r"\(opcjonalnie\)|[^a-z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def iter_input_fields(fd: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """(tytuł zakładki, pole wejściowe) dla wszystkich pól zbierających dane."""
    for tab in fd.get("tabs", []):
        for question in tab.get("questions", []):
            for child in question.get("field", {}).get("repetingSectionGrid") or []:
                item = child.get("field") or {}
                if item.get("type") in INPUT_TYPES:
                    yield tab.get("title", ""), item


def _validators(item: dict[str, Any]) -> set[str]:
    return {v for v in item.get("validation") or [] if isinstance(v, str) and v != "REQUIRED"}


def compare(result: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    """Porównanie po znormalizowanych etykietach (pole referencji dopasowane co najwyżej raz)."""
    ours: dict[str, list[dict[str, Any]]] = {}
    for _tab, item in iter_input_fields(result):
        ours.setdefault(_norm(item.get("title")), []).append(item)
    ref_fields = [item for _tab, item in iter_input_fields(reference)]
    matched = type_ok = validator_ok = dictionary_ok = required_ok = 0
    missing: list[str] = []
    for ref in ref_fields:
        candidates = ours.get(_norm(ref.get("title")))
        if not candidates:
            missing.append(ref.get("title") or ref.get("techName") or "?")
            continue
        got = candidates.pop(0)
        matched += 1
        type_ok += got.get("type") == ref.get("type")
        validator_ok += _validators(got) == _validators(ref)
        dictionary_ok += (got.get("dictionaryName") or "") == (ref.get("dictionaryName") or "")
        required_ok += ("REQUIRED" in (got.get("validation") or [])) == ("REQUIRED" in (ref.get("validation") or []))
    extra = sum(len(v) for v in ours.values())
    total = len(ref_fields) or 1

    def ratio(value: int, base: int) -> float:
        return round(value / base, 3) if base else 0.0

    return {
        "reference_fields": len(ref_fields),
        "recall": ratio(matched, total),
        "type": ratio(type_ok, matched),
        "validator": ratio(validator_ok, matched),
        "dictionary": ratio(dictionary_ok, matched),
        "required": ratio(required_ok, matched),
        "extra_fields": extra,
        "tabs": (len(result.get("tabs", [])), len(reference.get("tabs", []))),
        "missing": missing[:20],
    }


@dataclass
class _CallStats:
    calls: int = 0
    prompt_chars: int = 0
    seconds: list[float] = field(default_factory=list)


class _CountingBackend:
    """Opakowanie backendu LLM liczące wywołania, długość promptów i czas każdego wywołania."""

    def __init__(self, inner: Any, stats: _CallStats) -> None:
        self._inner = inner
        self._stats = stats

    def complete(self, messages: list[dict[str, str]], json_schema: dict[str, Any]) -> str:
        self._stats.calls += 1
        self._stats.prompt_chars += sum(len(m.get("content", "")) for m in messages)
        started = time.perf_counter()
        try:
            return self._inner.complete(messages, json_schema)
        finally:
            self._stats.seconds.append(time.perf_counter() - started)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def run_file(path: Path) -> dict[str, Any]:
    from backend.app.modules.ai_extractor.extractor import LlmStructureExtractor
    from backend.app.services.form_definition_validation import validate_form_definition
    from backend.app.services.pipeline_service import RealFormPipeline, run_conversion

    settings = get_settings()
    pipeline = RealFormPipeline(settings)
    stats = _CallStats()
    pipeline._extractor = LlmStructureExtractor(_CountingBackend(pipeline._llm_backend, stats))
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    started = time.perf_counter()
    response = run_conversion(pipeline, settings, path.name, path.read_bytes(), mime)
    elapsed = time.perf_counter() - started
    fd = response.form_definition or {}
    issues = validate_form_definition(fd)
    report: dict[str, Any] = {
        "file": path.name,
        "seconds": round(elapsed, 1),
        "llm_calls": stats.calls,
        "llm_seconds": round(sum(stats.seconds), 1),
        "prompt_chars": stats.prompt_chars,
        "tabs": len(fd.get("tabs", [])),
        "fields": sum(1 for _ in iter_input_fields(fd)),
        "errors": [f"{i.rule}: {i.message}" for i in issues if i.level == "error"],
        "warnings": len([i for i in issues if i.level == "warn"]),
    }
    reference = path.with_name(f"{path.stem}.reference.json")
    if reference.is_file():
        report["reference"] = compare(fd, json.loads(reference.read_text(encoding="utf-8")))
    return report


def render_markdown(reports: list[dict[str, Any]]) -> str:
    lines = [
        "| Plik | Czas [s] | Wywołania LLM | Czas LLM [s] | Prompt [znaki] | Strony | Pola | Błędy reguł | Ostrz. |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        if "failure" in r:
            lines.append(f"| {r['file']} | — | — | — | — | — | — | BŁĄD: {r['failure']} | — |")
            continue
        lines.append(
            f"| {r['file']} | {r['seconds']} | {r['llm_calls']} | {r['llm_seconds']} | {r['prompt_chars']} "
            f"| {r['tabs']} | {r['fields']} | {len(r['errors'])} | {r['warnings']} |"
        )
    with_ref = [r for r in reports if "reference" in r]
    if with_ref:
        lines += [
            "",
            "Porównanie z referencją (dopasowanie po etykietach):",
            "",
            "| Plik | Pól w ref. | Pokrycie | Typ | Walidator | Słownik | Wymagalność | Nadmiarowe | Strony (my/ref) |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in with_ref:
            c = r["reference"]
            lines.append(
                f"| {r['file']} | {c['reference_fields']} | {c['recall']:.0%} | {c['type']:.0%} "
                f"| {c['validator']:.0%} | {c['dictionary']:.0%} | {c['required']:.0%} | {c['extra_fields']} "
                f"| {c['tabs'][0]}/{c['tabs'][1]} |"
            )
    for r in reports:
        if r.get("errors"):
            lines += ["", f"Błędy reguł - {r['file']}:", *[f"- {e}" for e in r["errors"][:15]]]
        if r.get("reference", {}).get("missing"):
            lines += ["", f"Brakujące pola - {r['file']}: {', '.join(r['reference']['missing'])}"]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pomiar jakości ekstrakcji na prawdziwych formularzach.")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--report", type=Path, help="zapisz tabelę Markdown do pliku")
    args = parser.parse_args(argv)
    files = sorted(
        p for p in args.directory.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS and not p.name.endswith(".reference.json")
    )
    reports = []
    for path in files:
        print(f"→ {path.name}", file=sys.stderr, flush=True)
        try:
            reports.append(run_file(path))
        except Exception as exc:  # noqa: BLE001 - błąd jednego pliku nie przerywa serii pomiarów
            reports.append({"file": path.name, "failure": f"{type(exc).__name__}: {exc}"[:200]})
    table = render_markdown(reports)
    print(table)
    if args.report:
        args.report.write_text(table, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
