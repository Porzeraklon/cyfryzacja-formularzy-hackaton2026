"""Wykrywanie miejsc do wpisania na skanach (kreski, wykropkowania, ramki, kratki) bez OpenCV."""

from PIL import Image, ImageDraw

from backend.app.modules.ingestion.fill_marks import annotate_ocr, detect_fill_marks

W, H = 1200, 900


def _page() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("L", (W, H), 255)
    return image, ImageDraw.Draw(image)


def test_detects_dotted_solid_frame_and_checkbox():
    image, draw = _page()
    for x in range(300, 900, 8):  # wykropkowanie
        draw.rectangle([x, 100, x + 2, 102], fill=0)
    draw.line([300, 250, 900, 250], fill=0, width=2)  # podkreślenie
    draw.rectangle([300, 380, 900, 430], outline=0, width=2)  # ramka pola
    draw.rectangle([300, 600, 330, 630], outline=0, width=2)  # kratka wyboru
    marks = detect_fill_marks(image)
    lines = [m for m in marks if m.kind == "line"]
    boxes = [m for m in marks if m.kind == "box"]
    assert len(lines) == 3, marks
    assert len(boxes) == 1 and 295 <= boxes[0].x0 <= 305


def test_text_is_not_a_fill_line():
    image, draw = _page()
    for row in range(5):  # „wiersze tekstu” - gęste bloki znaków nad linią bazową
        y = 100 + row * 60
        for x in range(100, 1000, 14):
            draw.rectangle([x, y, x + 9, y + 20], fill=0)
    assert detect_fill_marks(image) == []


def test_annotate_ocr_puts_markers_in_label_rows():
    image, draw = _page()
    draw.line([400, 120, 900, 120], fill=0, width=2)
    draw.rectangle([100, 300, 130, 330], outline=0, width=2)
    items = [
        [[[100, 100], [380, 100], [380, 125], [100, 125]], "Imię i nazwisko:", 0.99],
        [[[150, 305], [400, 305], [400, 328], [150, 328]], "Gotówką w kasie", 0.98],
    ]
    text = annotate_ocr(items, detect_fill_marks(image))
    assert text.splitlines() == ["Imię i nazwisko: ____", "[ ] Gotówką w kasie"]
