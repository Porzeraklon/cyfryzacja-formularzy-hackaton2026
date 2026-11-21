"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Parsery wieloformatowe ingestion: PDF (pdfplumber; skany pypdfium2 -> OCR),
DOC (olefile + tabela kawałków Word 97+), DOCX (python-docx), XLSX (openpyxl),
ODS (odfpy), obrazy (Pillow -> OCR). OCR: RapidOCR (modele ONNX wewnątrz
wheela, 100% offline) z fallbackiem pytesseract. ZAKAZ PyMuPDF/fitz (AGPL).
Wszystkie operacje na bytes/BytesIO - zero zapisów na dysk.
"""

from __future__ import annotations

import io
import re
import threading
from typing import Any

from backend.app.core.exceptions import FileValidationError, PipelineNotReadyError
from backend.app.core.security import (
    MIME_DOC,
    MIME_DOCX,
    MIME_JPEG,
    MIME_ODS,
    MIME_PDF,
    MIME_PNG,
    MIME_XLSX,
)
from backend.app.interfaces.pipeline import ParsedDocument, ParsedPage

# scale=2.0 ~ 144 DPI: wystarczające dla OCR przy rozsądnym koszcie RAM.
_PDF_RENDER_SCALE = 2.0
# Puste komórki ODS bywają powtarzane 16384 razy - ograniczamy rozwinięcie.
_MAX_ODS_REPEAT = 64

_OCR_ENGINE: Any = None
_OCR_LOCK = threading.Lock()

_ParseResult = tuple[list[ParsedPage], list[list[list[str]]], bool]


def _get_ocr_engine() -> Any:
    global _OCR_ENGINE
    with _OCR_LOCK:
        if _OCR_ENGINE is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:
                raise PipelineNotReadyError(
                    "Brak RapidOCR. Zainstaluj extras: pip install -e '.[ai]'.",
                    {"reason": str(exc)},
                ) from exc
            _OCR_ENGINE = RapidOCR()
        return _OCR_ENGINE


def _ocr_tesseract(image: Any) -> str:
    import pytesseract

    try:
        languages = pytesseract.get_languages()
        lang = "+".join(code for code in ("pol", "eng") if code in languages) or "eng"
        return pytesseract.image_to_string(image, lang=lang).strip()
    except Exception as exc:
        raise PipelineNotReadyError(
            "OCR niedostępny: brak RapidOCR (extras [ai]) i działającego tesseracta.",
            {"reason": str(exc)},
        ) from exc


def _ocr_image(image: Any) -> str:
    try:
        engine = _get_ocr_engine()
    except PipelineNotReadyError:
        return _ocr_tesseract(image)
    import numpy as np

    # RapidOCR oczekuje układu BGR (jak cv2.imread).
    array = np.asarray(image.convert("RGB"))[:, :, ::-1]
    with _OCR_LOCK:
        result, _ = engine(array)
    if not result:
        return ""
    return "\n".join(item[1] for item in result)


def _doc_piece_text(word: bytes, table: bytes) -> str | None:
    """Tekst z tabeli kawałków (PlcPcd) formatu Word 97+; None = nieobsługiwany układ."""
    if len(word) < 0x01AA:
        return None
    n_fib = int.from_bytes(word[2:4], "little")
    if n_fib < 193:
        return None
    fc_clx = int.from_bytes(word[0x01A2:0x01A6], "little")
    lcb_clx = int.from_bytes(word[0x01A6:0x01AA], "little")
    if lcb_clx == 0 or fc_clx + lcb_clx > len(table):
        return None
    clx = table[fc_clx : fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:
        if pos + 3 > len(clx):
            return None
        cb = int.from_bytes(clx[pos + 1 : pos + 3], "little")
        pos += 3 + cb
    if pos + 5 > len(clx) or clx[pos] != 0x02:
        return None
    lcb_plc = int.from_bytes(clx[pos + 1 : pos + 5], "little")
    plc = clx[pos + 5 : pos + 5 + lcb_plc]
    n = (lcb_plc - 4) // 12
    if n <= 0 or len(plc) < (n + 1) * 4 + n * 8:
        return None
    cps = [int.from_bytes(plc[i * 4 : i * 4 + 4], "little") for i in range(n + 1)]
    parts: list[str] = []
    for i in range(n):
        pcd = plc[(n + 1) * 4 + i * 8 : (n + 1) * 4 + (i + 1) * 8]
        fc_raw = int.from_bytes(pcd[2:6], "little")
        cch = cps[i + 1] - cps[i]
        if cch <= 0:
            continue
        if fc_raw & 0x40000000:
            # Kawałek skompresowany = 1 bajt na znak w stronicy kodowej ANSI
            # dokumentu; dla polskich formularzy cp1250.
            fc = (fc_raw & 0x3FFFFFFF) // 2
            parts.append(word[fc : fc + cch].decode("cp1250", errors="replace"))
        else:
            parts.append(word[fc_raw : fc_raw + cch * 2].decode("utf-16-le", errors="replace"))
    return "".join(parts)


def _doc_fallback_text(word: bytes) -> str:
    decoded = word.decode("cp1250", errors="ignore")
    runs = re.findall(r"[^\x00-\x08\x0b\x0c\x0e-\x1f]{4,}", decoded)
    return "\n".join(run.strip() for run in runs if any(ch.isalpha() for ch in run))


def _normalize_doc_text(text: str) -> str:
    text = text.replace("\x07", "\t").replace("\r", "\n").replace("\x0b", "\n")
    return re.sub(r"[\x00-\x08\x0e-\x1f]", "", text).strip()


def _ods_repeat(element: Any, attribute: str) -> int:
    try:
        return max(1, int(element.getAttribute(attribute) or 1))
    except (TypeError, ValueError):
        return 1


_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_W_P = f"{_W_NS}p"
_W_T = f"{_W_NS}t"
_W_TAB = f"{_W_NS}tab"
_W_BR = f"{_W_NS}br"
_W_CR = f"{_W_NS}cr"
_TEXT_TAGS = frozenset({_W_T, _W_TAB, _W_BR, _W_CR})


def _under_fallback(node: Any) -> bool:
    parent = node.getparent()
    while parent is not None:
        if parent.tag == _MC_FALLBACK:
            return True
        parent = parent.getparent()
    return False


def _nearest_paragraph(node: Any) -> Any:
    parent = node.getparent()
    while parent is not None and parent.tag != _W_P:
        parent = parent.getparent()
    return parent


def _docx_text(element: Any) -> str:
    """Tekst akapitu/komórki wraz z zawartością text-boxów (w:txbxContent).

    Formularze COI bywają złożone wyłącznie z text-boxów (w:drawing/mc:AlternateContent),
    których python-docx Paragraph.text nie sięga. Pomijamy mc:Fallback — duplikuje tekst
    z mc:Choice. Każdy w:p (także zagnieżdżony w text-boxie) staje się osobną linią.
    """
    lines: list[str] = []
    for paragraph in element.iter(_W_P):
        if _under_fallback(paragraph):
            continue
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag not in _TEXT_TAGS:
                continue
            if _under_fallback(node) or _nearest_paragraph(node) is not paragraph:
                continue
            if node.tag == _W_T:
                parts.append(node.text or "")
            elif node.tag == _W_TAB:
                parts.append("\t")
            else:
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


class MultiFormatParser:
    """Implementacja protokołu DocumentParser."""

    def parse(self, *, filename: str, content: bytes, mime_type: str) -> ParsedDocument:
        handlers = {
            MIME_PDF: self._parse_pdf,
            MIME_DOC: self._parse_doc,
            MIME_DOCX: self._parse_docx,
            MIME_XLSX: self._parse_xlsx,
            MIME_ODS: self._parse_ods,
            MIME_PNG: self._parse_image,
            MIME_JPEG: self._parse_image,
        }
        handler = handlers.get(mime_type)
        if handler is None:
            raise FileValidationError(
                "MIME_UNSUPPORTED",
                f"Nieobsługiwany typ MIME: {mime_type}.",
                {"filename": filename, "mime_type": mime_type},
            )
        pages, tables, used_ocr = handler(content)
        return ParsedDocument(
            source_filename=filename,
            source_format=mime_type,
            pages=pages,
            tables=tables,
            used_ocr=used_ocr,
        )

    def _parse_pdf(self, content: bytes) -> _ParseResult:
        import pdfplumber

        pages: list[ParsedPage] = []
        tables: list[list[list[str]]] = []
        try:
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                for number, page in enumerate(pdf.pages, start=1):
                    text = (page.extract_text() or "").strip()
                    for table in page.extract_tables():
                        tables.append([["" if cell is None else str(cell) for cell in row] for row in table])
                    pages.append(ParsedPage(page_number=number, text=text))
        except Exception as exc:
            raise FileValidationError(
                "FILE_PARSE_ERROR",
                "Nie udało się odczytać pliku PDF (uszkodzony lub zaszyfrowany).",
                {"reason": str(exc)},
            ) from exc

        used_ocr = False
        empty_indexes = [index for index, page in enumerate(pages) if not page.text]
        if empty_indexes:
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument(content)
            try:
                for index in empty_indexes:
                    bitmap = document[index].render(scale=_PDF_RENDER_SCALE)
                    pages[index].text = _ocr_image(bitmap.to_pil())
            finally:
                document.close()
            used_ocr = True
        return pages, tables, used_ocr

    def _parse_doc(self, content: bytes) -> _ParseResult:
        import olefile

        try:
            with olefile.OleFileIO(io.BytesIO(content)) as ole:
                if not ole.exists("WordDocument"):
                    raise ValueError("brak strumienia WordDocument")
                word = ole.openstream("WordDocument").read()
                flags = int.from_bytes(word[0x000A:0x000C], "little")
                table_name = "1Table" if flags & 0x0200 else "0Table"
                table = ole.openstream(table_name).read() if ole.exists(table_name) else b""
        except Exception as exc:
            raise FileValidationError(
                "FILE_PARSE_ERROR",
                "Nie udało się odczytać pliku DOC (kontener OLE).",
                {"reason": str(exc)},
            ) from exc

        text = _doc_piece_text(word, table)
        if text is None:
            text = _doc_fallback_text(word)
        return [ParsedPage(page_number=1, text=_normalize_doc_text(text))], [], False

    def _parse_docx(self, content: bytes) -> _ParseResult:
        from docx import Document
        from docx.table import Table

        try:
            document = Document(io.BytesIO(content))
            lines: list[str] = []
            tables: list[list[list[str]]] = []
            for child in document.element.body.iterchildren():
                if child.tag.endswith("}p"):
                    lines.append(_docx_text(child))
                elif child.tag.endswith("}tbl"):
                    rows = [
                        [_docx_text(cell._tc) for cell in row.cells] for row in Table(child, document).rows
                    ]
                    tables.append(rows)
                    lines.extend(" | ".join(row) for row in rows)
        except Exception as exc:
            raise FileValidationError(
                "FILE_PARSE_ERROR",
                "Nie udało się odczytać pliku DOCX.",
                {"reason": str(exc)},
            ) from exc
        return [ParsedPage(page_number=1, text="\n".join(lines).strip())], tables, False

    def _parse_xlsx(self, content: bytes) -> _ParseResult:
        from openpyxl import load_workbook

        pages: list[ParsedPage] = []
        tables: list[list[list[str]]] = []
        try:
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            try:
                for number, sheet in enumerate(workbook.worksheets, start=1):
                    rows: list[list[str]] = []
                    for row in sheet.iter_rows(values_only=True):
                        cells = ["" if value is None else str(value) for value in row]
                        if any(cell.strip() for cell in cells):
                            rows.append(cells)
                    if rows:
                        tables.append(rows)
                    text = "\n".join([sheet.title] + [" | ".join(row) for row in rows])
                    pages.append(ParsedPage(page_number=number, text=text))
            finally:
                workbook.close()
        except Exception as exc:
            raise FileValidationError(
                "FILE_PARSE_ERROR",
                "Nie udało się odczytać pliku XLSX.",
                {"reason": str(exc)},
            ) from exc
        return pages, tables, False

    def _parse_ods(self, content: bytes) -> _ParseResult:
        from odf import teletype
        from odf.opendocument import load
        from odf.table import Table, TableCell, TableRow

        pages: list[ParsedPage] = []
        tables: list[list[list[str]]] = []
        try:
            document = load(io.BytesIO(content))
            for number, table in enumerate(document.spreadsheet.getElementsByType(Table), start=1):
                rows: list[list[str]] = []
                for row in table.getElementsByType(TableRow):
                    cells: list[str] = []
                    for cell in row.getElementsByType(TableCell):
                        text = teletype.extractText(cell).strip()
                        if text:
                            repeat = min(_ods_repeat(cell, "numbercolumnsrepeated"), _MAX_ODS_REPEAT)
                            cells.extend([text] * repeat)
                        else:
                            cells.append("")
                    if not any(cell.strip() for cell in cells):
                        continue
                    rows.extend([cells] * min(_ods_repeat(row, "numberrowsrepeated"), _MAX_ODS_REPEAT))
                if rows:
                    tables.append(rows)
                name = str(table.getAttribute("name") or f"Arkusz {number}")
                text = "\n".join([name] + [" | ".join(row) for row in rows])
                pages.append(ParsedPage(page_number=number, text=text))
        except Exception as exc:
            raise FileValidationError(
                "FILE_PARSE_ERROR",
                "Nie udało się odczytać pliku ODS.",
                {"reason": str(exc)},
            ) from exc
        return pages, tables, False

    def _parse_image(self, content: bytes) -> _ParseResult:
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(content))
            image.load()
        except Exception as exc:
            raise FileValidationError(
                "FILE_PARSE_ERROR",
                "Nie udało się odczytać pliku obrazu.",
                {"reason": str(exc)},
            ) from exc
        return [ParsedPage(page_number=1, text=_ocr_image(image))], [], True
