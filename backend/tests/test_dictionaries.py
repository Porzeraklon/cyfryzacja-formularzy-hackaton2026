"""Testy lokalnej bazy słowników COI i generatora słowników z TERYT GUS."""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from backend.app.core.exceptions import NotFoundError
from backend.app.modules.domain_logic import teryt_import
from backend.app.modules.domain_logic.teryt_import import (
    TerytFormatError,
    build_communities,
    build_districts,
    build_places,
    build_voivodeships,
    parse_simc,
    parse_terc,
    polish_sort_key,
)
from backend.app.services.dictionary_service import (
    DictionaryDatabaseError,
    DictionaryService,
    _load_dictionaries,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DICTIONARIES_DIR = REPO_ROOT / "data" / "dictionaries"
FORMDEF_JS = REPO_ROOT / "frontend" / "formdef.js"

OFFICIAL_NAMES = {
    "dict_voivodeships": "Województwo",
    "dict_districts": "Powiat",
    "dict_communities": "Gmina",
    "dict_places": "Miejscowość",
    "dict_countries": "Kraje",
    "dict_dial_codes": "Numery kierunkowe",
}

# Liczba powiatów (ziemskich + miast na prawach powiatu) w województwach wg TERYT.
DISTRICTS_PER_VOIVODESHIP = {
    "02": 30, "04": 23, "06": 24, "08": 14, "10": 24, "12": 22, "14": 42, "16": 12,
    "18": 25, "20": 17, "22": 20, "24": 36, "26": 14, "28": 21, "30": 35, "32": 21,
}
CITY_DISTRICTS_PER_VOIVODESHIP = {
    "02": 4, "04": 4, "06": 4, "08": 2, "10": 3, "12": 3, "14": 5, "16": 1,
    "18": 4, "20": 3, "22": 4, "24": 19, "26": 1, "28": 2, "30": 4, "32": 3,
}


@pytest.fixture()
def service(settings, monkeypatch):
    monkeypatch.setattr(settings, "DICTIONARIES_DIR", DICTIONARIES_DIR)
    _load_dictionaries.cache_clear()
    yield DictionaryService(settings)
    _load_dictionaries.cache_clear()


def _items(service: DictionaryService, dictionary_id: str) -> list[dict[str, str]]:
    return [item.model_dump() for item in service.get_dictionary(dictionary_id).items]


def _codes(service: DictionaryService, dictionary_id: str) -> list[str]:
    return [item["code"] for item in _items(service, dictionary_id)]


def _js_string_array(name: str) -> list[str]:
    source = FORMDEF_JS.read_text(encoding="utf-8")
    match = re.search(rf"const {name} = \[(.*?)\];", source, re.DOTALL)
    assert match, f"Brak tablicy {name} w frontend/formdef.js"
    return re.findall(r'"([^"]+)"', match.group(1))


def _write_db(directory: Path, filename: str, entries: list[dict]) -> None:
    (directory / filename).write_text(json.dumps({"dictionaries": entries}, ensure_ascii=False), "utf-8")


def _entry(dictionary_id: str, *codes: str) -> dict:
    return {
        "dictionary_id": dictionary_id,
        "name": dictionary_id,
        "description": None,
        "items": [{"code": code, "label": f"pozycja {code}"} for code in codes],
    }


class TestDatabaseIntegrity:
    def test_official_dictionaries_present_with_official_names(self, service):
        names = {entry.dictionary_id: entry.name for entry in service.list_dictionaries().dictionaries}
        for dictionary_id, official in OFFICIAL_NAMES.items():
            assert names.get(dictionary_id) == official

    def test_legacy_dictionaries_kept(self, service):
        ids = service.known_dictionary_ids()
        assert {"dict_gender", "dict_document_types", "dict_marital_status"} <= ids
        assert _codes(service, "dict_gender") == ["K", "M"]

    def test_dictionary_ids_unique_across_files(self):
        ids: list[str] = []
        for path in sorted(DICTIONARIES_DIR.glob("*.json")):
            ids.extend(entry["dictionary_id"] for entry in json.loads(path.read_text("utf-8"))["dictionaries"])
        assert ids and len(ids) == len(set(ids))

    def test_codes_and_labels_unique_in_every_dictionary(self, service):
        for summary in service.list_dictionaries().dictionaries:
            items = _items(service, summary.dictionary_id)
            assert items, summary.dictionary_id
            codes = Counter(item["code"] for item in items)
            labels = Counter(item["label"] for item in items)
            assert not [c for c, n in codes.items() if n > 1], summary.dictionary_id
            assert not [label for label, n in labels.items() if n > 1], summary.dictionary_id
            assert all(item["code"].strip() and item["label"].strip() for item in items)

    def test_voivodeships(self, service):
        codes = _codes(service, "dict_voivodeships")
        assert codes == sorted(DISTRICTS_PER_VOIVODESHIP)


class TestDistricts:
    def test_total_is_380(self, service):
        codes = _codes(service, "dict_districts")
        assert len(codes) == 380
        assert sum(1 for code in codes if int(code[2:]) >= 61) == 66
        assert sum(1 for code in codes if int(code[2:]) < 61) == 314

    def test_codes_are_teryt_wwpp_of_existing_voivodeship(self, service):
        voivodeships = set(_codes(service, "dict_voivodeships"))
        for code in _codes(service, "dict_districts"):
            assert re.fullmatch(r"\d{4}", code), code
            assert code[:2] in voivodeships, code

    def test_counts_per_voivodeship(self, service):
        codes = _codes(service, "dict_districts")
        assert Counter(code[:2] for code in codes) == DISTRICTS_PER_VOIVODESHIP
        assert Counter(code[:2] for code in codes if int(code[2:]) >= 61) == CITY_DISTRICTS_PER_VOIVODESHIP

    def test_known_districts_and_label_convention(self, service):
        labels = {item["code"]: item["label"] for item in _items(service, "dict_districts")}
        assert labels["1206"] == "powiat krakowski"
        assert labels["1261"] == "Kraków (miasto na prawach powiatu)"
        assert labels["1465"] == "Warszawa (miasto na prawach powiatu)"
        assert labels["1432"] == "powiat warszawski zachodni"
        assert labels["0265"] == "Wałbrzych (miasto na prawach powiatu)"
        assert "1431" not in labels  # zniesiony powiat warszawski
        assert labels["3263"] == "Świnoujście (miasto na prawach powiatu)"


class TestCommunitiesAndPlaces:
    def test_communities_are_terc_codes_within_known_districts(self, service):
        districts = set(_codes(service, "dict_districts"))
        codes = _codes(service, "dict_communities")
        assert len(codes) >= 66
        for code in codes:
            assert re.fullmatch(r"\d{6}[1-3]", code), code
            assert code[:4] in districts, code
        labels = {item["code"]: item["label"] for item in _items(service, "dict_communities")}
        assert labels["1261011"] == "Kraków (gmina miejska, pow. Kraków)"
        assert labels["1465011"] == "Warszawa (gmina miejska, pow. Warszawa)"

    def test_every_city_district_has_its_community(self, service):
        communities = set(_codes(service, "dict_communities"))
        for code in _codes(service, "dict_districts"):
            if int(code[2:]) >= 61:
                assert f"{code}011" in communities, code

    def test_places_reference_known_districts(self, service):
        districts = set(_codes(service, "dict_districts"))
        items = _items(service, "dict_places")
        assert len(items) >= 66
        for item in items:
            assert re.fullmatch(r"\d{4}-\d{2}|\d{7}", item["code"]), item
            assert item["code"][:4] in districts or re.fullmatch(r"\d{7}", item["code"]), item
        labels = {item["label"] for item in items}
        assert "Kraków (gm. Kraków, pow. Kraków)" in labels
        assert "Zakopane (gm. Zakopane, pow. tatrzański)" in labels

    def test_demo_subsets_documented(self, service):
        for dictionary_id in ("dict_communities", "dict_places"):
            description = service.get_dictionary(dictionary_id).description or ""
            assert "Podzbiór demonstracyjny" in description
            assert "python -m backend.app.modules.domain_logic.teryt_import" in description


class TestCountriesAndDialCodes:
    def test_countries_iso_alpha2_poland_first_then_polish_collation(self, service):
        items = _items(service, "dict_countries")
        assert items[0] == {"code": "PL", "label": "Polska"}
        assert len(items) >= 195
        assert all(re.fullmatch(r"[A-Z]{2}", item["code"]) for item in items)
        rest = [item["label"] for item in items[1:]]
        assert rest == sorted(rest, key=polish_sort_key)

    def test_countries_consistent_with_frontend_fallback(self, service):
        labels = {item["label"] for item in _items(service, "dict_countries")}
        missing = [name for name in _js_string_array("FD_COUNTRIES") if name not in labels]
        assert not missing

    def test_known_country_codes(self, service):
        by_code = {item["code"]: item["label"] for item in _items(service, "dict_countries")}
        assert by_code["DE"] == "Niemcy"
        assert by_code["GB"] == "Wielka Brytania"
        assert by_code["US"] == "Stany Zjednoczone"
        assert by_code["KR"] == "Korea Południowa"
        assert by_code["XK"] == "Kosowo"

    def test_dial_codes_format_and_order(self, service):
        items = _items(service, "dict_dial_codes")
        assert items[0] == {"code": "+48", "label": "+48 Polska"}
        for item in items:
            assert re.fullmatch(r"\+\d{1,3}(-\d{3})?", item["code"]), item
            assert item["label"].startswith(item["code"] + " "), item
        labels = {item["label"] for item in items}
        assert "+1 Stany Zjednoczone / Kanada" in labels
        assert "+7 Rosja / Kazachstan" in labels

    def test_dial_codes_cover_frontend_fallback(self, service):
        by_code = {item["code"]: item["label"] for item in _items(service, "dict_dial_codes")}
        for entry in _js_string_array("FD_DIAL_CODES"):
            code, names = entry.split(" ", 1)
            assert code in by_code, entry
            for name in names.split(" / "):
                assert name in by_code[code], entry

    def test_dial_code_countries_exist_in_countries_dictionary(self, service):
        countries = {item["label"] for item in _items(service, "dict_countries")}
        for item in _items(service, "dict_dial_codes"):
            for name in item["label"].split(" ", 1)[1].split(" / "):
                assert name in countries, item


class TestMultiFileLoading:
    @pytest.fixture()
    def tmp_service(self, settings, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "DICTIONARIES_DIR", tmp_path)
        _load_dictionaries.cache_clear()
        yield DictionaryService(settings), tmp_path
        _load_dictionaries.cache_clear()

    def test_merges_all_json_files_sorted_by_name(self, tmp_service):
        service, directory = tmp_service
        _write_db(directory, "b.json", [_entry("dict_b", "1")])
        _write_db(directory, "a.json", [_entry("dict_a", "1", "2"), _entry("dict_a2", "x")])
        (directory / "notatki.txt").write_text("nie JSON", "utf-8")
        listing = service.list_dictionaries()
        assert [d.dictionary_id for d in listing.dictionaries] == ["dict_a", "dict_a2", "dict_b"]
        assert listing.total == 3
        assert listing.dictionaries[0].items_count == 2
        assert service.known_dictionary_ids() == {"dict_a", "dict_a2", "dict_b"}

    def test_duplicate_dictionary_id_is_rejected(self, tmp_service):
        service, directory = tmp_service
        _write_db(directory, "a.json", [_entry("dict_x", "1")])
        _write_db(directory, "b.json", [_entry("dict_x", "2")])
        with pytest.raises(DictionaryDatabaseError, match="dict_x"):
            service.list_dictionaries()

    def test_invalid_file_is_reported_with_filename(self, tmp_service):
        service, directory = tmp_service
        _write_db(directory, "zly.json", [{"dictionary_id": "d", "name": "d", "items": [{"code": "1"}]}])
        with pytest.raises(DictionaryDatabaseError, match="zly.json"):
            service.get_dictionary("d")

    def test_extra_item_fields_are_forbidden(self, tmp_service):
        service, directory = tmp_service
        entry = _entry("dict_x", "1")
        entry["items"][0]["parent"] = "02"
        _write_db(directory, "a.json", [entry])
        with pytest.raises(DictionaryDatabaseError):
            service.list_dictionaries()

    def test_missing_or_empty_directory(self, settings, monkeypatch, tmp_path):
        _load_dictionaries.cache_clear()
        monkeypatch.setattr(settings, "DICTIONARIES_DIR", tmp_path / "brak")
        with pytest.raises(NotFoundError):
            DictionaryService(settings).list_dictionaries()
        monkeypatch.setattr(settings, "DICTIONARIES_DIR", tmp_path)
        with pytest.raises(NotFoundError):
            DictionaryService(settings).list_dictionaries()
        _load_dictionaries.cache_clear()


class TestEndpoints:
    @pytest.mark.parametrize("dictionary_id", sorted(OFFICIAL_NAMES))
    def test_official_dictionary_endpoint(self, client, dictionary_id):
        response = client.get(f"/api/v1/dictionaries/{dictionary_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["dictionary_id"] == dictionary_id
        assert body["name"] == OFFICIAL_NAMES[dictionary_id]
        assert body["items"] and set(body["items"][0]) == {"code", "label"}

    def test_list_contains_new_dictionaries(self, client):
        body = client.get("/api/v1/dictionaries").json()
        ids = {entry["dictionary_id"] for entry in body["dictionaries"]}
        assert set(OFFICIAL_NAMES) <= ids
        assert body["total"] == len(body["dictionaries"])

    def test_countries_endpoint_starts_with_poland(self, client):
        body = client.get("/api/v1/dictionaries/dict_countries").json()
        assert body["items"][0] == {"code": "PL", "label": "Polska"}


TERC_SAMPLE = (
    "﻿WOJ;POW;GMI;RODZ;NAZWA;NAZWA_DOD;STAN_NA\n"
    "12;;;;MAŁOPOLSKIE;województwo;2026-01-01\n"
    "12;06;;;krakowski;powiat;2026-01-01\n"
    "12;06;01;1;Skawina;gmina miejska;2026-01-01\n"
    "12;06;02;3;Liszki;gmina miejsko-wiejska;2026-01-01\n"
    "12;06;02;4;Liszki;miasto;2026-01-01\n"
    "12;06;02;5;Liszki;obszar wiejski;2026-01-01\n"
    "12;06;03;2;Zielonki;gmina wiejska;2026-01-01\n"
    "12;61;;;Kraków;miasto na prawach powiatu;2026-01-01\n"
    "12;61;01;1;Kraków;gmina miejska;2026-01-01\n"
    "12;61;02;9;Kraków-Krowodrza;delegatura;2026-01-01\n"
    "14;;;;MAZOWIECKIE;województwo;2026-01-01\n"
    "14;06;;;grójecki;powiat;2026-01-01\n"
    "14;06;05;2;Zielonki;gmina wiejska;2026-01-01\n"
)

SIMC_SAMPLE = (
    "﻿WOJ;POW;GMI;RODZ_GMI;RM;MZ;NAZWA;SYM;SYMPOD;STAN_NA\n"
    "12;61;01;1;96;1;Kraków;0950463;0950463;2026-01-01\n"
    "12;61;02;9;95;1;Krowodrza;0950470;0950463;2026-01-01\n"
    "12;06;02;4;96;1;Liszki;0000001;0000001;2026-01-01\n"
    "12;06;02;5;01;1;Wola;0000002;0000002;2026-01-01\n"
    "12;06;03;2;01;1;Wola;0000003;0000003;2026-01-01\n"
    "12;06;03;2;01;1;Wola;0000004;0000004;2026-01-01\n"
    "12;06;03;2;00;1;Wola Mała;0000005;0000004;2026-01-01\n"
    "14;06;05;2;01;1;Wola;0000006;0000006;2026-01-01\n"
)


class TestTerytImport:
    def test_parse_terc(self):
        terc = parse_terc(TERC_SAMPLE)
        assert {code: unit.name for code, unit in terc.voivodeships.items()} == {
            "12": "małopolskie", "14": "mazowieckie",
        }
        assert sorted(terc.districts) == ["1206", "1261", "1406"]
        assert sorted(terc.communities) == ["1206011", "1206023", "1206032", "1261011", "1406052"]
        assert terc.state_date == "2026-01-01"

    def test_build_terc_dictionaries(self):
        terc = parse_terc(TERC_SAMPLE)
        voivodeships = build_voivodeships(terc)
        assert voivodeships["dictionary_id"] == "dict_voivodeships"
        assert voivodeships["items"][0] == {"code": "12", "label": "małopolskie"}
        districts = build_districts(terc)
        assert districts["name"] == "Powiat"
        assert districts["items"] == [
            {"code": "1206", "label": "powiat krakowski"},
            {"code": "1261", "label": "Kraków (miasto na prawach powiatu)"},
            {"code": "1406", "label": "powiat grójecki"},
        ]
        communities = {i["code"]: i["label"] for i in build_communities(terc)["items"]}
        assert communities["1206023"] == "Liszki (gmina miejsko-wiejska, pow. krakowski)"
        assert communities["1261011"] == "Kraków (gmina miejska, pow. Kraków)"
        assert communities["1406052"] == "Zielonki (gmina wiejska, pow. grójecki)"

    def test_parse_simc_resolves_communities_and_skips_parts(self):
        terc = parse_terc(TERC_SAMPLE)
        places = {place.sym: place for place in parse_simc(SIMC_SAMPLE, terc)}
        assert sorted(places) == ["0000001", "0000002", "0000003", "0000004", "0000006", "0950463"]
        assert places["0950463"].community_code == "1261011"
        assert places["0000001"].community_code == "1206023"  # miasto w gminie miejsko-wiejskiej
        assert places["0000002"].community_name == "Liszki"
        assert places["0000006"].voivodeship_name == "mazowieckie"

    def test_build_places_disambiguates_labels(self):
        terc = parse_terc(TERC_SAMPLE)
        entry = build_places(parse_simc(SIMC_SAMPLE, terc), terc)
        labels = {item["code"]: item["label"] for item in entry["items"]}
        assert entry["dictionary_id"] == "dict_places"
        assert labels["0950463"] == "Kraków (gm. Kraków, pow. Kraków)"
        assert labels["0000002"] == "Wola (gm. Liszki, pow. krakowski)"
        assert labels["0000003"] == "Wola (gm. Zielonki, pow. krakowski) – woj. małopolskie [0000003]"
        assert labels["0000006"] == "Wola (gm. Zielonki, pow. grójecki)"
        assert len(set(labels.values())) == len(labels)

    def test_missing_columns_raise(self):
        with pytest.raises(TerytFormatError, match="RODZ"):
            parse_terc("WOJ;POW;GMI;NAZWA;NAZWA_DOD;STAN_NA\n12;;;X;województwo;2026-01-01\n")
        with pytest.raises(TerytFormatError, match="WOJ"):
            parse_terc("WOJ;POW;GMI;RODZ;NAZWA;NAZWA_DOD;STAN_NA\nAB;;;;X;województwo;2026-01-01\n")

    def test_main_generates_loadable_files(self, tmp_path, settings, monkeypatch, capsys):
        terc_path = tmp_path / "terc.csv"
        simc_path = tmp_path / "simc.csv"
        terc_path.write_text(TERC_SAMPLE, encoding="utf-8")
        simc_path.write_text(SIMC_SAMPLE, encoding="utf-8")
        out = tmp_path / "out"
        argv = ["--terc", str(terc_path), "--simc", str(simc_path), "--out", str(out), "--voivodeships"]
        assert teryt_import.main(argv) == 0
        assert sorted(p.name for p in out.glob("*.json")) == [
            "teryt_communities.json", "teryt_districts.json", "teryt_places.json", "teryt_voivodeships.json",
        ]
        assert "dict_places" in capsys.readouterr().out
        monkeypatch.setattr(settings, "DICTIONARIES_DIR", out)
        _load_dictionaries.cache_clear()
        try:
            service = DictionaryService(settings)
            assert service.known_dictionary_ids() == {
                "dict_voivodeships", "dict_districts", "dict_communities", "dict_places",
            }
            assert len(service.get_dictionary("dict_places").items) == 6
        finally:
            _load_dictionaries.cache_clear()

    def test_main_reports_errors(self, tmp_path, capsys):
        assert teryt_import.main(["--terc", str(tmp_path / "brak.csv"), "--out", str(tmp_path)]) == 1
        assert "Błąd" in capsys.readouterr().err

    def test_polish_sort_key(self):
        words = ["Żory", "Łódź", "Zabrze", "Lublin", "Ełk", "Ostrów", "Ćmielów", "Czechy", "Curaçao"]
        assert sorted(words, key=polish_sort_key) == [
            "Curaçao", "Czechy", "Ćmielów", "Ełk", "Lublin", "Łódź", "Ostrów", "Zabrze", "Żory",
        ]
        assert sorted(["Korea Północna", "Korea Południowa", "Koreańczyk"], key=polish_sort_key) == [
            "Korea Południowa", "Korea Północna", "Koreańczyk",
        ]
