"""[OWNERSHIP: Branch 1 - feat/backend-ai-pipeline]

Heurystyki etykiet pól (deterministyczne, offline): walidatory COI, słowniki predefiniowane,
typy danych, zgody, pola "inne/jakie" oraz rozpoznawanie sekcji standardowych.
Wszystkie wzorce działają na tekście znormalizowanym przez `norm()` (małe litery, bez diakrytyków).
"""

import re
import unicodedata

from backend.app.schemas.form_schema import CustomValidator

_PL_TRANSLIT = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def norm(text: str | None) -> str:
    """Małe litery, bez polskich znaków i nadmiarowych spacji."""
    value = unicodedata.normalize("NFKD", (text or "").translate(_PL_TRANSLIT))
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", value).strip()


def slug(text: str | None, fallback: str = "pole") -> str:
    value = re.sub(r"[^a-z0-9]+", "_", norm(text)).strip("_")
    return value[:50].strip("_") or fallback


# Etykieta pola tekstowego -> walidator COI (Reguły techniczne: lista walidatorów).
VALIDATOR_RULES: tuple[tuple[re.Pattern[str], CustomValidator], ...] = (
    (re.compile(r"\bpesel\b"), CustomValidator.PESEL),
    (re.compile(r"\bnip\b"), CustomValidator.NIP),
    (re.compile(r"\bregon\b"), CustomValidator.REGON),
    (re.compile(r"kod pocztowy|\bkod\b.*\bpoczt"), CustomValidator.ZIP_CODE),
    (re.compile(r"e-?mail|poczta elektroniczn|adres poczty e"), CustomValidator.EMAIL),
    (re.compile(r"epuap|skrytk"), CustomValidator.EPUAP),
    (re.compile(r"e-?dorecz|\bade\b"), CustomValidator.ADE),
    (
        re.compile(r"seria i (nr|numer) dowodu|(nr|numer) dowodu osobistego|seria i numer dokumentu tozsamosci"),
        CustomValidator.SERIAL_NUMBER_ID_CARD,
    ),
)

# Etykieta pola -> słownik predefiniowany COI (materiał "Słowniki").
DICTIONARY_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"numer kierunkow|prefiks"), "dict_dial_codes"),
    (re.compile(r"^(kraj|panstwo)\b|obywatelstw|kraj (urodzenia|zamieszkania|pochodzenia)"), "dict_countries"),
    (re.compile(r"^wojewodztwo\b"), "dict_voivodeships"),
    (re.compile(r"^powiat\b"), "dict_districts"),
    (re.compile(r"^gmina\b"), "dict_communities"),
)
# Słowniki Województwo/Powiat/Gmina/Miejscowość/Kraje - wyłącznie lista rozwijana.
SELECT_ONLY_DICTIONARIES = frozenset(
    {"dict_voivodeships", "dict_districts", "dict_communities", "dict_places", "dict_countries"}
)

DATE_LABEL = re.compile(r"^data\b|\bdata (urodzenia|wydania|zdarzenia|zgonu|zawarcia)\b")
NUMBER_LABEL = re.compile(r"^(kwota|liczba|ilosc|wartosc|powierzchnia)\b|\bw zl\b|\(zl\)")

CONSENT_LABEL = re.compile(r"oswiadczam|wyrazam zgode|zapoznal|akceptuj|zgod[ay] na|potwierdzam, ze")
OTHER_OPTION = re.compile(r"^(inn[aeyi]|pozostal[aey]|other)\b|\binn[aeyi] \(")
YES_OPTION = re.compile(r"^tak\b")
DETAIL_LABEL = re.compile(r"\b(jak[aiey]|podaj|opisz|wpisz|okresl|uzasadni|jesli tak|jezeli tak|inn[aeyi])\b")

# Rozpoznawanie sekcji, które logika domenowa zastępuje kanonicznymi (Standardy).
APPLICANT_SECTION = re.compile(
    r"\b(twoje dane|dane wnioskodawcy|dane osobowe|dane osoby skladajacej|dane wnioskujacego|wnioskodawca)\b"
)
NOT_OWN_DATA = re.compile(r"innej osoby|pelnomocnik|podmiot|dziecka|malzonk|wspolwlasciciel|zmarl")
ADDRESS_SECTION = re.compile(r"\badres\b")
CONTACT_SECTION = re.compile(r"\bkontakt|\bdane kontaktowe")
CONSENT_SECTION = re.compile(r"oswiadczen|\bzgod|klauzul|\brodo\b")
OFFICE_SECTION = re.compile(r"^(urzad|organ)\b|\badresat wniosku\b")

FIRST_NAME = re.compile(r"^imie( pierwsze)?$|^imiona?$")
LAST_NAME = re.compile(r"^nazwisko$")
PESEL = re.compile(r"\bpesel\b")
ADDRESS_FIELD = re.compile(
    r"\b(kraj|wojewodztwo|powiat|gmina|miejscowosc|kod pocztowy|poczta|ulica|nr domu|numer domu"
    r"|nr budynku|numer budynku|nr lokalu|numer lokalu|nr mieszkania)\b"
)
PHONE_FIELD = re.compile(r"telefon|\btel\b")
EMAIL_FIELD = re.compile(r"e-?mail")


def validator_for(label: str) -> CustomValidator | None:
    text = norm(label)
    for pattern, validator in VALIDATOR_RULES:
        if pattern.search(text):
            return validator
    return None


def dictionary_for(label: str) -> str | None:
    text = norm(label)
    for pattern, dictionary_id in DICTIONARY_RULES:
        if pattern.search(text):
            return dictionary_id
    return None


def is_consent(label: str) -> bool:
    return bool(CONSENT_LABEL.search(norm(label)))
