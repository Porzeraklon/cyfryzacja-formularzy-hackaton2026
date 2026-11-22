"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Deterministyczny odczyt nagłówka formularza (bez LLM): tytuł wniosku i urząd-adresat.

LLM na CPU bywa niepewny w tych dwóch miejscach (tytuł parafrazuje, urzędu nie podaje, bo
prompt każe pomijać elementy standardowe), a oba da się pewnie wyczytać z układu typowego
formularza: tytuł to linia zaczynająca się od „Wniosek/Deklaracja/Zgłoszenie…”, adresat to
linia z organem („Wójt Gminy…”, „Do Urzędu…”, „Starosta…”) zwykle w prawym górnym rogu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TITLE_MAX_LENGTH = 255
_HEAD_LINES = 40  # nagłówek = początek dokumentu

_TITLE_START = re.compile(
    r"^\s*(wniosek|deklaracja|zgłoszenie|zgloszenie|oświadczenie|oswiadczenie|podanie|informacja|"
    r"zawiadomienie|wystąpienie)\b",
    re.IGNORECASE,
)
# Linie, które zaczynają się jak tytuł, ale są polami/pouczeniami.
_NOT_TITLE = re.compile(r"(\.{3,}|_{3,}|:\s*$|\bpodpis\b|\bnumer\b|\bdata\b|\bstr\.)", re.IGNORECASE)
_OFFICE = re.compile(
    r"^\s*(?:do\s+)?(?P<office>(?:wójt|wojt|burmistrz|prezydent(?:a)?\s+miasta|starost[aay]|marszałek|"
    r"marszalek|zarząd|zarzad|urz[ąa]d|dyrektor|kierownik|naczelnik|komendant|rada|"
    r"samorządowe|sad|sąd|powiatowy|gminny|miejski|regionalna izba)\b.*)$",
    re.IGNORECASE,
)
_OFFICE_TAIL = re.compile(r"\s*[\.…_]{3,}.*$")  # wykropkowanie do wpisania nazwy


@dataclass(frozen=True)
class HeaderInfo:
    title: str | None = None
    office: str | None = None


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip(" \t:-–")


def _sentence_case(text: str) -> str:
    """„WNIOSEK O WYDANIE ZAŚWIADCZENIA” -> „Wniosek o wydanie zaświadczenia”."""
    if text.isupper():
        lowered = text.lower()
        return lowered[0].upper() + lowered[1:]
    first, _, rest = text.partition(" ")
    if first.isupper():  # „WNIOSEK o wydanie…” -> „Wniosek o wydanie…”
        first = first.capitalize()
    return f"{first} {rest}".strip()


def _continues_title(title: str, following: str) -> bool:
    """Czy następna linia jest dalszym ciągiem tytułu („WNIOSEK” / „o wydanie zezwolenia…”)."""
    if not following or len(following) > 110 or _NOT_TITLE.search(following) or _OFFICE.match(following):
        return False
    if title.rstrip().endswith((".", "!", "?")):
        return False
    first = following[0]
    keyword_only = len(title.split()) == 1
    upper_continuation = title.isupper() and following.isupper()
    return keyword_only or first.islower() or upper_continuation


def extract_title(lines: list[str]) -> str | None:
    """Tytuł z nagłówka; łączy tytuł łamany na kilka linii (maks. 3)."""
    for index, raw in enumerate(lines[:_HEAD_LINES]):
        line = _clean(raw)
        if not _TITLE_START.match(line) or _NOT_TITLE.search(line):
            continue
        title = line
        for following_raw in lines[index + 1 : index + 4]:
            following = _clean(following_raw)
            if not _continues_title(title, following):
                break
            title = f"{title} {following}"
        if len(title.split()) < 2:  # samo „WNIOSEK” bez przedmiotu nie jest tytułem
            continue
        title = title.replace("*", "").strip()
        return _sentence_case(title)[:TITLE_MAX_LENGTH]
    return None


def _title_case(text: str) -> str:
    """„WÓJT GMINY PŁOSKINIA” -> „Wójt Gminy Płoskinia” (tylko dla tekstu pisanego wersalikami)."""
    if not text.isupper():
        return text[0].upper() + text[1:]
    return " ".join(word.capitalize() if len(word) > 2 else word.lower() for word in text.split())


_COLUMNS = re.compile("	| {3,}")
_PLACE_NAME = re.compile(r"^[A-ZĄĆĘŁŃÓŚŹŻ][\w\-ąćęłńóśźż ]{1,60}$")


def extract_office(lines: list[str]) -> str | None:
    """Adresat wniosku z nagłówka, np. „Wójt Gminy Ulan-Majorat”; None, gdy nie da się ustalić."""
    # Nagłówek bywa dwukolumnowy („Wnioskodawca:<TAB>Adresat:”) - dzielimy linie na kolumny.
    head = [
        _clean(part)
        for raw in lines[:_HEAD_LINES]
        for part in _COLUMNS.split(raw)
        if part.strip()
    ]
    for index, line in enumerate(head):
        match = _OFFICE.match(line)
        if not match or _TITLE_START.match(line):
            continue
        office = _OFFICE_TAIL.sub("", match.group("office")).strip(" ,;:")
        # „Urząd Gminy” / „Ulan-Majorat” - nazwa jednostki w kolejnej linii (bez cyfr = nie adres).
        following = head[index + 1] if index + 1 < len(head) else ""
        if len(office.split()) == 2 and _PLACE_NAME.match(following) and not re.search(r"\d", following):
            office = f"{office} {following}"
        if len(office.split()) < 2 or len(office) > 120:
            continue
        return _title_case(office)
    return None


def extract_header(text: str) -> HeaderInfo:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return HeaderInfo(title=extract_title(lines), office=extract_office(lines))
