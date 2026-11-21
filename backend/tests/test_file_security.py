"""FR-05..FR-08: walidacja nazwy, rozmiaru, MIME magic bytes, przetwarzanie w RAM."""

import asyncio
import io
import zipfile

import pytest

from backend.app.core.exceptions import FileValidationError
from backend.app.core.security import (
    MIME_DOC,
    MIME_DOCX,
    MIME_ODS,
    MIME_PDF,
    MIME_PNG,
    MIME_XLSX,
    read_upload_limited,
    sniff_mime_type,
    validate_file_content,
    validate_filename,
)
from backend.tests.conftest import PDF_BYTES, PNG_BYTES


def make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


DOCX_BYTES = make_zip({"word/document.xml": b"<w:document/>", "[Content_Types].xml": b"<Types/>"})
XLSX_BYTES = make_zip({"xl/workbook.xml": b"<workbook/>", "[Content_Types].xml": b"<Types/>"})
ODS_BYTES = make_zip({"mimetype": MIME_ODS.encode("ascii"), "content.xml": b"<office/>"})
DOC_BYTES = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64


class FakeUpload:
    def __init__(self, data: bytes, filename: str = "plik.pdf", chunk_size: int = 1024):
        self._stream = io.BytesIO(data)
        self._chunk_size = chunk_size
        self.filename = filename

    async def read(self, size: int = -1) -> bytes:
        return self._stream.read(min(size, self._chunk_size) if size > 0 else self._chunk_size)


class TestFilenameValidation:
    def test_valid_name(self):
        assert validate_filename("wniosek_o_zaswiadczenie.pdf") == "wniosek_o_zaswiadczenie.pdf"

    def test_name_over_250_chars_rejected(self):
        name = "a" * 247 + ".pdf"  # 251 znaków
        assert len(name) == 251
        with pytest.raises(FileValidationError) as exc:
            validate_filename(name)
        assert exc.value.code == "FILE_NAME_TOO_LONG"

    def test_name_exactly_250_chars_accepted(self):
        name = "a" * 246 + ".pdf"
        assert len(name) == 250
        assert validate_filename(name) == name

    @pytest.mark.parametrize("char", ["$", ":", ">", "<", "/", "\\", "|", "'", "*", "%", "?", '"'])
    def test_forbidden_special_chars_rejected(self, char):
        with pytest.raises(FileValidationError) as exc:
            validate_filename(f"wniosek{char}.pdf")
        assert exc.value.code == "FILE_NAME_FORBIDDEN"

    def test_path_traversal_rejected(self):
        with pytest.raises(FileValidationError) as exc:
            validate_filename("wniosek..pdf")
        assert exc.value.code == "FILE_NAME_FORBIDDEN"

    def test_control_chars_rejected(self):
        with pytest.raises(FileValidationError) as exc:
            validate_filename("wniosek\x01.pdf")
        assert exc.value.code == "FILE_NAME_FORBIDDEN"

    def test_empty_name_rejected(self):
        with pytest.raises(FileValidationError) as exc:
            validate_filename("")
        assert exc.value.code == "FILE_NAME_EMPTY"

    def test_disallowed_extension_rejected(self):
        with pytest.raises(FileValidationError) as exc:
            validate_filename("skrypt.exe")
        assert exc.value.code == "FILE_EXTENSION_NOT_ALLOWED"


class TestMimeMagicBytes:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (PDF_BYTES, MIME_PDF),
            (PNG_BYTES, MIME_PNG),
            (b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image/jpeg"),
            (DOC_BYTES, MIME_DOC),
            (DOCX_BYTES, MIME_DOCX),
            (XLSX_BYTES, MIME_XLSX),
            (ODS_BYTES, MIME_ODS),
        ],
    )
    def test_sniff_by_content(self, content, expected):
        assert sniff_mime_type(content) == expected

    def test_unknown_content_rejected(self):
        assert sniff_mime_type(b"zwykly tekst bez magic bytes") is None

    def test_mime_mismatch_png_named_pdf(self):
        with pytest.raises(FileValidationError) as exc:
            validate_file_content("skan.pdf", PNG_BYTES, 1024 * 1024)
        assert exc.value.code == "MIME_MISMATCH"

    def test_mime_mismatch_pdf_named_docx(self):
        with pytest.raises(FileValidationError) as exc:
            validate_file_content("dokument.docx", PDF_BYTES, 1024 * 1024)
        assert exc.value.code == "MIME_MISMATCH"

    def test_text_named_docx_unrecognized(self):
        with pytest.raises(FileValidationError) as exc:
            validate_file_content("dokument.docx", b"to nie jest docx", 1024 * 1024)
        assert exc.value.code == "MIME_UNRECOGNIZED"

    def test_valid_pdf_passes(self):
        validated = validate_file_content("wniosek.pdf", PDF_BYTES, 1024 * 1024)
        assert validated.mime_type == MIME_PDF
        assert validated.content == PDF_BYTES  # RAM-only: bytes, bez zapisu na dysk


class TestSizeAndRam:
    def test_size_limit_enforced(self):
        with pytest.raises(FileValidationError) as exc:
            validate_file_content("duzy.pdf", PDF_BYTES, 10)
        assert exc.value.code == "FILE_TOO_LARGE"

    def test_empty_file_rejected(self):
        with pytest.raises(FileValidationError) as exc:
            validate_file_content("pusty.pdf", b"", 1024)
        assert exc.value.code == "FILE_EMPTY"

    def test_streamed_read_stops_at_limit(self):
        upload = FakeUpload(PDF_BYTES * 100, chunk_size=64)
        with pytest.raises(FileValidationError) as exc:
            asyncio.run(read_upload_limited(upload, 512))
        assert exc.value.code == "FILE_TOO_LARGE"

    def test_streamed_read_returns_bytes_in_ram(self):
        upload = FakeUpload(PDF_BYTES)
        content = asyncio.run(read_upload_limited(upload, 1024 * 1024))
        assert isinstance(content, bytes)
        assert content == PDF_BYTES
