"""Regresja parsowania DOCX z text-boxami (w:txbxContent).

Formularze COI (np. 2 i 8) trzymają całą treść w polach tekstowych OOXML,
których python-docx nie czyta przez Paragraph.text. Budujemy minimalny
kontener DOCX w RAM i sprawdzamy, że tekst jest wyciągany dokładnie raz
(mc:Fallback pominięty) oraz że zwykłe akapity i tabele nadal działają.
"""

import io
import zipfile

from backend.app.core.security import MIME_DOCX
from backend.app.modules.ingestion.parsers import MultiFormatParser

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
V = "urn:schemas-microsoft-com:vml"

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def _docx_bytes(body_xml: str) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}" xmlns:mc="{MC}" xmlns:wps="{WPS}" '
        f'xmlns:wp="{WP}" xmlns:a="{A}" xmlns:v="{V}">'
        f"<w:body>{body_xml}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _textbox(text: str) -> str:
    inner = f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
    return (
        "<w:p><w:r><mc:AlternateContent>"
        '<mc:Choice Requires="wps"><w:drawing><wp:inline>'
        "<a:graphic><a:graphicData>"
        f"<wps:wtxbx><w:txbxContent>{inner}</w:txbxContent></wps:wtxbx>"
        "</a:graphicData></a:graphic>"
        "</wp:inline></w:drawing></mc:Choice>"
        f"<mc:Fallback><w:pict><v:textbox><w:txbxContent>{inner}</w:txbxContent>"
        "</v:textbox></w:pict></mc:Fallback>"
        "</mc:AlternateContent></w:r></w:p>"
    )


def test_textbox_extracted_once_and_paragraph_preserved():
    body = _textbox("Tekst w polu tekstowym") + "<w:p><w:r><w:t>Zwykly akapit</w:t></w:r></w:p>"
    parsed = MultiFormatParser().parse(filename="tb.docx", content=_docx_bytes(body), mime_type=MIME_DOCX)
    text = parsed.pages[0].text
    assert text.count("Tekst w polu tekstowym") == 1
    assert "Zwykly akapit" in text


def test_multiple_textboxes_each_extracted_once():
    body = _textbox("Pierwszy") + _textbox("Drugi") + _textbox("Trzeci")
    parsed = MultiFormatParser().parse(filename="tb2.docx", content=_docx_bytes(body), mime_type=MIME_DOCX)
    text = parsed.pages[0].text
    for word in ("Pierwszy", "Drugi", "Trzeci"):
        assert text.count(word) == 1


def test_table_cell_textbox_without_duplicate():
    body = (
        "<w:tbl><w:tr>"
        f"<w:tc>{_textbox('Komorka')}</w:tc>"
        "<w:tc><w:p><w:r><w:t>Naglowek</w:t></w:r></w:p></w:tc>"
        "</w:tr></w:tbl>"
    )
    parsed = MultiFormatParser().parse(filename="tb3.docx", content=_docx_bytes(body), mime_type=MIME_DOCX)
    assert parsed.tables
    flat = " ".join(cell for row in parsed.tables[0] for cell in row)
    assert "Komorka" in flat
    assert "Naglowek" in flat
    assert flat.count("Komorka") == 1
