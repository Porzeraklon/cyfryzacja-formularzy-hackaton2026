"""Walidatory wartości pól z katalogu COI (services/field_validation.py).

Poprawne numery wyliczone skryptem z algorytmów sum kontrolnych (nie zgadywane).
"""

import pytest

from backend.app.schemas.form_schema import CustomValidator, DataType, ValidationRules
from backend.app.services.field_validation import (
    _CUSTOM_VALIDATORS,
    validate_ade,
    validate_email,
    validate_epuap,
    validate_nip,
    validate_pesel,
    validate_regon,
    validate_serial_number_id_card,
    validate_value,
    validate_zip_code,
)


def test_every_catalog_validator_has_implementation():
    assert set(_CUSTOM_VALIDATORS) == set(CustomValidator)


@pytest.mark.parametrize(
    ("func", "valid", "invalid"),
    [
        # PESEL: urodzeni w XX w., w 2002 r. (miesiąc 27 = lipiec + 20) i w 2004 r.
        (validate_pesel, ["44051401458", "02270803624", "04211500016"],
         ["44051401459", "4405140145", "44A51401458", "44133101458"]),
        (validate_nip, ["1234563218", "526-000-12-69", "526 000 12 69"],
         ["1234563219", "123456321", "12345630310"]),
        # REGON: 9 i 14 cyfr; 100000050 ma resztę 10 -> cyfra kontrolna 0 (GUS).
        (validate_regon, ["123456785", "12345678512347", "100000050"],
         ["123456786", "1234567", "12345678512348"]),
        (validate_zip_code, ["00-950", " 31-001 "], ["00950", "0-950", "00-95a"]),
        (validate_email, ["jan@example.pl", "a.b+c@urzad.gov.pl"], ["jan@", "jan@example", "jan example.pl"]),
        (validate_epuap, ["/UMWarszawa/SkrytkaESP", "/abc/def"], ["UMWarszawa/SkrytkaESP", "/abc", "/a b/c"]),
        (validate_ade, ["AE:PL-12345-67890-ABCDE-12", "ae:pl-12345-67890-abcde-12"],
         ["AE:PL-1234-67890-ABCDE-12", "PL-12345-67890-ABCDE-12"]),
        # Dowód osobisty: ABA300000 (przykład z komunikatu walidatora; suma ważona 113 -> cyfra kontrolna 3).
        (validate_serial_number_id_card, ["ABA300000", "aba 300000"], ["ABA300001", "AB300000", "ABA30000"]),
    ],
)
def test_validators(func, valid, invalid):
    for value in valid:
        assert func(value) == [], value
    for value in invalid:
        assert func(value), value


def test_validate_value_uses_custom_validator_and_error_message():
    rules = ValidationRules(custom_validator=CustomValidator.PESEL, error_message="Podaj poprawny PESEL.")
    assert validate_value("44051401458", rules) == (True, [], "44051401458")
    assert validate_value("44051401459", rules)[1] == ["Podaj poprawny PESEL."]


def test_validate_value_empty_passes_and_data_types():
    assert validate_value("", ValidationRules(custom_validator=CustomValidator.NIP))[0] is True
    assert validate_value("2026-02-30", ValidationRules(data_type=DataType.DATE))[0] is False
    assert validate_value("12,5", ValidationRules(data_type=DataType.NUMBER))[0] is True
    assert validate_value("abc", ValidationRules(min_length=5))[0] is False


def test_validate_field_endpoint(client):
    response = client.post(
        "/api/v1/forms/validate-field", json={"value": "100000050", "rules": {"custom_validator": "REGON"}}
    )
    assert response.status_code == 200 and response.json()["valid"] is True
