"""Deterministyczny odczyt nagłówka: tytuł wniosku i urząd-adresat (bez LLM)."""

import pytest

from backend.app.modules.ai_extractor.header import extract_header


@pytest.mark.parametrize(
    ("text", "title", "office"),
    [
        (
            (
                "Ulan-Majorat, dnia......\nUrząd Gminy\nUlan-Majorat\nUlan-Majorat 57\nWNIOSEK\n"
                "o stwierdzenie nadpłaty podatku\nNa podstawie art. 75 ustawy"
            ),
            "Wniosek o stwierdzenie nadpłaty podatku",
            "Urząd Gminy Ulan-Majorat",
        ),
        (
            (
                "(nr telefonu)\nWÓJT GMINY PŁOSKINIA\nPłoskinia 8\n14-526 Płoskinia\nWNIOSEK\no\n"
                "wynajęcie/użyczenie* świetlicy wiejskiej\nProszę o wynajem"
            ),
            "Wniosek o wynajęcie/użyczenie świetlicy wiejskiej",
            "Wójt Gminy Płoskinia",
        ),
        (
            (
                "1/6\nDEKLARACJA O WYSOKOŚCI MIESIĘCZNEJ OPŁATY\nZA GOSPODAROWANIE ODPADAMI KOMUNALNYMI\n"
                "Podstawa prawna:\nPrezydent Miasta Gdyni"
            ),
            "Deklaracja o wysokości miesięcznej opłaty za gospodarowanie odpadami komunalnymi",
            "Prezydent Miasta Gdyni",
        ),
        (
            (
                "Urząd Miejski Wrocławia\nWydział Środowiska\n"
                "Wniosek o wydanie zezwolenia na hodowanie lub utrzymywanie psów ras uznawanych\nza agresywne\n"
                "A DANE IDENTYFIKACYJNE WNIOSKODAWCY"
            ),
            "Wniosek o wydanie zezwolenia na hodowanie lub utrzymywanie psów ras uznawanych za agresywne",
            "Urząd Miejski Wrocławia",
        ),
    ],
)
def test_header_from_real_form_layouts(text, title, office):
    header = extract_header(text)
    assert header.title == title
    assert header.office == office


def test_no_header_returns_none():
    header = extract_header("Imię: ....\nNazwisko: ....\nUrząd\n")
    assert header.title is None and header.office is None
