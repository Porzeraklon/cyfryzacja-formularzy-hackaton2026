"""Prawdziwa walidacja wartości pól — katalog walidatorów COI (Reguły techniczne).

Sumy kontrolne: PESEL, NIP, REGON (9 i 14 cyfr), seria i numer dowodu osobistego.
Formaty: ZIP_CODE, EMAIL, EPUAP, ADE. Komunikaty zgodne z frontendem (formdef.js).
"""

import re
from datetime import date
from typing import Any

from backend.app.schemas.form_schema import CustomValidator, DataType, ValidationRules

_PESEL_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)
_REGON9_WEIGHTS = (8, 9, 2, 3, 4, 5, 6, 7)
_REGON14_WEIGHTS = (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)
_ID_CARD_WEIGHTS = (7, 3, 1, 0, 7, 3, 1, 7, 3)
_ZIP_RE = re.compile(r"\d{2}-\d{3}")
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}")
_EPUAP_RE = re.compile(r"/[^/\s]+/[^/\s]+")
_ADE_RE = re.compile(r"AE:PL-\d{5}-\d{5}-[A-Z]{5}-\d{2}")
_ID_CARD_RE = re.compile(r"[A-Z]{3}\d{6}")


def _checksum(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights, strict=False)) % 10


def validate_pesel(value: str) -> list[str]:
    digits = value.strip()
    if not re.fullmatch(r"\d{11}", digits):
        return ["PESEL musi składać się z 11 cyfr."]
    errors: list[str] = []
    control = (10 - _checksum(digits[:10], _PESEL_WEIGHTS)) % 10
    if control != int(digits[10]):
        errors.append("Nieprawidłowa suma kontrolna PESEL.")
    year, month, day = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
    century = {0: 1900, 1: 2000, 2: 2100, 3: 2200, 4: 1800}.get(month // 20)
    if century is None:
        errors.append("Nieprawidłowy miesiąc w numerze PESEL.")
    else:
        try:
            date(century + year, month % 20, day)
        except ValueError:
            errors.append("Nieprawidłowa data urodzenia w numerze PESEL.")
    return errors


def validate_nip(value: str) -> list[str]:
    digits = re.sub(r"[\s-]", "", value)
    if not re.fullmatch(r"\d{10}", digits):
        return ["NIP musi składać się z 10 cyfr."]
    total = sum(int(d) * w for d, w in zip(digits[:9], _NIP_WEIGHTS, strict=True)) % 11
    if total == 10 or total != int(digits[9]):
        return ["Nieprawidłowa suma kontrolna NIP."]
    return []


def validate_regon(value: str) -> list[str]:
    digits = re.sub(r"[\s-]", "", value)
    if re.fullmatch(r"\d{9}", digits):
        weights = _REGON9_WEIGHTS
    elif re.fullmatch(r"\d{14}", digits):
        weights = _REGON14_WEIGHTS
    else:
        return ["REGON musi składać się z 9 lub 14 cyfr."]
    # GUS: reszta 10 oznacza cyfrę kontrolną 0 (inaczej niż w NIP, gdzie taki numer nie istnieje).
    total = sum(int(d) * w for d, w in zip(digits[:-1], weights, strict=True)) % 11 % 10
    if total != int(digits[-1]):
        return ["Nieprawidłowa suma kontrolna REGON."]
    return []


def validate_zip_code(value: str) -> list[str]:
    if not _ZIP_RE.fullmatch(value.strip()):
        return ["Wpisz kod pocztowy w formacie 00-000."]
    return []


def validate_email(value: str) -> list[str]:
    if not _EMAIL_RE.fullmatch(value.strip()):
        return ["Wpisz poprawny adres e-mail, np. jan@example.pl."]
    return []


def validate_epuap(value: str) -> list[str]:
    if not _EPUAP_RE.fullmatch(value.strip()):
        return ["Wpisz adres skrytki ePUAP w formacie /nazwa_urzedu/SkrytkaESP."]
    return []


def validate_ade(value: str) -> list[str]:
    if not _ADE_RE.fullmatch(value.strip().upper()):
        return ["Wpisz adres do e-Doręczeń w formacie AE:PL-12345-67890-ABCDE-12."]
    return []


def validate_serial_number_id_card(value: str) -> list[str]:
    text = re.sub(r"\s+", "", value).upper()
    if not _ID_CARD_RE.fullmatch(text):
        return ["Wpisz serię i numer dowodu: 3 litery i 6 cyfr, np. ABA300000."]
    total = sum(
        (ord(ch) - 55 if ch.isalpha() else int(ch)) * weight
        for ch, weight in zip(text, _ID_CARD_WEIGHTS, strict=True)
    )
    if total % 10 != int(text[3]):
        return ["Nieprawidłowa seria i numer dowodu osobistego (cyfra kontrolna)."]
    return []


_CUSTOM_VALIDATORS = {
    CustomValidator.PESEL: validate_pesel,
    CustomValidator.NIP: validate_nip,
    CustomValidator.REGON: validate_regon,
    CustomValidator.ZIP_CODE: validate_zip_code,
    CustomValidator.EMAIL: validate_email,
    CustomValidator.EPUAP: validate_epuap,
    CustomValidator.ADE: validate_ade,
    CustomValidator.SERIAL_NUMBER_ID_CARD: validate_serial_number_id_card,
}


def normalize_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip()


def validate_value(value: Any, rules: ValidationRules | None) -> tuple[bool, list[str], str | None]:
    """Zwraca (valid, errors, normalized_value). Pusta wartość przechodzi (required osobno)."""
    normalized = normalize_value(value)
    if normalized is None or normalized == "":
        return True, [], normalized
    if rules is None:
        return True, [], normalized

    errors: list[str] = []
    text = normalized

    if rules.data_type == DataType.INTEGER:
        if not re.fullmatch(r"-?\d+", text):
            errors.append("Wartość musi być liczbą całkowitą.")
    elif rules.data_type == DataType.NUMBER:
        try:
            float(text.replace(",", "."))
        except ValueError:
            errors.append("Wartość musi być liczbą.")
    elif rules.data_type == DataType.BOOLEAN:
        if text.lower() not in {"true", "false", "1", "0", "tak", "nie"}:
            errors.append("Wartość musi być logiczna (true/false).")
    elif rules.data_type == DataType.DATE:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            errors.append("Data musi mieć format RRRR-MM-DD.")
        else:
            try:
                date.fromisoformat(text)
            except ValueError:
                errors.append("Nieprawidłowa data.")

    if rules.min_length is not None and len(text) < rules.min_length:
        errors.append(f"Wartość jest za krótka (min. {rules.min_length} znaków).")
    if rules.max_length is not None and len(text) > rules.max_length:
        errors.append(f"Wartość jest za długa (maks. {rules.max_length} znaków).")

    if rules.min_value is not None or rules.max_value is not None:
        try:
            numeric = float(text.replace(",", "."))
            if rules.min_value is not None and numeric < rules.min_value:
                errors.append(f"Wartość jest mniejsza niż minimum ({rules.min_value}).")
            if rules.max_value is not None and numeric > rules.max_value:
                errors.append(f"Wartość jest większa niż maksimum ({rules.max_value}).")
        except ValueError:
            errors.append("Wartość musi być liczbą, aby sprawdzić zakres.")

    if rules.pattern is not None:
        try:
            if re.fullmatch(rules.pattern, text) is None:
                errors.append("Wartość nie pasuje do wymaganego wzorca.")
        except re.error:
            errors.append("Reguła walidacji zawiera nieprawidłowe wyrażenie regularne.")

    if rules.custom_validator is not None:
        errors.extend(_CUSTOM_VALIDATORS[rules.custom_validator](text))

    if errors and rules.error_message:
        errors = [rules.error_message]
    return not errors, errors, normalized
