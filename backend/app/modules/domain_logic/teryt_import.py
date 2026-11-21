"""Generator słowników COI (Województwo, Powiat, Gmina, Miejscowość) z rejestru TERYT GUS.

Narzędzie deweloperskie uruchamiane RĘCZNIE, poza runtime API — nie wykonuje żadnych
połączeń sieciowych. Pliki CSV pobiera się samodzielnie z https://eteryt.stat.gov.pl
(katalogi TERC i SIMC, wersja „urzędowa”, format CSV: separator ``;``, UTF-8 z BOM)::

    python -m backend.app.modules.domain_logic.teryt_import \\
        --terc TERC_Urzedowy_2026-01-01.csv --simc SIMC_Urzedowy_2026-01-01.csv \\
        --out data/dictionaries

Wynik to pliki ``teryt_districts.json``, ``teryt_communities.json`` i ``teryt_places.json``
w formacie lokalnej bazy słowników (``{"dictionaries": [{dictionary_id, name, description,
items: [{code, label}]}]}``), które nadpisują dołączone podzbiory demonstracyjne.
Słownik województw (``dict_voivodeships``) jest już w ``sample_dictionaries.json``; flaga
``--voivodeships`` generuje go dodatkowo do ``teryt_voivodeships.json`` — wtedy trzeba
usunąć wpis z ``sample_dictionaries.json`` (DictionaryService odrzuca zdublowane id).

Kody: województwo = WOJ (2 cyfry), powiat = WOJ+POW (4 cyfry, WWPP), gmina = kod TERC
WOJ+POW+GMI+RODZ (7 cyfr, WWPPGGR), miejscowość = identyfikator SIMC ``SYM`` (7 cyfr).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TERC_COLUMNS = ("WOJ", "POW", "GMI", "RODZ", "NAZWA", "NAZWA_DOD", "STAN_NA")
SIMC_COLUMNS = ("WOJ", "POW", "GMI", "RODZ_GMI", "RM", "MZ", "NAZWA", "SYM", "SYMPOD", "STAN_NA")

# Rodzaje jednostek TERC będące gminami (pozostałe: 4/5 — część miejska/wiejska gminy
# miejsko-wiejskiej, 8 — dzielnica m.st. Warszawy, 9 — delegatura miasta).
COMMUNITY_KINDS = {"1": "gmina miejska", "2": "gmina wiejska", "3": "gmina miejsko-wiejska"}
CITY_DISTRICT_MIN = 61  # TERYT: powiaty o numerach 61+ to miasta na prawach powiatu

DISTRICTS_FILENAME = "teryt_districts.json"
COMMUNITIES_FILENAME = "teryt_communities.json"
PLACES_FILENAME = "teryt_places.json"
VOIVODESHIPS_FILENAME = "teryt_voivodeships.json"

FULL_SOURCE_NOTE = "Wygenerowano z rejestru TERYT GUS"

# Polska kolacja: kolejność liter alfabetu polskiego; litery spoza alfabetu (é, ç, ü…)
# sprowadzane do litery bazowej.
_POLISH_ALPHABET = "aąbcćdeęfghijklłmnńoópqrsśtuvwxyzźż"
_POLISH_RANK = {letter: index for index, letter in enumerate(_POLISH_ALPHABET)}
_FOREIGN_LETTERS = str.maketrans({
    "á": "a", "à": "a", "â": "a", "ä": "a", "ã": "a", "å": "a", "ç": "c", "č": "c", "é": "e",
    "è": "e", "ê": "e", "ë": "e", "í": "i", "ì": "i", "î": "i", "ï": "i", "ñ": "n", "ô": "o",
    "ö": "o", "õ": "o", "ò": "o", "ř": "r", "š": "s", "ú": "u", "ù": "u", "û": "u", "ü": "u",
    "ý": "y", "ž": "z",
})


class TerytFormatError(ValueError):
    """Plik CSV nie ma oczekiwanego formatu GUS (brak kolumn, błędne kody)."""


def polish_sort_key(text: str) -> tuple[tuple[int, ...], str]:
    """Klucz sortowania wg polskiej kolacji (a < ą < b … ł < m … ź < ż), bez rozróżniania wielkości liter.

    Znaki niebędące literami (spacja, łącznik, przecinek) sortują się przed każdą literą,
    dzięki czemu „Korea Południowa” < „Koreańczycy”; cyfry po separatorach, przed literami.
    """
    ranks: list[int] = []
    for char in text.lower().translate(_FOREIGN_LETTERS):
        if char in _POLISH_RANK:
            ranks.append(100 + _POLISH_RANK[char])
        elif char.isdigit():
            ranks.append(10 + int(char))
        else:
            ranks.append(1)
    return tuple(ranks), text


@dataclass(frozen=True)
class TercUnit:
    """Jeden wiersz katalogu TERC (województwo, powiat lub gmina)."""

    woj: str
    pow: str
    gmi: str
    rodz: str
    name: str
    kind: str

    @property
    def code(self) -> str:
        return f"{self.woj}{self.pow}{self.gmi}{self.rodz}"


@dataclass
class TercRegistry:
    """Wynik parse_terc: jednostki indeksowane kodem słownikowym."""

    voivodeships: dict[str, TercUnit] = field(default_factory=dict)
    districts: dict[str, TercUnit] = field(default_factory=dict)
    communities: dict[str, TercUnit] = field(default_factory=dict)
    state_date: str | None = None

    def district_name(self, district_code: str) -> str:
        unit = self.districts.get(district_code)
        return unit.name if unit else district_code


@dataclass(frozen=True)
class SimcPlace:
    """Miejscowość samodzielna z katalogu SIMC z rozwiązaną gminą i powiatem."""

    sym: str
    name: str
    community_code: str
    community_name: str
    district_name: str
    voivodeship_name: str


def _read_rows(text: str, required: Sequence[str], source: str) -> Iterable[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";")
    header = [name.strip() for name in (reader.fieldnames or [])]
    missing = [column for column in required if column not in header]
    if missing:
        raise TerytFormatError(f"{source}: brak kolumn {', '.join(missing)} (nagłówek: {';'.join(header)}).")
    reader.fieldnames = header
    for row in reader:
        yield {key: (value or "").strip() for key, value in row.items() if key is not None}


def _digits(value: str, width: int, column: str, source: str) -> str:
    if not value.isdigit() or len(value) > width:
        raise TerytFormatError(f"{source}: nieprawidłowa wartość kolumny {column}: {value!r}.")
    return value.zfill(width)


def parse_terc(text: str) -> TercRegistry:
    """Parsuje treść pliku TERC (CSV GUS) do rejestru województw, powiatów i gmin."""
    registry = TercRegistry()
    for row in _read_rows(text, TERC_COLUMNS, "TERC"):
        if not row["WOJ"]:
            continue
        woj = _digits(row["WOJ"], 2, "WOJ", "TERC")
        registry.state_date = registry.state_date or row["STAN_NA"] or None
        if not row["POW"]:
            registry.voivodeships[woj] = TercUnit(woj, "", "", "", row["NAZWA"].lower(), row["NAZWA_DOD"])
            continue
        pow_ = _digits(row["POW"], 2, "POW", "TERC")
        if not row["GMI"]:
            registry.districts[woj + pow_] = TercUnit(woj, pow_, "", "", row["NAZWA"], row["NAZWA_DOD"])
            continue
        gmi = _digits(row["GMI"], 2, "GMI", "TERC")
        rodz = _digits(row["RODZ"], 1, "RODZ", "TERC")
        unit = TercUnit(woj, pow_, gmi, rodz, row["NAZWA"], row["NAZWA_DOD"])
        if rodz in COMMUNITY_KINDS:
            registry.communities[unit.code] = unit
    return registry


def _resolve_community_code(woj: str, pow_: str, gmi: str, rodz: str) -> str:
    """Kod gminy dla miejscowości: części gminy miejsko-wiejskiej → gmina (RODZ 3),
    dzielnice/delegatury (RODZ 8/9) → gmina miejska o numerze 01."""
    if rodz in ("4", "5"):
        return f"{woj}{pow_}{gmi}3"
    if rodz in ("8", "9"):
        return f"{woj}{pow_}011"
    return f"{woj}{pow_}{gmi}{rodz}"


def parse_simc(text: str, terc: TercRegistry) -> list[SimcPlace]:
    """Parsuje treść pliku SIMC (CSV GUS) do listy miejscowości samodzielnych.

    Pomija części miejscowości i dzielnice (SYM ≠ SYMPOD). Gmina, powiat i województwo
    są rozwiązywane w rejestrze TERC; brak gminy w TERC (różne daty stanu plików) nie
    przerywa importu — etykieta zawiera wtedy kod TERC zamiast nazwy.
    """
    places: list[SimcPlace] = []
    for row in _read_rows(text, SIMC_COLUMNS, "SIMC"):
        if not row["SYM"]:
            continue
        sym = _digits(row["SYM"], 7, "SYM", "SIMC")
        sympod = _digits(row["SYMPOD"], 7, "SYMPOD", "SIMC") if row["SYMPOD"] else sym
        if sym != sympod:
            continue
        woj = _digits(row["WOJ"], 2, "WOJ", "SIMC")
        pow_ = _digits(row["POW"], 2, "POW", "SIMC")
        gmi = _digits(row["GMI"], 2, "GMI", "SIMC")
        rodz = _digits(row["RODZ_GMI"], 1, "RODZ_GMI", "SIMC")
        community_code = _resolve_community_code(woj, pow_, gmi, rodz)
        community = terc.communities.get(community_code)
        if community is None:
            logger.warning("SIMC %s (%s): brak gminy %s w TERC.", sym, row["NAZWA"], community_code)
        voivodeship = terc.voivodeships.get(woj)
        places.append(
            SimcPlace(
                sym=sym,
                name=row["NAZWA"],
                community_code=community_code,
                community_name=community.name if community else community_code,
                district_name=terc.district_name(woj + pow_),
                voivodeship_name=voivodeship.name if voivodeship else woj,
            )
        )
    return places


def district_label(name: str, district_number: int) -> str:
    """Etykieta powiatu: „powiat krakowski” albo „Kraków (miasto na prawach powiatu)”."""
    if district_number >= CITY_DISTRICT_MIN:
        return f"{name} (miasto na prawach powiatu)"
    return f"powiat {name}"


def community_label(name: str, rodz: str, district_name: str) -> str:
    """Etykieta gminy: „Bolesławiec (gmina wiejska, pow. bolesławiecki)”."""
    return f"{name} ({COMMUNITY_KINDS.get(rodz, 'gmina')}, pow. {district_name})"


def place_label(name: str, community_name: str, district_name: str) -> str:
    """Etykieta miejscowości: „Kraków (gm. Kraków, pow. Kraków)”."""
    return f"{name} (gm. {community_name}, pow. {district_name})"


def _entry(dictionary_id: str, name: str, description: str, items: list[dict[str, str]]) -> dict[str, Any]:
    return {"dictionary_id": dictionary_id, "name": name, "description": description, "items": items}


def _source_note(terc: TercRegistry) -> str:
    return f"{FULL_SOURCE_NOTE} (stan na {terc.state_date})." if terc.state_date else f"{FULL_SOURCE_NOTE}."


def build_voivodeships(terc: TercRegistry) -> dict[str, Any]:
    items = [{"code": code, "label": unit.name} for code, unit in sorted(terc.voivodeships.items())]
    return _entry(
        "dict_voivodeships", "Województwo",
        f"Słownik województw RP, kody TERYT (2 cyfry). {_source_note(terc)}", items,
    )


def build_districts(terc: TercRegistry) -> dict[str, Any]:
    items = [
        {"code": code, "label": district_label(unit.name, int(unit.pow))}
        for code, unit in sorted(terc.districts.items())
    ]
    # Ta sama nazwa powiatu w kilku województwach (np. powiat średzki) -> dopisek województwa.
    counts = Counter(item["label"] for item in items)
    for item in items:
        if counts[item["label"]] > 1:
            voivodeship = terc.voivodeships.get(item["code"][:2])
            item["label"] = f"{item['label']} (woj. {voivodeship.name if voivodeship else item['code'][:2]})"
    return _entry(
        "dict_districts", "Powiat",
        f"Słownik powiatów RP, kody TERYT WWPP (4 cyfry). {_source_note(terc)}", items,
    )


def build_communities(terc: TercRegistry) -> dict[str, Any]:
    items = [
        {"code": code, "label": community_label(unit.name, unit.rodz, terc.district_name(code[:4]))}
        for code, unit in sorted(terc.communities.items())
    ]
    return _entry(
        "dict_communities", "Gmina",
        f"Słownik gmin RP, kody TERC WWPPGGR (7 cyfr). {_source_note(terc)}", items,
    )


def build_places(places: Sequence[SimcPlace], terc: TercRegistry) -> dict[str, Any]:
    """Słownik miejscowości; zdublowane etykiety ujednoznacznia województwem, a w ostateczności kodem SIMC."""
    labels = [place_label(p.name, p.community_name, p.district_name) for p in places]
    counts = Counter(labels)
    labels = [
        f"{label} – woj. {place.voivodeship_name}" if counts[label] > 1 else label
        for label, place in zip(labels, places, strict=True)
    ]
    counts = Counter(labels)
    items = [
        {"code": place.sym, "label": f"{label} [{place.sym}]" if counts[label] > 1 else label}
        for label, place in zip(labels, places, strict=True)
    ]
    items.sort(key=lambda item: polish_sort_key(item["label"]))
    return _entry(
        "dict_places", "Miejscowość",
        f"Słownik miejscowości RP, identyfikatory SIMC (7 cyfr). {_source_note(terc)}", items,
    )


def dump_dictionary_file(entries: Sequence[dict[str, Any]], path: Path) -> None:
    """Zapisuje słowniki w formacie bazy: jedna pozycja na linię (czytelne diffy, mały rozmiar)."""
    lines = ["{", '  "dictionaries": [']
    for entry_index, entry in enumerate(entries):
        lines.append("    {")
        for key in ("dictionary_id", "name", "description"):
            lines.append(f"      {json.dumps(key)}: {json.dumps(entry.get(key), ensure_ascii=False)},")
        lines.append('      "items": [')
        items = entry["items"]
        for item_index, item in enumerate(items):
            separator = "," if item_index < len(items) - 1 else ""
            lines.append("        " + json.dumps(item, ensure_ascii=False) + separator)
        lines.append("      ]")
        lines.append("    }" + ("," if entry_index < len(entries) - 1 else ""))
    lines.extend(["  ]", "}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_csv(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.app.modules.domain_logic.teryt_import",
        description="Generuje słowniki COI (Powiat, Gmina, Miejscowość) z plików CSV TERYT GUS (offline).",
    )
    parser.add_argument("--terc", type=Path, required=True, help="plik CSV katalogu TERC (eteryt.stat.gov.pl)")
    parser.add_argument("--simc", type=Path, help="plik CSV katalogu SIMC; bez niego słownik miejscowości jest pomijany")
    parser.add_argument("--out", type=Path, default=Path("data/dictionaries"), help="katalog docelowy")
    parser.add_argument(
        "--voivodeships", action="store_true",
        help="zapisz też teryt_voivodeships.json (usuń wtedy dict_voivodeships z sample_dictionaries.json)",
    )
    args = parser.parse_args(argv)

    try:
        terc = parse_terc(_read_csv(args.terc))
        places = parse_simc(_read_csv(args.simc), terc) if args.simc else None
    except (OSError, TerytFormatError) as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        return 1
    if not terc.districts or not terc.communities:
        print("Błąd: plik TERC nie zawiera powiatów ani gmin.", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    outputs: list[tuple[str, dict[str, Any]]] = [
        (DISTRICTS_FILENAME, build_districts(terc)),
        (COMMUNITIES_FILENAME, build_communities(terc)),
    ]
    if places is not None:
        outputs.append((PLACES_FILENAME, build_places(places, terc)))
    if args.voivodeships:
        outputs.append((VOIVODESHIPS_FILENAME, build_voivodeships(terc)))
    for filename, entry in outputs:
        dump_dictionary_file([entry], args.out / filename)
        print(f"{args.out / filename}: {entry['dictionary_id']} — {len(entry['items'])} pozycji")
    if args.voivodeships:
        print("Uwaga: usuń dict_voivodeships z sample_dictionaries.json (zdublowane dictionary_id).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
