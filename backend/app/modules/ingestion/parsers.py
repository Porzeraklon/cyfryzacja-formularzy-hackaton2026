"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Parsery wieloformatowe: PDF (pypdf/pdfplumber), DOC/DOCX (python-docx/OLE),
ODS (odfpy), XLSX (openpyxl), obrazy (Pillow -> OCR w ai_extractor).
ZAKAZ PyMuPDF/fitz (AGPL). Wszystkie operacje na bytes/BytesIO (RAM-only).
"""

from backend.app.interfaces.pipeline import ParsedDocument


class MultiFormatParser:
    """Implementacja protokołu DocumentParser."""

    def parse(self, *, filename: str, content: bytes, mime_type: str) -> ParsedDocument:
        # TODO(branch-1): dispatch po mime_type do parserów per format:
        #   application/pdf                        -> pypdf / pdfplumber (tekst + tabele)
        #   application/msword (.doc)              -> ekstrakcja tekstu z OLE
        #   ...wordprocessingml.document (.docx)   -> python-docx
        #   ...spreadsheetml.sheet (.xlsx)         -> openpyxl (read_only=True)
        #   application/vnd.oasis...spreadsheet    -> odfpy
        #   image/png, image/jpeg                  -> przekazanie do OCR (used_ocr=True)
        # Skany PDF bez warstwy tekstowej -> render pypdfium2 -> OCR.
        raise NotImplementedError("TODO(branch-1): parsery wieloformatowe (ingestion)")
