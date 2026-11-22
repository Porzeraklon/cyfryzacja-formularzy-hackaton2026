"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Wykrywanie miejsc do wpisania na skanach formularzy (przed/obok OCR), bez OpenCV.

OCR zwraca etykiety („Imię i nazwisko:”), ale gubi to, co dla cyfryzacji najważniejsze:
miejsca do wypełnienia - wykropkowania, podkreślenia, linie i kratki wyboru. Ten moduł
znajduje je geometrycznie na obrazie binarnym (numpy + Pillow, licencje permisywne; oficjalne
wheele opencv-python dołączają FFmpeg na LGPL, dlatego go nie używamy), a `annotate_ocr`
dopisuje do tekstu OCR znaczniki „____” (pole tekstowe) i „[ ]” (kratka), dzięki którym LLM
widzi pola także na skanie.

Heurystyki:
- linia do wpisania: poziomy ciąg ciemnych pikseli (ciągły albo kropkowany - przerwy domykane
  dylatacją w osi X) o długości >= 4% szerokości strony, cienki (pas nad i pod nim jest pusty,
  czym różni się od wiersza tekstu);
- kratka: kwadrat 10-45 px z czterech krawędzi (dwa krótkie poziome odcinki tej samej długości
  i dwie pionowe krawędzie łączące ich końce).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

DARK_THRESHOLD = 128
LINE_MIN_FRACTION = 0.04
LINE_MIN_PX = 40
DOT_GAP_PX = 4
LINE_MIN_DENSITY = 0.25
LINE_MAX_THICKNESS = 4
CLEAR_BAND_PX = 8
CLEAR_BAND_MAX_DENSITY = 0.12
# Kratka wyboru: bok 1.2-4% szerokości strony (dziurki liter „o”, „d” są mniejsze).
BOX_MIN_FRACTION = 0.012
BOX_MAX_FRACTION = 0.04
RECT_MIN_PX = 18
RECT_MAX_PX = 160


@dataclass(frozen=True)
class FillMark:
    kind: str  # "line" | "box"
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def y_center(self) -> float:
        return (self.y0 + self.y1) / 2


def _binary(image: Any) -> np.ndarray:
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    return gray < DARK_THRESHOLD


def _runs(row: np.ndarray) -> list[tuple[int, int]]:
    """Ciągi True w wierszu jako (start, koniec wyłącznie)."""
    padded = np.concatenate(([False], row, [False])).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist(), strict=True))


def _dilate_x(mask: np.ndarray, gap: int) -> np.ndarray:
    """Domyka przerwy <= 2*gap w poziomie (kropki/kreski wykropkowania stają się ciągłą linią)."""
    width = mask.shape[1]
    cumulative = np.cumsum(np.pad(mask.astype(np.int32), ((0, 0), (gap + 1, gap)), mode="constant"), axis=1)
    window = cumulative[:, 2 * gap + 1 :] - cumulative[:, : -(2 * gap + 1)]
    return window[:, :width] > 0


def detect_lines(mask: np.ndarray) -> list[FillMark]:
    height, width = mask.shape
    min_len = max(LINE_MIN_PX, int(width * LINE_MIN_FRACTION))
    closed = _dilate_x(mask, DOT_GAP_PX)
    candidates: list[tuple[int, int, int]] = []  # (y, x0, x1)
    for y in range(height):
        for x0, x1 in _runs(closed[y]):
            if x1 - x0 < min_len:
                continue
            if mask[y, x0:x1].mean() < LINE_MIN_DENSITY:
                continue
            candidates.append((y, x0, x1))
    # Sklejamy sąsiednie wiersze tej samej linii (grubość kilku pikseli).
    marks: list[list[int]] = []
    for y, x0, x1 in candidates:
        for mark in marks:
            if y - mark[3] <= 1 and min(x1, mark[2]) - max(x0, mark[0]) > 0.6 * (x1 - x0):
                mark[0], mark[2], mark[3] = min(mark[0], x0), max(mark[2], x1), y
                break
        else:
            marks.append([x0, y, x1, y])
    result = []
    for x0, y0, x1, y1 in marks:
        if y1 - y0 + 1 > LINE_MAX_THICKNESS:
            continue  # gruby blok = tekst, logo lub ramka tabeli wypełniona
        # Linia do wpisania ma pusto nad sobą i pod sobą; dolna krawędź liter (linia bazowa
        # wiersza tekstu) ma nad sobą gęste piksele liter, a przekreślenie - z obu stron.
        above = mask[max(0, y0 - 3 - CLEAR_BAND_PX) : max(0, y0 - 3), x0:x1]
        below = mask[y1 + 3 : y1 + 3 + CLEAR_BAND_PX, x0:x1]
        dense_above = above.size and above.mean() > CLEAR_BAND_MAX_DENSITY
        dense_below = below.size and below.mean() > CLEAR_BAND_MAX_DENSITY
        if dense_above or dense_below:
            continue
        result.append(FillMark("line", int(x0), int(y0), int(x1), int(y1)))
    return _merge_collinear(result)


def _merge_collinear(lines: list[FillMark], gap: int = 30) -> list[FillMark]:
    """Odcinki jednej linii (wykropkowanie rwie się na kilka kawałków) -> jedna linia."""
    merged: list[FillMark] = []
    for line in sorted(lines, key=lambda m: (m.y0, m.x0)):
        for index, other in enumerate(merged):
            same_row = abs(other.y_center - line.y_center) <= 3
            if same_row and line.x0 - other.x1 <= gap and other.x0 - line.x1 <= gap:
                merged[index] = FillMark(
                    "line", min(other.x0, line.x0), min(other.y0, line.y0),
                    max(other.x1, line.x1), max(other.y1, line.y1),
                )
                break
        else:
            merged.append(line)
    return merged


def detect_boxes(mask: np.ndarray) -> list[FillMark]:
    height, width = mask.shape
    box_min, box_max = max(10, int(width * BOX_MIN_FRACTION)), max(20, int(width * BOX_MAX_FRACTION))
    segments: dict[tuple[int, int], list[int]] = {}
    for y in range(height):
        for x0, x1 in _runs(mask[y]):
            if box_min <= x1 - x0 <= box_max:
                segments.setdefault((x0 // 3, x1 // 3), []).append(y)
    boxes: list[FillMark] = []
    for (bx0, bx1), rows in segments.items():
        x0, x1 = bx0 * 3, bx1 * 3
        side = x1 - x0
        rows = sorted(set(rows))
        for top in rows:
            bottom = next((r for r in rows if 0.7 * side <= r - top <= 1.3 * side), None)
            if bottom is None:
                continue
            left = mask[top:bottom, max(0, x0 - 1) : x0 + 3].any(axis=1).mean()
            right = mask[top:bottom, max(0, x1 - 3) : x1 + 1].any(axis=1).mean()
            inside = mask[top + 3 : bottom - 2, x0 + 3 : x1 - 3]
            if left > 0.85 and right > 0.85 and (inside.size == 0 or inside.mean() < 0.3):
                if not any(abs(b.x0 - x0) < 4 and abs(b.y0 - top) < 4 for b in boxes):
                    boxes.append(FillMark("box", x0, top, x1, bottom))
                break
    return boxes


def _merge_rectangles(lines: list[FillMark], mask: np.ndarray) -> list[FillMark]:
    """Ramka pola (górna i dolna krawędź tej samej szerokości połączone bokami) -> jedno pole."""
    used: set[int] = set()
    result: list[FillMark] = []
    ordered = sorted(lines, key=lambda m: (m.y0, m.x0))
    for i, top in enumerate(ordered):
        if i in used:
            continue
        for j in range(i + 1, len(ordered)):
            bottom = ordered[j]
            if j in used or abs(bottom.x0 - top.x0) > 6 or abs(bottom.x1 - top.x1) > 6:
                continue
            gap = bottom.y0 - top.y1
            if not RECT_MIN_PX <= gap <= RECT_MAX_PX:
                continue
            left = mask[top.y1 : bottom.y0, max(0, top.x0 - 2) : top.x0 + 8].any(axis=1).mean()
            right = mask[top.y1 : bottom.y0, max(0, top.x1 - 8) : top.x1 + 2].any(axis=1).mean()
            if left > 0.8 and right > 0.8:
                used.update((i, j))
                result.append(FillMark("line", top.x0, top.y0, top.x1, bottom.y1))
                break
        if i not in used:
            result.append(top)
    return result


def detect_fill_marks(image: Any) -> list[FillMark]:
    """Miejsca do wpisania (linie, ramki pól i kratki) na obrazie strony, w kolejności czytania."""
    mask = _binary(image)
    boxes = detect_boxes(mask)
    # Krawędzie kratek nie są liniami do wpisania; ramka pola daje jedno pole, nie dwie linie.
    lines = _merge_rectangles(
        [
            line for line in detect_lines(mask)
            if not any(b.x0 - 2 <= line.x0 and line.x1 <= b.x1 + 2 and b.y0 - 2 <= line.y0 <= b.y1 + 2 for b in boxes)
        ],
        mask,
    )
    return sorted([*lines, *boxes], key=lambda m: (round(m.y_center / 10), m.x0))


def _bbox(points: Any) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def annotate_ocr(items: list[Any], marks: list[FillMark]) -> str:
    """Tekst OCR (pozycje RapidOCR: [ramka, tekst, pewność]) wzbogacony o znaczniki pól.

    Pozycje i znaczniki grupowane są w wiersze po osi Y i sortowane po X, więc znacznik „____”
    trafia za etykietę w tym samym wierszu, a „[ ]” przed opis opcji. Linie bez etykiety
    (np. kolejne wiersze uzasadnienia) dają osobne „____”.
    """
    tokens: list[tuple[float, float, float, str]] = []  # (y0, y1, x, tekst)
    for item in items:
        x0, y0, _x1, y1 = _bbox(item[0])
        tokens.append((y0, y1, x0, str(item[1])))
    for mark in marks:
        text = "____" if mark.kind == "line" else "[ ]"
        tokens.append((mark.y0 - 6, mark.y1 + 2, mark.x0, text))
    tokens.sort(key=lambda t: ((t[0] + t[1]) / 2, t[2]))
    rows: list[list[tuple[float, float, float, str]]] = []
    for token in tokens:
        center = (token[0] + token[1]) / 2
        if rows:
            last = rows[-1]
            top = min(t[0] for t in last)
            bottom = max(t[1] for t in last)
            if top - 2 <= center <= bottom + 2:
                last.append(token)
                continue
        rows.append([token])
    lines = []
    for row in rows:
        words = [t[3] for t in sorted(row, key=lambda t: t[2])]
        # Kilka znaczników pod rząd w jednym wierszu = jedno pole.
        merged: list[str] = []
        for word in words:
            if not (merged and word == "____" and merged[-1] == "____"):
                merged.append(word)
        lines.append(" ".join(merged))
    return "\n".join(lines)
