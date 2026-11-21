"""Bezpieczeństwo: walidacja plików (FR-05..FR-08, RAM-only) oraz JWT (FR-09).

Pliki są przetwarzane wyłącznie w pamięci (bytes/BytesIO). Żadnych zapisów na dysk.
MIME rozpoznawany po magic bytes rzeczywistej zawartości, nie po rozszerzeniu.
"""

import io
import re
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from backend.app.core.config import ALLOWED_EXTENSIONS, Settings, get_settings
from backend.app.core.exceptions import AuthenticationError, FileValidationError

MAX_FILENAME_LENGTH = 250

# Znaki specjalne ścieżek/powłoki ze specyfikacji COI + separator katalogów Windows.
FORBIDDEN_FILENAME_CHARS = frozenset("$:></\\|'*%?\"")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

MIME_PDF = "application/pdf"
MIME_DOC = "application/msword"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_ODS = "application/vnd.oasis.opendocument.spreadsheet"
MIME_PNG = "image/png"
MIME_JPEG = "image/jpeg"

EXTENSION_TO_MIMES: dict[str, frozenset[str]] = {
    ".pdf": frozenset({MIME_PDF}),
    ".doc": frozenset({MIME_DOC}),
    ".docx": frozenset({MIME_DOCX}),
    ".xlsx": frozenset({MIME_XLSX}),
    ".ods": frozenset({MIME_ODS}),
    ".png": frozenset({MIME_PNG}),
    ".jpg": frozenset({MIME_JPEG}),
    ".jpeg": frozenset({MIME_JPEG}),
}

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_READ_CHUNK_SIZE = 1024 * 1024


class ValidatedFile(BaseModel):
    filename: str
    mime_type: str
    size_bytes: int
    content: bytes


def validate_filename(filename: str) -> str:
    """FR-05/FR-06: długość <= 250, zakazane znaki, '..', znaki kontrolne, rozszerzenie."""
    if not filename or not filename.strip():
        raise FileValidationError("FILE_NAME_EMPTY", "Nazwa pliku nie może być pusta.")
    if len(filename) > MAX_FILENAME_LENGTH:
        raise FileValidationError(
            "FILE_NAME_TOO_LONG",
            f"Nazwa pliku przekracza {MAX_FILENAME_LENGTH} znaków (ma {len(filename)}).",
            {"length": len(filename), "max_length": MAX_FILENAME_LENGTH},
        )
    if ".." in filename:
        raise FileValidationError("FILE_NAME_FORBIDDEN", "Nazwa pliku zawiera niedozwolony ciąg '..'.")
    forbidden = sorted({ch for ch in filename if ch in FORBIDDEN_FILENAME_CHARS})
    if forbidden:
        raise FileValidationError(
            "FILE_NAME_FORBIDDEN",
            "Nazwa pliku zawiera niedozwolone znaki specjalne.",
            {"forbidden_chars": forbidden},
        )
    if _CONTROL_CHARS_RE.search(filename):
        raise FileValidationError("FILE_NAME_FORBIDDEN", "Nazwa pliku zawiera znaki kontrolne.")
    extension = _extension_of(filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise FileValidationError(
            "FILE_EXTENSION_NOT_ALLOWED",
            f"Niedozwolone rozszerzenie '{extension}'. Dozwolone: {sorted(ALLOWED_EXTENSIONS)}.",
            {"extension": extension},
        )
    return filename


def _extension_of(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


def sniff_mime_type(content: bytes) -> str | None:
    """Rozpoznaje MIME po magic bytes; dla kontenerów ZIP/OLE bada strukturę."""
    if content.startswith(b"%PDF-"):
        return MIME_PDF
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return MIME_PNG
    if content.startswith(b"\xff\xd8\xff"):
        return MIME_JPEG
    if content.startswith(_OLE_MAGIC):
        return MIME_DOC
    if content.startswith(_ZIP_MAGICS):
        return _sniff_zip_container(content)
    return None


def _sniff_zip_container(content: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            if "word/document.xml" in names:
                return MIME_DOCX
            if "xl/workbook.xml" in names:
                return MIME_XLSX
            if "mimetype" in names:
                declared = archive.read("mimetype").decode("ascii", errors="ignore").strip()
                if declared == MIME_ODS:
                    return MIME_ODS
    except (zipfile.BadZipFile, OSError, KeyError):
        return None
    return None


def validate_file_content(filename: str, content: bytes, max_size_bytes: int) -> ValidatedFile:
    """FR-07/FR-08: limit rozmiaru + zgodność MIME z rozszerzeniem. Wszystko w RAM."""
    size = len(content)
    if size == 0:
        raise FileValidationError("FILE_EMPTY", "Plik jest pusty.", {"filename": filename})
    if size > max_size_bytes:
        raise FileValidationError(
            "FILE_TOO_LARGE",
            f"Plik przekracza limit {max_size_bytes} bajtów (ma {size}).",
            {"filename": filename, "size_bytes": size, "max_size_bytes": max_size_bytes},
        )
    extension = _extension_of(filename)
    detected = sniff_mime_type(content)
    if detected is None:
        raise FileValidationError(
            "MIME_UNRECOGNIZED",
            "Nie rozpoznano formatu pliku po magic bytes.",
            {"filename": filename},
        )
    expected = EXTENSION_TO_MIMES.get(extension, frozenset())
    if detected not in expected:
        raise FileValidationError(
            "MIME_MISMATCH",
            f"Zawartość pliku ({detected}) nie zgadza się z rozszerzeniem '{extension}'.",
            {"filename": filename, "detected_mime": detected, "expected_mimes": sorted(expected)},
        )
    return ValidatedFile(filename=filename, mime_type=detected, size_bytes=size, content=content)


async def read_upload_limited(upload: Any, max_size_bytes: int) -> bytes:
    """Czyta UploadFile strumieniowo do RAM, przerywa po przekroczeniu limitu."""
    buffer = io.BytesIO()
    total = 0
    while chunk := await upload.read(_READ_CHUNK_SIZE):
        total += len(chunk)
        if total > max_size_bytes:
            raise FileValidationError(
                "FILE_TOO_LARGE",
                f"Plik przekracza limit {max_size_bytes} bajtów.",
                {"filename": upload.filename, "max_size_bytes": max_size_bytes},
            )
        buffer.write(chunk)
    return buffer.getvalue()


def validate_upload_bytes(filename: str | None, content: bytes, settings: Settings) -> ValidatedFile:
    name = validate_filename(filename or "")
    return validate_file_content(name, content, settings.MAX_FILE_SIZE_BYTES)


# --- JWT (FR-09) -------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(subject: str, settings: Settings, extra_claims: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)).timestamp()),
        **(extra_claims or {}),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token JWT wygasł.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Nieprawidłowy token JWT.") from exc


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Dependency chroniąca endpointy. Przy JWT_AUTH_ENABLED=false przepuszcza anonimowo."""
    if not settings.JWT_AUTH_ENABLED:
        return {"sub": "anonymous", "auth_disabled": True}
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Wymagany token JWT w nagłówku Authorization: Bearer <token>.")
    claims = decode_access_token(credentials.credentials, settings)
    request.state.jwt_claims = claims
    return claims
