"""HTTP adapter checks; no test contacts Baidu."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.providers.baidu.geocoding import GeocodingMatch
from app.schemas.common import CenterPoint
from app.schemas.diagnosis import DiagnosisResult
from app.schemas.location import LocationResult


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"
COORDINATE = {"type": "coordinate", "lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"}


@pytest.fixture(autouse=True)
def no_live_ak(monkeypatch):
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    api.app.dependency_overrides.clear()
    yield
    api.app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(api.app)


def test_health_docs_and_existing_schema_names(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/location/resolve" in paths
    assert "/api/v1/diagnosis" in paths
    assert paths["/api/v1/location/resolve"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/LocationRequest")
    assert paths["/api/v1/diagnosis"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/DiagnosisRequest")


def test_coordinate_location_without_ak(client):
    response = client.post("/api/v1/location/resolve", json={"schema_version": "1.0", "input": COORDINATE})
    assert response.status_code == 200
    result = LocationResult.model_validate(response.json()).root
    assert result.ok is True
    assert result.data.center.model_dump() == {key: COORDINATE[key] for key in ("lng", "lat", "crs")}


def test_address_location_without_ak_is_a_contract_failure(client):
    response = client.post("/api/v1/location/resolve", json={
        "schema_version": "1.0", "input": {"type": "address", "query": "同济大学", "city": "上海市"},
    })
    assert response.status_code == 200
    result = LocationResult.model_validate(response.json()).root
    assert result.ok is False
    assert result.error.code.value == "PROVIDER_ERROR"


def test_address_location_uses_geocoding_provider_without_leaking_ak(client, monkeypatch):
    monkeypatch.setenv("BAIDU_MAP_AK", "test-secret-do-not-return")

    class FakeGeocoder:
        def __init__(self, ak):
            assert ak == "test-secret-do-not-return"

        def geocode(self, query, city):
            assert (query, city) == ("同济大学", "上海市")
            return GeocodingMatch(
                center=CenterPoint(lng=121.506123, lat=31.282456, crs="BD09LL"), label=query,
            )

    monkeypatch.setattr(api, "BaiduGeocodingProvider", FakeGeocoder)
    response = client.post("/api/v1/location/resolve", json={
        "schema_version": "1.0", "input": {"type": "address", "query": "同济大学", "city": "上海市"},
    })
    assert response.status_code == 200
    assert LocationResult.model_validate(response.json()).root.data.label == "同济大学"
    assert "test-secret-do-not-return" not in response.text


def test_diagnosis_without_ak_is_a_contract_failure(client):
    payload = json.loads((CONTRACTS / "diagnosis-request.example.json").read_text(encoding="utf-8"))
    response = client.post("/api/v1/diagnosis", json=payload)
    assert response.status_code == 200
    result = DiagnosisResult.model_validate(response.json()).root
    assert result.ok is False
    assert result.error.code.value == "ISOCHRONE_FAILED"


def test_diagnosis_returns_existing_result_unchanged(client):
    payload = json.loads((CONTRACTS / "diagnosis-request.example.json").read_text(encoding="utf-8"))
    expected = DiagnosisResult.model_validate_json(
        (CONTRACTS / "diagnosis-result.example.json").read_text(encoding="utf-8")
    )
    api.app.dependency_overrides[api.get_diagnosis_runner] = lambda: lambda _: expected
    response = client.post("/api/v1/diagnosis", json=payload)
    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    assert "root" not in response.json()


@pytest.mark.parametrize("path,payload", [
    ("/api/v1/location/resolve", {"schema_version": "1.0", "input": {**COORDINATE, "crs": "WGS84"}}),
    ("/api/v1/diagnosis", {"schema_version": "1.0", "location": COORDINATE, "facility_types": ["hospital"]}),
])
def test_invalid_body_uses_existing_failure_envelope(client, path, payload):
    response = client.post(path, json=payload)
    assert response.status_code == 422
    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert response.json()["meta"]["schema_version"] == "1.0"


def test_unexpected_runner_error_is_sanitized(client):
    def broken(_):
        raise RuntimeError("test-secret-do-not-return")

    api.app.dependency_overrides[api.get_location_runner] = lambda: broken
    response = client.post("/api/v1/location/resolve", json={"schema_version": "1.0", "input": COORDINATE})
    assert response.status_code == 200
    assert response.json()["error"]["code"] == "PROVIDER_ERROR"
    assert "test-secret-do-not-return" not in response.text


def test_cors_only_allows_configured_dev_origin(client):
    headers = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
    allowed = client.options("/api/v1/diagnosis", headers=headers)
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.options("/api/v1/diagnosis", headers={**headers, "Origin": "https://unlisted.example"})
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_wildcard_cors_is_rejected(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(ValueError):
        api._allowed_origins()
