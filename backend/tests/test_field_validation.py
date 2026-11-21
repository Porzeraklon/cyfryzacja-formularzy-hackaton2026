"""Testy walidatorów wartości pól (katalog COI + historyczne aliasy).

Poprawne numery z sumami kontrolnymi zostały wyliczone algorytmami (PESEL, NIP, REGON GUS,
dowód osobisty MSWiA), a nie zgadnięte; ABA300000 to powszechnie znany poprawny numer dowodu.
"""

import pytest

from backend.app.schemas.form_schema import COI_VALIDATORS, CustomValidator, ValidationRules
from backend.app.services import field_validation as fv
from backend.app.services.field_validation import (
    COI_VALIDATOR_NAMES,
    REQUIRED_MESSAGE,
    validate_by_names,
    validate_value,
)


def _check(validator: CustomValidator, value: str) -> list[str]:
    return fv._CUSTOM_VALIDATORS[validator](value)


class TestCatalogCoverage:
    def test_every_custom_validator_has_implementation(self):
        missing = set(CustomValidator) - set(fv._CUSTOM_VALIDATORS)
        assert not missing, f"Brak implementacji walidatorów: {sorted(m.value for m in missing)}"

    def test_coi_validator_names_order_from_technical_rules(self):
        assert COI_VALIDATOR_NAMES == (
            "REQUIRED", "EMAIL", "PESEL", "NIP", "REGON", "ZIP_CODE", "EPUAP", "ADE",
            "SERIAL_NUMBER_ID_CARD",
        )

    def test_coi_validator_names_match_schema_catalog(self):
        assert set(COI_VALIDATOR_NAMES) - {"REQUIRED"} == {v.value for v in COI_VALIDATORS}

    @pytest.mark.parametrize("validator", list(CustomValidator))
    def test_validate_value_accepts_every_validator(self, validator):
        valid, errors, _ = validate_value("x", ValidationRules(custom_validator=validator))
        assert valid is False and errors


class TestPesel:
    @pytest.mark.parametrize("value", ["44051401458", "02270803624", "90010100016", "04211500016"])
    def test_valid(self, value):
        assert _check(CustomValidator.PESEL, value) == []

    def test_bad_checksum(self):
        assert any("suma kontrolna" in e for e in _check(CustomValidator.PESEL, "44051401459"))

    @pytest.mark.parametrize("value", ["4405140145", "4405140145a", "440514014589", "٤٤٠٥١٤٠١٤٥٨"])
    def test_bad_format(self, value):
        assert _check(CustomValidator.PESEL, value) == ["PESEL musi składać się z 11 cyfr."]

    @pytest.mark.parametrize("value", ["44131401458", "44001401458", "44991401458"])
    def test_bad_month(self, value):
        # Miesiąc 13/00/99 (99 → XIX w., miesiąc 19) nie istnieje → błędna data urodzenia.
        assert "Nieprawidłowa data urodzenia w numerze PESEL." in _check(CustomValidator.PESEL, value)

    def test_bad_day(self):
        assert "Nieprawidłowa data urodzenia w numerze PESEL." in _check(
            CustomValidator.PESEL, "44023101458"
        )


class TestNip:
    @pytest.mark.parametrize("value", ["1234563218", "123-456-32-18", "5260001269", "7773141769"])
    def test_valid(self, value):
        assert _check(CustomValidator.NIP, value) == []

    @pytest.mark.parametrize("value", ["1234563219", "1234563030"])  # 2. przypadek: reszta 10
    def test_bad_checksum(self, value):
        assert _check(CustomValidator.NIP, value) == ["Nieprawidłowa suma kontrolna NIP."]

    def test_bad_format(self):
        assert _check(CustomValidator.NIP, "12345") == ["NIP musi składać się z 10 cyfr."]


class TestRegon:
    @pytest.mark.parametrize("value", ["123456785", "12345678512347", "123456160"])
    def test_valid(self, value):
        # 123456160: reszta 10 → cyfra kontrolna 0 (algorytm GUS)
        assert _check(CustomValidator.REGON, value) == []

    @pytest.mark.parametrize("value", ["123456786", "12345678512348"])
    def test_bad_checksum(self, value):
        assert _check(CustomValidator.REGON, value) == ["Nieprawidłowa suma kontrolna REGON."]

    def test_bad_format(self):
        assert _check(CustomValidator.REGON, "1234567") == ["REGON musi składać się z 9 lub 14 cyfr."]


class TestEmail:
    @pytest.mark.parametrize("value", ["jan@example.pl", " anna.nowak+urzad@gov.pl ", "a@b.co"])
    def test_valid(self, value):
        assert _check(CustomValidator.EMAIL, value) == []

    @pytest.mark.parametrize("value", ["jan", "jan@", "jan@example", "jan@example.p", "j an@ex.pl", "a@@b.pl"])
    def test_invalid(self, value):
        assert _check(CustomValidator.EMAIL, value) == ["Wpisz poprawny adres e-mail, np. jan@example.pl."]


class TestZipCode:
    @pytest.mark.parametrize("value", ["00-950", " 31-510 "])
    def test_valid(self, value):
        assert _check(CustomValidator.ZIP_CODE, value) == []

    @pytest.mark.parametrize("value", ["00950", "0-0950", "00-95", "AB-123"])
    def test_invalid(self, value):
        assert _check(CustomValidator.ZIP_CODE, value) == ["Wpisz kod pocztowy w formacie 00-000."]

    @pytest.mark.parametrize("value", ["00-950", "00950"])
    def test_postal_code_is_alias(self, value):
        assert _check(CustomValidator.POSTAL_CODE, value) == _check(CustomValidator.ZIP_CODE, value)


class TestEpuap:
    @pytest.mark.parametrize("value", ["/nazwa_urzedu/SkrytkaESP", "/umwarszawa/skrytka"])
    def test_valid(self, value):
        assert _check(CustomValidator.EPUAP, value) == []

    @pytest.mark.parametrize("value", ["nazwa_urzedu/SkrytkaESP", "/nazwa_urzedu", "/a/b/c", "/a b/c"])
    def test_invalid(self, value):
        assert _check(CustomValidator.EPUAP, value) == [
            "Wpisz adres skrytki ePUAP w formacie /nazwa_urzedu/SkrytkaESP."
        ]


class TestAde:
    @pytest.mark.parametrize("value", ["AE:PL-12345-67890-ABCDE-12", "ae:pl-12345-67890-abcde-12"])
    def test_valid(self, value):
        assert _check(CustomValidator.ADE, value) == []

    @pytest.mark.parametrize("value", ["AE:PL-1234-67890-ABCDE-12", "PL-12345-67890-ABCDE-12",
                                       "AE:PL-12345-67890-ABCD1-12", "AE:DE-12345-67890-ABCDE-12"])
    def test_invalid(self, value):
        assert _check(CustomValidator.ADE, value) == [
            "Wpisz adres do e-Doręczeń w formacie AE:PL-12345-67890-ABCDE-12."
        ]


class TestIdCard:
    @pytest.mark.parametrize("value", ["ABA300000", "aba300000", "ABA 300000", "AXB912345", "ZZZ499999"])
    def test_valid(self, value):
        assert _check(CustomValidator.SERIAL_NUMBER_ID_CARD, value) == []

    @pytest.mark.parametrize("value", ["ABA400000", "ABA300001", "AXB812345"])
    def test_bad_check_digit(self, value):
        assert _check(CustomValidator.SERIAL_NUMBER_ID_CARD, value) == [
            "Sprawdź serię i numer dowodu. Przepisz je dokładnie z dowodu osobistego."
        ]

    @pytest.mark.parametrize("value", ["AB300000", "ABA30000", "1BA300000", "ĄBA300000"])
    def test_bad_format(self, value):
        assert _check(CustomValidator.SERIAL_NUMBER_ID_CARD, value) == [
            "Wpisz serię i numer dowodu: 3 litery i 6 cyfr, np. ABA300000."
        ]


class TestPlPhone:
    @pytest.mark.parametrize("value", ["600100200", "+48 600 100 200", "(+48) 600-100-200"])
    def test_valid(self, value):
        assert _check(CustomValidator.PL_PHONE, value) == []

    def test_invalid(self):
        assert _check(CustomValidator.PL_PHONE, "12345")

    def test_not_in_coi_catalog(self):
        assert CustomValidator.PL_PHONE not in COI_VALIDATORS
        assert "PL_PHONE" not in COI_VALIDATOR_NAMES


class TestValidateByNames:
    @pytest.mark.parametrize("empty", [None, "", "   ", False, [], ()])
    def test_required_on_empty(self, empty):
        assert validate_by_names(empty, ["REQUIRED", "PESEL"]) == [REQUIRED_MESSAGE]
        assert REQUIRED_MESSAGE == "To pole jest wymagane."

    def test_empty_without_required_passes(self):
        assert validate_by_names("", ["PESEL", "EMAIL"]) == []

    def test_required_satisfied(self):
        assert validate_by_names("cokolwiek", ["REQUIRED"]) == []
        assert validate_by_names(["opcja_1"], ["REQUIRED"]) == []
        assert validate_by_names(True, ["REQUIRED"]) == []

    def test_runs_value_validators(self):
        assert validate_by_names("44051401458", ["REQUIRED", "PESEL"]) == []
        assert validate_by_names("44051401459", ["REQUIRED", "PESEL"]) == [
            "Nieprawidłowa suma kontrolna PESEL."
        ]

    def test_multiple_validators_collect_errors(self):
        errors = validate_by_names("abc", ["EMAIL", "ZIP_CODE"])
        assert errors == ["Wpisz poprawny adres e-mail, np. jan@example.pl.",
                          "Wpisz kod pocztowy w formacie 00-000."]

    def test_unknown_names_ignored(self):
        assert validate_by_names("abc", ["NIEZNANY", "PATTERN", "", "REQUIRED"]) == []

    def test_case_and_whitespace_tolerant(self):
        assert validate_by_names("00950", [" zip_code "]) == ["Wpisz kod pocztowy w formacie 00-000."]

    def test_historic_aliases_accepted_and_deduplicated(self):
        assert validate_by_names("00950", ["ZIP_CODE", "POSTAL_CODE"]) == [
            "Wpisz kod pocztowy w formacie 00-000."
        ]
        assert validate_by_names("12345", ["PL_PHONE"])

    def test_object_entries(self):
        assert validate_by_names("", [{"type": "REQUIRED"}]) == [REQUIRED_MESSAGE]
        assert validate_by_names("00950", [{"name": "ZIP_CODE"}])

    def test_non_text_values_skip_value_validators(self):
        # Liczba gubi zera wiodące (PESEL 02270803624 → 2270803624), float dostaje „.0" -
        # walidatory wartości działają tylko na tekście, jak fdValueErrors we frontendzie.
        assert validate_by_names(2270803624, ["PESEL"]) == []
        assert validate_by_names(1234563218.0, ["NIP"]) == []
        assert validate_by_names(1234563219, ["NIP"]) == []
        assert validate_by_names(0, ["REQUIRED", "NIP"]) == []
        assert validate_by_names("02270803624", ["PESEL"]) == []

    def test_names_as_single_string(self):
        assert validate_by_names("", "REQUIRED") == [REQUIRED_MESSAGE]
        assert validate_by_names("00950", "ZIP_CODE") == ["Wpisz kod pocztowy w formacie 00-000."]
        assert validate_by_names("00-950", "ZIP_CODE") == []

    def test_names_as_single_object(self):
        assert validate_by_names("", {"type": "REQUIRED"}) == [REQUIRED_MESSAGE]

    def test_names_none_means_no_validators(self):
        assert validate_by_names("x", None) == []
        assert validate_by_names("", None) == []

    def test_multi_choice_values_skip_value_validators(self):
        assert validate_by_names(["a", "b"], ["REQUIRED", "EMAIL"]) == []

    @pytest.mark.parametrize(("name", "valid"), [
        ("EMAIL", "jan@example.pl"), ("PESEL", "44051401458"), ("NIP", "1234563218"),
        ("REGON", "123456785"), ("ZIP_CODE", "00-950"), ("EPUAP", "/urzad/SkrytkaESP"),
        ("ADE", "AE:PL-12345-67890-ABCDE-12"), ("SERIAL_NUMBER_ID_CARD", "ABA300000"),
    ])
    def test_every_catalog_validator_by_name(self, name, valid):
        assert validate_by_names(valid, ["REQUIRED", name]) == []
        assert validate_by_names("x", ["REQUIRED", name])


class TestValidateValueCompat:
    def test_signature_and_new_validators(self):
        valid, errors, normalized = validate_value(
            " ABA300000 ", ValidationRules(custom_validator=CustomValidator.SERIAL_NUMBER_ID_CARD)
        )
        assert (valid, errors, normalized) == (True, [], "ABA300000")

    def test_empty_value_passes(self):
        assert validate_value("", ValidationRules(custom_validator=CustomValidator.EMAIL)) == (True, [], "")

    def test_error_message_override(self):
        rules = ValidationRules(custom_validator=CustomValidator.EMAIL, error_message="Popraw e-mail.")
        assert validate_value("zly", rules) == (False, ["Popraw e-mail."], "zly")
