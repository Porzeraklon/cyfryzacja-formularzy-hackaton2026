"""Prawdziwa walidacja wartości pól (działa od razu w core, współdzielona z Branch 1).

Sumy kontrolne: PESEL, NIP, REGON (9 i 14 cyfr), format PL_PHONE i POSTAL_CODE.
"""

import re
from datetime import date
from typing import Any

from backend.app.schemas.form_schema import CustomValidator, DataType, ValidationRules

_PESEL_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)
_REGON9_WEIGHTS = (8, 9, 2, 3, 4, 5, 6, 7)
_REGON14_WEIGHTS = (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)
_PHONE_RE = re.compile(r"^(?:\+48)?\d{9}$")
_POSTAL_RE = re.compile(r"^\d{2}-\d{3}$")


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
    century = {0: 1900, 2: 2000, 4: 2100, 6: 2200, 8: 1800}.get(month // 20)
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
    total = sum(int(d) * w for d, w in zip(digits[:-1], weights, strict=True)) % 11
    if total == 10 or total != int(digits[-1]):
        return ["Nieprawidłowa suma kontrolna REGON."]
    return []


def validate_pl_phone(value: str) -> list[str]:
    normalized = re.sub(r"[\s\-()]", "", value.strip())
    if not _PHONE_RE.fullmatch(normalized):
        return ["Numer telefonu musi mieć 9 cyfr (opcjonalnie z prefiksem +48)."]
    return []


def validate_postal_code(value: str) -> list[str]:
    if not _POSTAL_RE.fullmatch(value.strip()):
        return ["Kod pocztowy musi mieć format NN-NNN (np. 00-950)."]
    return []


_CUSTOM_VALIDATORS = {
    CustomValidator.PESEL: validate_pesel,
    CustomValidator.NIP: validate_nip,
    CustomValidator.REGON: validate_regon,
    CustomValidator.PL_PHONE: validate_pl_phone,
    CustomValidator.POSTAL_CODE: validate_postal_code,
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
