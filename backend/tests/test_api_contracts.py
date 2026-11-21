"""Kontrakty wszystkich endpointów API v1 — działają od razu w core (mock pipeline)."""

import io

from fastapi.testclient import TestClient

from backend.app.schemas.form_schema import FormDocumentSchema
from backend.app.services.formdefinition_export import iter_fields
from backend.tests.conftest import PDF_BYTES, PNG_BYTES


def upload(name="wniosek.pdf", content=PDF_BYTES, mime="application/pdf"):
    return {"file": (name, io.BytesIO(content), mime)}


class TestSystemEndpoints:
    def test_health(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["pipeline_mode"] == "mock"
        assert body["models"]["ready"] is True

    def test_public_config(self, client):
        response = client.get("/api/v1/config")
        assert response.status_code == 200
        body = response.json()
        assert ".pdf" in body["allowed_extensions"]
        assert ".ods" in body["allowed_extensions"]
        assert body["max_file_size_bytes"] == 1024 * 1024
        assert body["jwt_auth_enabled"] is False

    def test_dev_token_in_development(self, client):
        response = client.post("/api/v1/auth/dev-token")
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"].count(".") == 2

    def test_dev_token_forbidden_in_production(self, make_app):
        app = make_app(ENV="production", USE_MOCK_PIPELINE="true", JWT_AUTH_ENABLED="false")
        with TestClient(app) as production_client:
            response = production_client.post("/api/v1/auth/dev-token")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"


class TestConvert:
    def test_convert_rich_mock_form(self, client):
        response = client.post("/api/v1/forms/convert", files=upload())
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        form = FormDocumentSchema.model_validate(body["form"])
        assert form.metadata.title == "Wniosek o stwierdzenie nadpłaty podatku"
        assert len(form.pages) == 3
        assert len(form.dictionaries.mapped_dictionaries) >= 2
        assert len(form.dictionaries.proposed_dictionaries) == 1
        assert form.conditional_flows, "mock musi zawierać przepływy warunkowe"
        fields = {f.field_id: f for p in form.pages for s in p.sections for f in iter_fields(s.fields)}
        assert fields["pesel"].validation_rules.custom_validator.value == "PESEL"
        assert fields["adres_zamieszkania_kod_pocztowy"].validation_rules.custom_validator.value == "ZIP_CODE"
        forbidden = {"PL_PHONE", "POSTAL_CODE"}
        assert not any(
            f.validation_rules
            and f.validation_rules.custom_validator
            and f.validation_rules.custom_validator.value in forbidden
            for f in fields.values()
        )
        assert "phone" not in fields and "gender" not in fields
        fd = body["form_definition"]
        assert [t["type"] for t in fd["tabs"]] == ["MAIN", "MAIN", "MAIN", "FINISH"]
        assert body["processing"]["pipeline_mode"] == "mock"
        assert body["processing"]["pages_count"] == 3
        assert body["processing"]["fields_count"] > 0
        assert body["warnings"][0]["code"] == "MOCK_PIPELINE"

    def test_convert_forbidden_filename(self, client):
        response = client.post("/api/v1/forms/convert", files=upload(name="zly$nazwa.pdf"))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "FILE_NAME_FORBIDDEN"

    def test_convert_mime_mismatch(self, client):
        response = client.post("/api/v1/forms/convert", files=upload(name="skan.pdf", content=PNG_BYTES))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "MIME_MISMATCH"

    def test_convert_too_large(self, client):
        response = client.post("/api/v1/forms/convert", files=upload(content=b"%PDF-" + b"x" * (1024 * 1024)))
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"

    def test_convert_real_pipeline_not_ready(self, make_app, tmp_path):
        app = make_app(
            ENV="development",
            USE_MOCK_PIPELINE="false",
            JWT_AUTH_ENABLED="false",
            MODELS_DIR=str(tmp_path),
        )
        with TestClient(app) as real_client:
            response = real_client.post("/api/v1/forms/convert", files=upload())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "PIPELINE_NOT_READY"


class TestBatch:
    def test_batch_mixed_results(self, client):
        files = [
            ("files", ("wniosek1.pdf", io.BytesIO(PDF_BYTES), "application/pdf")),
            ("files", ("wniosek2.pdf", io.BytesIO(PDF_BYTES), "application/pdf")),
            ("files", ("zly$plik.pdf", io.BytesIO(PDF_BYTES), "application/pdf")),
        ]
        response = client.post("/api/v1/forms/convert-batch", files=files)
        assert response.status_code == 200
        body = response.json()
        assert body["summary"] == {"total": 3, "succeeded": 2, "failed": 1}
        failed = [item for item in body["results"] if item["status"] == "error"]
        assert failed[0]["error"]["code"] == "FILE_NAME_FORBIDDEN"
        for item in body["results"]:
            if item["status"] == "success":
                FormDocumentSchema.model_validate(item["form"])

    def test_batch_limit_enforced(self, client):
        files = [("files", (f"plik{i}.pdf", io.BytesIO(PDF_BYTES), "application/pdf")) for i in range(6)]
        response = client.post("/api/v1/forms/convert-batch", files=files)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "BATCH_TOO_LARGE"


class TestValidation:
    def test_validate_json_accepts_mock(self, client):
        form = client.post("/api/v1/forms/convert", files=upload()).json()["form"]
        response = client.post("/api/v1/forms/validate-json", json={"form": form})
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert body["schema_errors"] == []
        assert body["consistency_errors"] == []

    def test_validate_json_detects_broken_flow(self, client):
        form = client.post("/api/v1/forms/convert", files=upload()).json()["form"]
        form["conditional_flows"][0]["target_id"] = "nieistniejaca_sekcja"
        response = client.post("/api/v1/forms/validate-json", json={"form": form})
        body = response.json()
        assert body["valid"] is False
        assert any("nieistniejaca_sekcja" in error for error in body["consistency_errors"])

    def test_validate_json_schema_errors(self, client):
        response = client.post("/api/v1/forms/validate-json", json={"form": {"metadata": {}}})
        body = response.json()
        assert body["valid"] is False
        assert body["schema_errors"]

    def test_validate_field_pesel_valid(self, client):
        response = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "44051401458",
                "rules": {"custom_validator": "PESEL"},
            },
        )
        assert response.json()["valid"] is True

    def test_validate_field_pesel_invalid_checksum(self, client):
        response = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "44051401459",
                "rules": {"custom_validator": "PESEL"},
            },
        )
        body = response.json()
        assert body["valid"] is False
        assert any("suma kontrolna" in error for error in body["errors"])

    def test_validate_field_zip_code(self, client):
        ok = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "00-950",
                "rules": {"custom_validator": "ZIP_CODE"},
            },
        )
        bad = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "00950",
                "rules": {"custom_validator": "ZIP_CODE"},
            },
        )
        assert ok.json()["valid"] is True
        assert bad.json()["valid"] is False

    def test_validate_field_email(self, client):
        ok = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "jan@example.pl",
                "rules": {"custom_validator": "EMAIL"},
            },
        )
        bad = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "jan@example",
                "rules": {"custom_validator": "EMAIL"},
            },
        )
        assert ok.json()["valid"] is True
        assert bad.json()["valid"] is False

    def test_validate_field_epuap(self, client):
        ok = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "/urzad/SkrytkaESP",
                "rules": {"custom_validator": "EPUAP"},
            },
        )
        bad = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "urzad/SkrytkaESP",
                "rules": {"custom_validator": "EPUAP"},
            },
        )
        assert ok.json()["valid"] is True
        assert bad.json()["valid"] is False

    def test_validate_field_ade(self, client):
        ok = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "AE:PL-12345-67890-ABCDE-12",
                "rules": {"custom_validator": "ADE"},
            },
        )
        bad = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "AE:PL-12345-67890-ABCDE-1",
                "rules": {"custom_validator": "ADE"},
            },
        )
        assert ok.json()["valid"] is True
        assert bad.json()["valid"] is False

    def test_validate_field_serial_number_id_card(self, client):
        ok = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "ABA300000",
                "rules": {"custom_validator": "SERIAL_NUMBER_ID_CARD"},
            },
        )
        bad = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "ABA400000",
                "rules": {"custom_validator": "SERIAL_NUMBER_ID_CARD"},
            },
        )
        assert ok.json()["valid"] is True
        assert bad.json()["valid"] is False

    def test_validate_field_nip_and_regon(self, client):
        nip = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "1234563218",
                "rules": {"custom_validator": "NIP"},
            },
        )
        regon = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "123456785",
                "rules": {"custom_validator": "REGON"},
            },
        )
        assert nip.json()["valid"] is True
        assert regon.json()["valid"] is True

    def test_validate_field_length_and_pattern(self, client):
        response = client.post(
            "/api/v1/forms/validate-field",
            json={
                "value": "ab",
                "rules": {"min_length": 5, "pattern": r"[A-Z]+"},
            },
        )
        body = response.json()
        assert body["valid"] is False
        assert len(body["errors"]) == 2


class TestDictionaries:
    def test_list_dictionaries(self, client):
        response = client.get("/api/v1/dictionaries")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 9
        ids = {entry["dictionary_id"] for entry in body["dictionaries"]}
        assert {
            "dict_voivodeships",
            "dict_gender",
            "dict_document_types",
            "dict_marital_status",
            "dict_countries",
            "dict_districts",
            "dict_communities",
            "dict_places",
            "dict_dial_codes",
        } <= ids

    def test_dictionary_detail(self, client):
        response = client.get("/api/v1/dictionaries/dict_voivodeships")
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 16
        assert {"code", "label"} <= set(body["items"][0])

    def test_dictionary_not_found(self, client):
        response = client.get("/api/v1/dictionaries/nie_ma_takiego")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestJwtAuth:
    def test_auth_required_when_enabled(self, make_app):
        app = make_app(ENV="development", USE_MOCK_PIPELINE="true", JWT_AUTH_ENABLED="true")
        with TestClient(app) as secured_client:
            anonymous = secured_client.post("/api/v1/forms/convert", files=upload())
            assert anonymous.status_code == 401
            token = secured_client.post("/api/v1/auth/dev-token").json()["access_token"]
            authorized = secured_client.post(
                "/api/v1/forms/convert",
                files=upload(),
                headers={"Authorization": f"Bearer {token}"},
            )
            assert authorized.status_code == 200
            bad_token = secured_client.get(
                "/api/v1/dictionaries", headers={"Authorization": "Bearer zly.token.jwt"}
            )
            assert bad_token.status_code == 401
