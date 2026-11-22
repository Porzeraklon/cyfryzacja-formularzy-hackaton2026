"""Eksport wewnętrznego FormDocumentSchema do oficjalnego FormDefinition COI.

Wierny port fdConvertInternal z frontend/formdef.js (Branch 2) plus rozszerzenia
uzgodnione dla pipeline'u: rekurencyjne przepływy (także kolumny tabel), złożone
operatory ALL/ANY, akcja READ_ONLY, wewnętrzne typy CONSENT_SECTION/TEXT_BLOCK/
INFO_SECTION, dane inicjalne (readOnly/defaultValueType) i realne rozmiary
słowników. Emituje pisownię ze schematu oficjalnego: `repetingSectionGrid`.
100% offline — bez wywołań zewnętrznych.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from backend.app.schemas.api_models import DictionaryDetailResponse
from backend.app.schemas.form_schema import (
    ConditionalFlow,
    ConditionOperator,
    CustomValidator,
    DictionaryItem,
    FieldDefinition,
    FieldType,
    FlowAction,
    FormDocumentSchema,
    PageDefinition,
    SectionDefinition,
    TargetType,
)

# ---------------------------------------------------------------------------
# Katalogi COI (Reguły techniczne + Standardy) — odpowiedniki stałych z formdef.js
# ---------------------------------------------------------------------------

FD_TYPE_MAP: dict[FieldType, str] = {
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

FD_CUSTOM_VALIDATOR_MAP: dict[str, str] = {validator.value: validator.value for validator in CustomValidator}

# Granice słów w stylu JS \b (ASCII), żeby polskie znaki nie zmieniały dopasowań.
_WB = r"(?<![A-Za-z0-9_])"
_WA = r"(?![A-Za-z0-9_])"

FD_LABEL_VALIDATORS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"pesel", re.IGNORECASE), "PESEL"),
    (re.compile(_WB + r"nip" + _WA, re.IGNORECASE), "NIP"),
    (re.compile(_WB + r"regon" + _WA, re.IGNORECASE), "REGON"),
    (re.compile(r"kod pocztowy", re.IGNORECASE), "ZIP_CODE"),
    (re.compile(r"e-?mail", re.IGNORECASE), "EMAIL"),
    (re.compile(r"epuap|skrytk", re.IGNORECASE), "EPUAP"),
    (re.compile(r"e-?dor[eę]cze|" + _WB + r"ade" + _WA, re.IGNORECASE), "ADE"),
    (re.compile(r"seria i numer dowodu|numer dowodu osobistego", re.IGNORECASE), "SERIAL_NUMBER_ID_CARD"),
]

FD_LABEL_DICTIONARIES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"^kraj" + _WA + r"|^państwo" + _WA + r"|obywatelstwo|kraj urodzenia|kraj zamieszkania",
            re.IGNORECASE,
        ),
        "Kraje",
    ),
    (re.compile(r"^województwo", re.IGNORECASE), "Województwo"),
    (re.compile(r"^powiat", re.IGNORECASE), "Powiat"),
    (re.compile(r"^gmina", re.IGNORECASE), "Gmina"),
    (re.compile(r"numer kierunkowy|prefiks telefon", re.IGNORECASE), "Numery kierunkowe"),
]

FD_OWN_DATA_SECTION = re.compile(
    r"twoje dane|dane wnioskodawcy|dane osobowe wnioskodawcy|dane osoby sk[łl]adaj", re.IGNORECASE
)
FD_OWN_DATA_EXCLUDE = re.compile(r"innej osoby|pe[łl]nomocnik|podmiot", re.IGNORECASE)
FD_OWN_DATA_FIELDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^imi[eę]( pierwsze)?$", re.IGNORECASE), "MLPZ.name"),
    (re.compile(r"^nazwisko$", re.IGNORECASE), "MLPZ.surname"),
    (re.compile(r"pesel", re.IGNORECASE), "MLPZ.pesel"),
]
FD_CONSENT_LABEL = re.compile(
    r"o[śs]wiadczam|wyra[żz]am zgod|zapozna[łl]|akceptuj|zgod[ay] na", re.IGNORECASE
)

# 6 oficjalnych słowników COI. builtin_count = liczba pozycji wbudowanych w JS
# (gdy brak danych z backendu); słowniki TERYT bez pozycji → rozmiar zastępczy 99.
FD_DICTIONARIES: list[dict[str, Any]] = [
    {
        "name": "Województwo",
        "aliases": ["WOJEWODZTWO", "WOJEWODZTWA", "dict_voivodeships"],
        "backend_id": "dict_voivodeships",
        "allowed_types": ("SELECT",),
        "builtin_count": 16,
    },
    {
        "name": "Powiat",
        "aliases": ["POWIAT", "POWIATY", "dict_districts"],
        "backend_id": "dict_districts",
        "allowed_types": ("SELECT",),
        "builtin_count": 0,
    },
    {
        "name": "Gmina",
        "aliases": ["GMINA", "GMINY", "dict_communities"],
        "backend_id": "dict_communities",
        "allowed_types": ("SELECT",),
        "builtin_count": 0,
    },
    {
        "name": "Miejscowość",
        "aliases": ["MIEJSCOWOSC", "MIEJSCOWOSCI", "TERYT_4G_DOWODOWY", "dict_places"],
        "backend_id": "dict_places",
        "allowed_types": ("SELECT",),
        "builtin_count": 0,
    },
    {
        "name": "Kraje",
        "aliases": ["KRAJE", "KRAJ", "PANSTWA", "dict_countries"],
        "backend_id": "dict_countries",
        "allowed_types": ("SELECT",),
        "builtin_count": 98,
    },
    {
        "name": "Numery kierunkowe",
        "aliases": ["NUMERY_KIERUNKOWE", "NUMERY KIERUNKOWE", "dict_dial_codes"],
        "backend_id": "dict_dial_codes",
        "allowed_types": ("SELECT", "RADIO", "CHECKBOX"),
        "builtin_count": 45,
    },
]

_CHOICE_TYPES = ("SELECT", "RADIO", "CHECKBOX")


# ---------------------------------------------------------------------------
# Pomocnicze algorytmy JS (fdUuid/fdNormalize/fdSlug/fdQuote) — port 1:1
# ---------------------------------------------------------------------------


def _u32(n: int) -> int:
    return n & 0xFFFFFFFF


def _i32(n: int) -> int:
    n &= 0xFFFFFFFF
    return n - 0x100000000 if n >= 0x80000000 else n


def _imul(a: int, b: int) -> int:
    return _i32(a * b)


def fd_uuid(seed: str) -> str:
    """Deterministyczny UUID v4 z nasiona (port fdUuid; charCodeAt = jednostki UTF-16)."""
    h1, h2, h3, h4 = 1779033703, 3144134277, 1013904242, 2773480762
    units = seed.encode("utf-16-le")
    for i in range(0, len(units), 2):
        k = units[i] | (units[i + 1] << 8)
        h1 = h2 ^ _imul(h1 ^ k, 597399067)
        h2 = h3 ^ _imul(h2 ^ k, 2869860233)
        h3 = h4 ^ _imul(h3 ^ k, 951274213)
        h4 = h1 ^ _imul(h4 ^ k, 2716044179)
    h1 = _imul(h3 ^ (_u32(h1) >> 18), 597399067)
    h2 = _imul(h4 ^ (_u32(h2) >> 22), 2869860233)
    h3 = _imul(h1 ^ (_u32(h3) >> 17), 951274213)
    h4 = _imul(h2 ^ (_u32(h4) >> 19), 2716044179)
    hex_str = "".join(f"{_u32(n):08x}" for n in (h1 ^ h2 ^ h3 ^ h4, h2 ^ h1, h3 ^ h1, h4 ^ h1))
    variant = format((int(hex_str[16], 16) & 0x3) | 0x8, "x")
    return f"{hex_str[0:8]}-{hex_str[8:12]}-4{hex_str[13:16]}-{variant}{hex_str[17:20]}-{hex_str[20:32]}"


def fd_normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or ""))
    stripped = re.sub(r"[\u0300-\u036f]", "", decomposed).replace("ł", "l").replace("Ł", "L")
    return re.sub(r"[\s-]+", "_", stripped.strip().upper())


def fd_slug(text: str, fallback: str = "pole") -> str:
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    stripped = re.sub(r"[\u0300-\u036f]", "", decomposed).replace("ł", "l").replace("Ł", "l")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", stripped).strip("_").lower()[:60]
    return slug or fallback


def fd_quote(text: str) -> str:
    return json.dumps(str(text), ensure_ascii=False)


def _js_number(value: float) -> str:
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))
    return repr(value)


def _js_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return _js_number(value)
    return str(value)


_FD_DICT_INDEX: dict[str, dict[str, Any]] = {
    fd_normalize(key): entry for entry in FD_DICTIONARIES for key in [entry["name"], *entry["aliases"]]
}


def fd_find_dictionary(name: str | None) -> dict[str, Any] | None:
    if not name:
        return None
    return _FD_DICT_INDEX.get(fd_normalize(name))


def fd_field_path(ref: _Ref) -> str:
    return (
        f"tabs[{fd_quote(ref.tab_title)} in title].questions"
        f"[{fd_quote(ref.section_tech)} in field.techName].field.repetingSectionGrid"
        f"[{fd_quote(ref.tech_name)} in field.techName].field"
    )


def iter_fields(fields: Sequence[FieldDefinition]) -> Iterator[FieldDefinition]:
    """Pola sekcji wraz z kolumnami grup powtarzalnych (rekurencyjnie)."""
    for field in fields:
        yield field
        if field.fields:
            yield from iter_fields(field.fields)


@dataclass
class _Ref:
    tab_title: str
    section_tech: str
    tech_name: str
    type: str
    options: list[DictionaryItem]
    dict_ref: str | None
    out: dict[str, Any]


@dataclass
class _Ctx:
    tab_title: str
    section_tech: str
    section_title: str
    tab_index: int


class _Converter:
    def __init__(
        self, internal: FormDocumentSchema, known_dictionaries: Sequence[DictionaryDetailResponse]
    ) -> None:
        self._internal = internal
        self._notes: list[str] = []
        self._seed = internal.metadata.form_id or internal.metadata.title or "form"
        self._issued_ids: set[str] = set()
        self._used_tech: set[str] = set()
        self._question_order = 0
        self._refs: dict[str, _Ref] = {}
        self._section_refs: dict[str, dict[str, Any]] = {}
        self._dropped_rules: list[str] = []
        self._known_by_id = {d.dictionary_id: d for d in known_dictionaries}
        self._known_items = {d.dictionary_id: d.items for d in known_dictionaries}
        self._mapped_names = {
            m.dictionary_id: m.dictionary_name for m in internal.dictionaries.mapped_dictionaries
        }
        self._proposed_by_id = {p.proposed_id: p for p in internal.dictionaries.proposed_dictionaries}

    def _uid(self, key: str) -> str:
        # Deterministyczny UUID z klucza; ten sam klucz drugi raz (np. pole i grupa o tym samym
        # field_id z LLM) dostaje kolejny wariant, bo identyfikatory w FormDefinition są unikalne.
        uid = fd_uuid(f"{self._seed}:{key}")
        suffix = 2
        while uid in self._issued_ids:
            uid = fd_uuid(f"{self._seed}:{key}#{suffix}")
            suffix += 1
        self._issued_ids.add(uid)
        return uid

    def _unique_tech(self, base: str) -> str:
        candidate = fd_slug(base)
        suffix = 2
        while candidate in self._used_tech:
            candidate = f"{fd_slug(base)}_{suffix}"
            suffix += 1
        self._used_tech.add(candidate)
        return candidate

    def _next_order(self) -> int:
        self._question_order += 1
        return self._question_order

    def _dict_size(self, name: str | None) -> int:
        entry = fd_find_dictionary(name)
        if entry is None:
            return 99 if name else 0
        items = self._known_items.get(entry["backend_id"])
        if items is not None:
            return len(items)
        return entry["builtin_count"] or 99

    def _option_name(self, code: Any, source: _Ref) -> str:
        text = _js_string(code)
        for option in source.options:
            if option.code == text or option.label == text:
                return option.label
        if source.dict_ref:
            for option in self._known_items.get(source.dict_ref) or []:
                if option.code == text or option.label == text:
                    return option.label
        return text

    # ------------------------------------------------------------------
    # Warunki jsonata (fdConditionQuery + rozszerzenia ALL/ANY/READ_ONLY)
    # ------------------------------------------------------------------

    def _simple_query(self, operator: ConditionOperator, value: Any, source: _Ref) -> str | None:
        path = fd_field_path(source) + ".__userAnswer"
        if operator == ConditionOperator.EQUALS:
            if source.type == "CHECKBOX" and not source.options:
                flag = value is True or value == "true"
                return f"{path} = {'true' if flag else 'false'}"
            return f"{fd_quote(self._option_name(value, source))} in {path}"
        if operator == ConditionOperator.NOT_EQUALS:
            return f"$not({fd_quote(self._option_name(value, source))} in {path})"
        if operator == ConditionOperator.IN:
            values = value if isinstance(value, list) else [value]
            parts = [f"{fd_quote(self._option_name(v, source))} in {path}" for v in values]
            return "(" + " or ".join(parts) + ")"
        if operator == ConditionOperator.IS_EMPTY:
            return f'($not($exists({path})) or {path} = "")'
        if operator == ConditionOperator.GREATER_THAN:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return f"$number({path}) > {_js_number(number)}"
        return None

    def _compound_query(self, operator: ConditionOperator, subconditions: Any) -> str | None:
        if not isinstance(subconditions, list):
            return None
        parts: list[str] = []
        for sub in subconditions:
            if not isinstance(sub, dict):
                continue
            raw_op = sub.get("operator")
            op_name = raw_op.value if isinstance(raw_op, ConditionOperator) else raw_op
            if op_name in (ConditionOperator.ALL.value, ConditionOperator.ANY.value):
                query = self._compound_query(ConditionOperator(op_name), sub.get("value"))
            else:
                ref = self._refs.get(sub.get("field_id"))
                if ref is None:
                    continue
                try:
                    sub_operator = ConditionOperator(op_name)
                except ValueError:
                    continue
                query = self._simple_query(sub_operator, sub.get("value"), ref)
            if query:
                parts.append(query)
        if not parts:
            return None
        joiner = " and " if operator == ConditionOperator.ALL else " or "
        return "(" + joiner.join(parts) + ")"

    def _condition_query(self, flow: ConditionalFlow, source: _Ref) -> str | None:
        if flow.operator in (ConditionOperator.ALL, ConditionOperator.ANY):
            return self._compound_query(flow.operator, flow.value)
        return self._simple_query(flow.operator, flow.value, source)

    # ------------------------------------------------------------------
    # Pola i sekcje
    # ------------------------------------------------------------------

    def _convert_field(self, f: FieldDefinition, ctx: _Ctx, y: int) -> dict[str, Any]:
        ftype = FD_TYPE_MAP.get(f.field_type, "TEXT_FIELD")
        label = f.label or f.name or "Pole"
        tech_name = self._unique_tech(f.name or f.field_id or label)

        if f.field_type in (FieldType.TEXT_BLOCK, FieldType.INFO_SECTION):
            out: dict[str, Any] = {
                "id": self._uid(f"field:{f.field_id}"),
                "type": ftype,
                "techName": tech_name,
                "title": f.label,
                "description": f.help_text or "",
                "states": [],
                "questionOrder": self._next_order(),
            }
            self._refs[f.field_id] = _Ref(ctx.tab_title, ctx.section_tech, tech_name, ftype, [], None, out)
            return {"x": 0, "y": y, "rows": 1, "cols": 1, "field": out}

        rules = f.validation_rules
        validation = ["REQUIRED"] if f.required else []
        custom = rules.custom_validator.value if rules and rules.custom_validator else None
        if custom:
            mapped = FD_CUSTOM_VALIDATOR_MAP.get(custom)
            if mapped:
                if mapped not in validation:
                    validation.append(mapped)
            else:
                self._notes.append(
                    f"Pole „{label}”: walidator {custom} nie występuje w katalogu COI — pominięto."
                )
        if rules and (rules.pattern or rules.min_length is not None or rules.max_length is not None):
            self._dropped_rules.append(label)
        # Heurystyki etykiet tylko jako fallback (bez jawnych reguł walidacji).
        if ftype == "TEXT_FIELD" and rules is None:
            hit = next((name for pattern, name in FD_LABEL_VALIDATORS if pattern.search(label)), None)
            if hit and hit not in validation:
                validation.append(hit)

        dict_ref = f.dictionary_ref
        official = fd_find_dictionary(dict_ref) if dict_ref else None
        dictionary_name = official["name"] if official else None
        if (
            official
            and ftype in ("TEXT_FIELD", "RADIO", "CHECKBOX")
            and ftype not in official["allowed_types"]
        ):
            ftype = "SELECT"
            self._notes.append(
                f"Pole „{label}”: słownik {official['name']} wymaga typu SELECT — zmieniono typ pola."
            )
        if (
            not dictionary_name
            and not dict_ref
            and ftype in ("TEXT_FIELD", "SELECT")
            and f.default_value_type != "EXTERNAL_SYSTEM"
        ):
            hit = next((name for pattern, name in FD_LABEL_DICTIONARIES if pattern.search(label)), None)
            if hit:
                dictionary_name = hit
                if ftype != "SELECT":
                    self._notes.append(f"Pole „{label}”: zamieniono na listę rozwijaną ze słownikiem {hit}.")
                    ftype = "SELECT"

        options: list[dict[str, Any]] = []
        if not dictionary_name and ftype in _CHOICE_TYPES:
            items: list[DictionaryItem] = list(f.options or [])
            if not items and dict_ref:
                proposed = self._proposed_by_id.get(dict_ref)
                known = self._known_by_id.get(dict_ref)
                if proposed and proposed.items:
                    items = list(proposed.items)
                elif known and known.items:
                    items = list(known.items)
                if not items:
                    self._notes.append(
                        f"Pole „{label}”: słownik {dict_ref} nie należy do słowników COI i nie ma pozycji."
                    )
                elif proposed and proposed.items:
                    self._notes.append(
                        f"Pole „{label}”: proponowany słownik {dict_ref} wstawiono jako opcje pola "
                        "(w COI go nie ma)."
                    )
                else:
                    self._notes.append(
                        f"Pole „{label}”: słownik spoza katalogu COI "
                        f"({self._mapped_names.get(dict_ref) or dict_ref}) wstawiono jako opcje pola."
                    )
            elif dict_ref and dict_ref in self._proposed_by_id:
                self._notes.append(
                    f"Pole „{label}”: proponowany słownik {dict_ref} wstawiono jako opcje pola "
                    "(w COI go nie ma)."
                )
            options = [
                {
                    "id": self._uid(f"opt:{f.field_id}:{o.code}"),
                    "name": o.label,
                    "techName": fd_slug(o.code, "opcja"),
                }
                for o in items
            ]

        out = {
            "id": self._uid(f"field:{f.field_id}"),
            "type": ftype,
            "techName": tech_name,
            "title": label,
            "description": f.help_text or "",
            "options": options,
            "validation": validation,
            "readOnly": bool(f.read_only),
            "defaultValue": _js_string(f.initial_value),
            "defaultValueType": f.default_value_type,
            "states": [],
            "questionOrder": self._next_order(),
            "tooltipMessage": (
                f"Np. {re.sub(r'^np\.\s*', '', f.placeholder, flags=re.IGNORECASE)}" if f.placeholder else ""
            ),
        }
        if dictionary_name:
            out["dictionaryName"] = dictionary_name
            if ftype == "SELECT" and (self._dict_size(dictionary_name) > 15 or len(options) > 15):
                out["searchEnabled"] = True
            if dictionary_name == "Kraje" and not out["defaultValue"]:
                out["defaultValue"] = "Polska"

        if (
            out["defaultValueType"] != "EXTERNAL_SYSTEM"
            and ctx.tab_index == 0
            and (FD_OWN_DATA_SECTION.search(ctx.section_title) or FD_OWN_DATA_SECTION.search(ctx.tab_title))
            and not FD_OWN_DATA_EXCLUDE.search(ctx.section_title)
        ):
            hit = next((source for pattern, source in FD_OWN_DATA_FIELDS if pattern.search(label)), None)
            if hit:
                out["readOnly"] = True
                out["defaultValue"] = hit
                out["defaultValueType"] = "EXTERNAL_SYSTEM"
                self._notes.append(
                    f"Pole „{label}”: dane inicjalne {hit} (sekcja „Twoje dane”, tylko do odczytu)."
                )

        self._refs[f.field_id] = _Ref(
            ctx.tab_title, ctx.section_tech, tech_name, ftype, list(f.options or []), dict_ref, out
        )
        return {"x": 0, "y": y, "rows": 1, "cols": 1, "field": out}

    def _make_section(
        self, type_: str, title: str, tech: str, description: str | None, y: int
    ) -> dict[str, Any]:
        field = {
            "id": self._uid(f"section:{tech}"),
            "type": type_,
            "techName": self._unique_tech(tech),
            "title": title,
            "description": description or "",
            "states": [],
            "questionOrder": self._next_order(),
            "repetingSectionGrid": [],
        }
        return {"x": 0, "y": y, "rows": 1, "cols": 1, "field": field}

    def _append_consent_field(self, f: FieldDefinition, q: dict[str, Any], ctx: _Ctx) -> None:
        consent_items = f.consents or []
        consents = [{"description": c.description, "required": bool(c.required)} for c in consent_items]
        any_required = bool(f.required) or any(c.required for c in consent_items)
        grid = q["field"]["repetingSectionGrid"]
        child = {
            "id": self._uid(f"consent:{f.field_id}"),
            "type": "CONSENT_SECTION",
            "techName": self._unique_tech(f.name or f.field_id),
            "title": f.label or q["field"]["title"],
            "validation": ["REQUIRED"] if any_required else [],
            "selectAllEnabled": len(consents) > 1,
            "questionOrder": self._next_order(),
            "states": [],
            "consentSectionConfig": {"consents": consents, "required": any_required, "files": []},
        }
        grid.append({"x": 0, "y": len(grid), "rows": 1, "cols": 1, "field": child})
        self._refs[f.field_id] = _Ref(
            ctx.tab_title, ctx.section_tech, child["techName"], "CONSENT_SECTION", [], None, child
        )

    def _convert_section(self, section: SectionDefinition, tab: dict[str, Any], tab_index: int) -> None:
        fields = section.fields
        groups = [f for f in fields if f.field_type == FieldType.REPEATABLE_GROUP]
        consent_fields = [f for f in fields if f.field_type == FieldType.CONSENT_SECTION]
        plain = [
            f for f in fields if f.field_type not in (FieldType.REPEATABLE_GROUP, FieldType.CONSENT_SECTION)
        ]
        consent_like = bool(fields) and all(
            f.field_type == FieldType.CHECKBOX and FD_CONSENT_LABEL.search(f.label or "") for f in fields
        )
        if plain or consent_fields or not groups:
            q = self._make_section(
                "SECTION",
                section.title,
                section.section_id or section.title,
                section.description,
                len(tab["questions"]),
            )
            ctx = _Ctx(tab["title"], q["field"]["techName"], section.title, tab_index)
            self._section_refs[section.section_id] = q["field"]
            grid = q["field"]["repetingSectionGrid"]
            if consent_like and not consent_fields:
                any_required = any(f.required for f in fields)
                child = {
                    "id": self._uid(f"consent:{section.section_id}"),
                    "type": "CONSENT_SECTION",
                    "techName": self._unique_tech(f"{section.section_id}_zgody"),
                    "title": section.title,
                    "validation": ["REQUIRED"] if any_required else [],
                    "selectAllEnabled": len(fields) > 1,
                    "questionOrder": self._next_order(),
                    "states": [],
                    "consentSectionConfig": {
                        "consents": [{"description": f.label, "required": bool(f.required)} for f in fields],
                        "required": any_required,
                        "files": [],
                    },
                }
                grid.append({"x": 0, "y": 0, "rows": 1, "cols": 1, "field": child})
                self._notes.append(
                    f"Sekcja „{section.title}”: oświadczenia z checkboxów zamieniono na sekcję zgód "
                    "CONSENT_SECTION (Standardy: zgody nie jako checkbox)."
                )
            else:
                for i, f in enumerate(plain):
                    grid.append(self._convert_field(f, ctx, i))
                for consent_field in consent_fields:
                    self._append_consent_field(consent_field, q, ctx)
            if not plain and not consent_fields:
                info = {
                    "id": self._uid(f"info:{section.section_id}"),
                    "type": "TEXT_BLOCK",
                    "techName": self._unique_tech(f"{section.section_id}_opis"),
                    "title": "",
                    "description": section.description or section.title,
                    "states": [],
                    "questionOrder": self._next_order(),
                }
                grid.append({"x": 0, "y": 0, "rows": 1, "cols": 1, "field": info})
            tab["questions"].append(q)
        for group in groups:
            q = self._make_section(
                "REPEATING_SECTION",
                group.label or section.title,
                group.name or group.field_id,
                group.help_text,
                len(tab["questions"]),
            )
            ctx = _Ctx(tab["title"], q["field"]["techName"], section.title, tab_index)
            children = group.fields or []
            for i, sub in enumerate(children):
                q["field"]["repetingSectionGrid"].append(self._convert_field(sub, ctx, i))
            if not children:
                self._notes.append(
                    f"Sekcja powtarzająca się „{q['field']['title']}” nie ma kolumn — "
                    "backend nie zwrócił pól tabeli."
                )
            self._section_refs[group.field_id] = q["field"]
            tab["questions"].append(q)
            self._notes.append(
                f"Grupa „{q['field']['title']}” → REPEATING_SECTION na poziomie strony "
                "(reguła 6: bez zagnieżdżania sekcji)."
            )

    # ------------------------------------------------------------------
    # Stany, FINISH, linki
    # ------------------------------------------------------------------

    def _apply_states(self, pages: list[PageDefinition]) -> None:
        all_flows: list[ConditionalFlow] = list(self._internal.conditional_flows)
        for page in pages:
            all_flows.extend(page.visibility_rules)
            for section in page.sections:
                all_flows.extend(section.visibility_rules)
                for field in iter_fields(section.fields):
                    all_flows.extend(field.dependencies)
        seen: set[str] = set()
        for flow in all_flows:
            key = json.dumps(flow.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            source = self._refs.get(flow.source_field_id)
            if flow.target_type == TargetType.SECTION:
                target = self._section_refs.get(flow.target_id)
            elif flow.target_type == TargetType.FIELD:
                ref = self._refs.get(flow.target_id)
                target = ref.out if ref else None
            else:
                target = None
            if source is None or target is None:
                self._notes.append(
                    f"Zależność {flow.source_field_id} → {flow.target_id}: "
                    "brak pola/sekcji lub cel typu strona — pominięto."
                )
                continue
            query = self._condition_query(flow, source)
            if query is None:
                continue
            if flow.action == FlowAction.HIDE:
                query = f"$not({query})"
            if flow.action in (FlowAction.SHOW, FlowAction.HIDE):
                target["states"].append({"type": "SHOW", "query": query})
            elif flow.action == FlowAction.READ_ONLY and flow.target_type == TargetType.FIELD:
                target["states"].append({"type": "READ_ONLY", "query": query})
            else:
                self._notes.append(
                    f"Zależność {flow.action.value} ({flow.source_field_id} → {flow.target_id}) "
                    "nie ma odpowiednika w COI (dostępne: SHOW, READ_ONLY) — pominięto."
                )

    def _finish_section(self, tab: dict[str, Any], tech: str, title: str, text: str, y: int) -> None:
        section = {
            "id": self._uid(f"section:{tech}"),
            "type": "SECTION",
            "techName": self._unique_tech(tech),
            "title": title,
            "states": [],
            "questionOrder": self._next_order(),
            "repetingSectionGrid": [
                {
                    "x": 0,
                    "y": 0,
                    "rows": 1,
                    "cols": 1,
                    "field": {
                        "id": self._uid(f"info:{tech}"),
                        "type": "INFO_SECTION",
                        "techName": self._unique_tech(f"{tech}_info"),
                        "description": text,
                        "states": [],
                        "questionOrder": self._next_order(),
                    },
                }
            ],
        }
        tab["questions"].append({"x": 0, "y": y, "rows": 1, "cols": 1, "field": section})

    def convert(self) -> dict[str, Any]:
        internal = self._internal
        raw_title = str(internal.metadata.title or "Formularz")
        title = raw_title[:255]
        if len(raw_title) > 255:
            self._notes.append("Tytuł przycięto do 255 znaków (reguła 1).")
        pages = sorted(internal.pages, key=lambda p: p.order)
        tabs: list[dict[str, Any]] = []
        for tab_index, page in enumerate(pages):
            tab = {
                "id": self._uid(f"tab:{page.page_id}"),
                "title": page.title or f"Strona {tab_index + 1}",
                "type": "MAIN",
                "questions": [],
            }
            if page.visibility_rules:
                self._notes.append(
                    f"Strona „{tab['title']}”: warunki widoczności stron nie mają odpowiednika "
                    "w FormDefinition (stany są tylko na polach i sekcjach) — pominięto."
                )
            for section in sorted(page.sections, key=lambda s: s.order):
                self._convert_section(section, tab, tab_index)
            tabs.append(tab)

        self._apply_states(pages)

        finish = {
            "id": self._uid("tab:finish"),
            "title": "Podsumowanie",
            "type": "FINISH",
            "questions": [],
        }
        self._finish_section(
            finish,
            "podsumowanie",
            "Podsumowanie",
            "Po wysłaniu wniosku urząd rozpatrzy sprawę. Jeśli będą potrzebne dodatkowe informacje, "
            "urzędnik skontaktuje się z Tobą.",
            0,
        )
        self._finish_section(
            finish,
            "podglad",
            "Podgląd",
            "Zanim przejdziesz do wyboru sposobu podpisu, sprawdź dane na podglądzie. "
            "Jeśli zobaczysz błędy, wróć do edycji.",
            1,
        )
        tabs.append(finish)

        if self._dropped_rules:
            self._notes.append(
                f"Reguły długości/regex ({', '.join(self._dropped_rules)}) nie mają odpowiednika "
                "w FormDefinition — pominięto."
            )
        links = [
            {"id": self._uid(f"link:{i}"), "source": tabs[i]["id"], "target": tabs[i + 1]["id"]}
            for i in range(len(tabs) - 1)
        ]
        return {"form": {"title": title, "tabs": tabs, "flow": {"links": links}}, "notes": self._notes}


def convert_formdefinition(
    internal: FormDocumentSchema | dict[str, Any],
    known_dictionaries: Sequence[DictionaryDetailResponse] | None = None,
) -> dict[str, Any]:
    """Zwraca {"form": FormDefinition, "notes": [ostrzeżenia o pominiętych regułach]}."""
    form = (
        internal if isinstance(internal, FormDocumentSchema) else FormDocumentSchema.model_validate(internal)
    )
    return _Converter(form, known_dictionaries or []).convert()
