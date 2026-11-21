"""Prawdziwa walidacja wartości pól (działa od razu w core, współdzielona z Branch 1).

Walidatory z katalogu COI (Reguły techniczne): REQUIRED, EMAIL, PESEL, NIP, REGON, ZIP_CODE, EPUAP,
ADE, SERIAL_NUMBER_ID_CARD. Sumy kontrolne: PESEL, NIP, REGON (9 i 14 cyfr), seria i numer dowodu
osobistego. Historyczne aliasy spoza katalogu: POSTAL_CODE (= ZIP_CODE) i PL_PHONE.

Komunikaty błędów są zgodne ze Standardami projektowania formularzy (krótkie zdania, strona czynna,
wskazówka, jak poprawić wartość, np. „Wpisz kod pocztowy w formacie 00-000.") i z implementacją
referencyjną we frontendzie (`frontend/formdef.js`, `FD_VALUE_VALIDATORS`).

Dwa wejścia publiczne:
- `validate_value(value, rules)` - format wewnętrzny (`ValidationRules`), używane przez
  `POST /forms/validate-field`;
- `validate_by_names(value, names)` - format FormDefinition (tablica nazw `validation`).
"""

import re
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from backend.app.schemas.form_schema import CustomValidator, DataType, ValidationRules

# Nazwy walidatorów w kolejności z Reguł technicznych (FormDefinition.questions[].field.validation).
COI_VALIDATOR_NAMES: tuple[str, ...] = (
    "REQUIRED",
    "EMAIL",
    "PESEL",
    "NIP",
    "REGON",
    "ZIP_CODE",
    "EPUAP",
    "ADE",
    "SERIAL_NUMBER_ID_CARD",
)
REQUIRED_MESSAGE = "To pole jest wymagane."

_PESEL_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)
_REGON9_WEIGHTS = (8, 9, 2, 3, 4, 5, 6, 7)
_REGON14_WEIGHTS = (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)
# Dowód osobisty: pozycja 4 (indeks 3) to cyfra kontrolna - waga 0 (nie wchodzi do sumy).
_ID_CARD_WEIGHTS = (7, 3, 1, 0, 7, 3, 1, 7, 3)

# re.ASCII: \d i \w tylko ASCII (bez cyfr arabskich itp., które int() by zaakceptował).
_PESEL_RE = re.compile(r"\d{11}", re.ASCII)
_NIP_RE = re.compile(r"\d{10}", re.ASCII)
_REGON9_RE = re.compile(r"\d{9}", re.ASCII)
_REGON14_RE = re.compile(r"\d{14}", re.ASCII)
_PHONE_RE = re.compile(r"(?:\+48)?\d{9}", re.ASCII)
_ZIP_CODE_RE = re.compile(r"\d{2}-\d{3}", re.ASCII)
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}")
_EPUAP_RE = re.compile(r"/[^/\s]+/[^/\s]+")
_ADE_RE = re.compile(r"AE:PL-\d{5}-\d{5}-[A-Z]{5}-\d{2}", re.ASCII)
_ID_CARD_RE = re.compile(r"[A-Z]{3}\d{6}", re.ASCII)
ID_CARD_CHECK_MESSAGE = "Sprawdź serię i numer dowodu. Przepisz je dokładnie z dowodu osobistego."


def _checksum(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights, strict=False)) % 10


def validate_pesel(value: str) -> list[str]:
    digits = value.strip()
    if not _PESEL_RE.fullmatch(digits):
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
    if not _NIP_RE.fullmatch(digits):
        return ["NIP musi składać się z 10 cyfr."]
    total = sum(int(d) * w for d, w in zip(digits[:9], _NIP_WEIGHTS, strict=True)) % 11
    # Reszta 10 oznacza, że taki NIP nie mógł zostać nadany.
    if total == 10 or total != int(digits[9]):
        return ["Nieprawidłowa suma kontrolna NIP."]
    return []


def validate_regon(value: str) -> list[str]:
    digits = re.sub(r"[\s-]", "", value)
    if _REGON9_RE.fullmatch(digits):
        weights = _REGON9_WEIGHTS
    elif _REGON14_RE.fullmatch(digits):
        weights = _REGON14_WEIGHTS
    else:
        return ["REGON musi składać się z 9 lub 14 cyfr."]
    total = sum(int(d) * w for d, w in zip(digits[:-1], weights, strict=True)) % 11
    # Algorytm GUS: reszta 10 daje cyfrę kontrolną 0.
    if total % 10 != int(digits[-1]):
        return ["Nieprawidłowa suma kontrolna REGON."]
    return []


def validate_email(value: str) -> list[str]:
    if not _EMAIL_RE.fullmatch(value.strip()):
        return ["Wpisz poprawny adres e-mail, np. jan@example.pl."]
    return []


def validate_zip_code(value: str) -> list[str]:
    if not _ZIP_CODE_RE.fullmatch(value.strip()):
        return ["Wpisz kod pocztowy w formacie 00-000."]
    return []


def validate_postal_code(value: str) -> list[str]:
    """Historyczny alias ZIP_CODE (eksport FormDefinition zapisuje ZIP_CODE)."""
    return validate_zip_code(value)


def validate_epuap(value: str) -> list[str]:
    """Adres skrytki ePUAP: /identyfikator_podmiotu/nazwa_skrytki, np. /nazwa_urzedu/SkrytkaESP."""
    if not _EPUAP_RE.fullmatch(value.strip()):
        return ["Wpisz adres skrytki ePUAP w formacie /nazwa_urzedu/SkrytkaESP."]
    return []


def validate_ade(value: str) -> list[str]:
    """Adres do e-Doręczeń (ADE), np. AE:PL-12345-67890-ABCDE-12 (wielkość liter bez znaczenia)."""
    if not _ADE_RE.fullmatch(value.strip().upper()):
        return ["Wpisz adres do e-Doręczeń w formacie AE:PL-12345-67890-ABCDE-12."]
    return []


def validate_id_card(value: str) -> list[str]:
    """Seria i numer dowodu osobistego: 3 litery + 6 cyfr, cyfra kontrolna na 4. pozycji.

    Litery mają wartości A=10 … Z=35, wagi 7,3,1,(kontrolna),7,3,1,7,3; suma mod 10 = cyfra
    kontrolna. Przykład poprawny: ABA300000. Spacje są pomijane, małe litery dopuszczalne.
    """
    text = re.sub(r"\s", "", value).upper()
    if not _ID_CARD_RE.fullmatch(text):
        return ["Wpisz serię i numer dowodu: 3 litery i 6 cyfr, np. ABA300000."]
    values = [int(ch) if ch.isdigit() else ord(ch) - ord("A") + 10 for ch in text]
    total = sum(v * w for v, w in zip(values, _ID_CARD_WEIGHTS, strict=True))
    if total % 10 != values[3]:
        return [ID_CARD_CHECK_MESSAGE]
    return []


def validate_pl_phone(value: str) -> list[str]:
    """Historyczny walidator spoza katalogu COI - eksport FormDefinition go pomija.

    W formularzach COI numer telefonu to pole tekstowe + słownik „Numery kierunkowe”.
    """
    normalized = re.sub(r"[\s\-()]", "", value.strip())
    if not _PHONE_RE.fullmatch(normalized):
        return ["Numer telefonu musi mieć 9 cyfr (opcjonalnie z prefiksem +48)."]
    return []


# Musi pokrywać KAŻDĄ wartość CustomValidator (wymusza to test_field_validation.py).
_CUSTOM_VALIDATORS: dict[CustomValidator, Callable[[str], list[str]]] = {
    CustomValidator.EMAIL: validate_email,
    CustomValidator.PESEL: validate_pesel,
    CustomValidator.NIP: validate_nip,
    CustomValidator.REGON: validate_regon,
    CustomValidator.ZIP_CODE: validate_zip_code,
    CustomValidator.EPUAP: validate_epuap,
    CustomValidator.ADE: validate_ade,
    CustomValidator.SERIAL_NUMBER_ID_CARD: validate_id_card,
    CustomValidator.POSTAL_CODE: validate_postal_code,
    CustomValidator.PL_PHONE: validate_pl_phone,
}


def normalize_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip()


def _is_empty(value: Any) -> bool:
    """Pusta wartość kontrolki: brak, pusty tekst, odznaczony checkbox, pusta lista wyborów."""
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, list | tuple | set | dict):
        return len(value) == 0
    return False


def _validator_name(entry: Any) -> str:
    """Nazwa walidatora z wpisu tablicy `validation` (napis albo obiekt z polem type/name)."""
    if isinstance(entry, str):
        return entry.strip().upper()
    if isinstance(entry, dict):
        return str(entry.get("type") or entry.get("name") or "").strip().upper()
    return ""


def validate_by_names(value: Any, names: Iterable[Any] | str | dict | None) -> list[str]:
    """Waliduje wartość wg tablicy nazw walidatorów z FormDefinition (`field.validation`).

    - pusta wartość: błąd tylko przy REQUIRED („To pole jest wymagane."), pozostałe walidatory
      nie są uruchamiane;
    - nieznane nazwy (np. PATTERN bez obsługi) są ignorowane;
    - akceptowane są też historyczne POSTAL_CODE i PL_PHONE;
    - `names` może być pominięte (None - w schemacie `validation` jest opcjonalne) albo podane
      jako pojedynczy napis/obiekt zamiast tablicy;
    - walidatory wartości sprawdzają wyłącznie tekst (jak `fdValueErrors` we frontendzie);
      liczby, wartości logiczne i listy wielokrotnego wyboru sprawdza tylko REQUIRED. Liczb nie
      zamieniamy na tekst, bo `str()` gubi zera wiodące (PESEL urodzonych od 2000 r., kody
      pocztowe) i dokleja „.0" do float - obywatel dostałby mylący błąd formatu.
    """
    if names is None:
        names = []
    elif isinstance(names, str | dict):
        names = [names]
    resolved = [_validator_name(entry) for entry in names]
    if _is_empty(value):
        return [REQUIRED_MESSAGE] if "REQUIRED" in resolved else []
    if not isinstance(value, str):
        return []
    text = value
    errors: list[str] = []
    for name in resolved:
        try:
            check = _CUSTOM_VALIDATORS[CustomValidator(name)]
        except ValueError:
            continue  # REQUIRED (już obsłużone) albo nazwa spoza katalogu
        for message in check(text):
            if message not in errors:
                errors.append(message)
    return errors


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
