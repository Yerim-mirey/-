"""HTTP adapter checks; no test contacts Baidu."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.providers.baidu.geocoding import GeocodingMatch
from app.schemas.agent import AgentRunState, RunStatus
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
    seen = {}

    class FakeGeocoder:
        def geocode(self, query, city):
            assert (query, city) == ("同济大学", "上海市")
            return GeocodingMatch(
                center=CenterPoint(lng=121.506123, lat=31.282456, crs="BD09LL"), label=query,
            )

    def fake_build(ak):
        seen["ak"] = ak
        return SimpleNamespace(geocoding=FakeGeocoder())

    monkeypatch.setattr(api, "build_live_providers", fake_build)
    response = client.post("/api/v1/location/resolve", json={
        "schema_version": "1.0", "input": {"type": "address", "query": "同济大学", "city": "上海市"},
    })
    assert response.status_code == 200
    assert LocationResult.model_validate(response.json()).root.data.label == "同济大学"
    assert seen["ak"] == "test-secret-do-not-return"
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


def test_concurrent_requests_respect_one_shared_quota_budget(monkeypatch):
    """Before the shared guard, each request got its own private budget.

    FastAPI runs these sync endpoints in a threadpool, so concurrency is real.
    The guard must throttle the *aggregate* rate: whatever the interleaving,
    the targets granted over an elapsed interval can never exceed the budget
    for that interval. (The guard does not serialise more than the quota
    requires — with ample quota these calls legitimately overlap.)
    """
    import threading
    import time

    from app.modules.routing.service import RoutingService
    from app.providers.baidu import quota as quota_module
    from app.providers.baidu.live import build_live_providers
    from app.providers.baidu.routing import BaiduRoutingProvider
    from app.schemas.routing import RoutingRequest, RoutingSuccess

    rate = 6
    threads = 4
    per_batch = 2
    total = threads * per_batch

    monkeypatch.setenv("BAIDU_MAP_AK", "ak-concurrency-test")
    monkeypatch.setenv("BAIDU_ROUTES_PER_SECOND", str(rate))
    monkeypatch.setenv("BAIDU_QUOTA_MAX_WAIT_SECONDS", "60")
    quota_module.reset_guards()

    def transport(_url, _timeout):
        return {
            "status": 0,
            "result": [
                {"distance": {"value": 100}, "duration": {"value": 80}}
                for _ in range(per_batch)
            ],
        }

    providers = build_live_providers("ak-concurrency-test")
    guarded = BaiduRoutingProvider(
        "ak-concurrency-test", transport=transport, quota_guard=providers.guard
    )
    completed: list[int] = []
    lock = threading.Lock()

    def request_for(index: int) -> RoutingRequest:
        return RoutingRequest.model_validate({
            "schema_version": "1.0",
            "origin": {key: COORDINATE[key] for key in ("lng", "lat", "crs")},
            "targets": [
                {
                    "target_id": f"t{index}-{n}",
                    "location": {
                        "lng": COORDINATE["lng"] + n / 1000,
                        "lat": COORDINATE["lat"],
                        "crs": "BD09LL",
                    },
                }
                for n in range(per_batch)
            ],
            "travel_mode": "walking",
        })

    start = threading.Barrier(threads)

    def worker(request: RoutingRequest) -> None:
        start.wait()
        result = RoutingService(guarded).route(request)
        assert isinstance(result.root, RoutingSuccess), result.root
        with lock:
            completed.append(result.root.data.summary.requested)

    started = time.monotonic()
    workers = [
        threading.Thread(target=worker, args=(request_for(index),))
        for index in range(threads)
    ]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join(timeout=30)
    elapsed = time.monotonic() - started

    assert completed == [per_batch] * threads
    granted = providers.guard.granted_routes
    assert granted == total
    # Allow the full first window plus a scheduling grace period.
    budget = rate * (elapsed + 1.0)
    assert granted <= budget, (
        f"并发请求共放行 {granted} 个目标，超出 {elapsed:.2f}s 内的配额上界 {budget:.1f}"
    )
    quota_module.reset_guards()


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


# --- Agent Run endpoint -----------------------------------------------------


def test_agent_run_without_llm_key_reports_a_failed_run(client, monkeypatch):
    """A well-formed request returns 200 with an explicit failed Run.

    The deployment lacks the key, not the caller: that is a terminal Run state,
    not an HTTP error, and it must say which key is missing.
    """
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("BAIDU_MAP_AK", "ak-present")

    response = client.post(
        "/api/v1/agent/runs", json={"schema_version": "1.0", "user_message": "查一下菜市场"}
    )

    assert response.status_code == 200
    state = AgentRunState.model_validate(response.json())
    assert state.status is RunStatus.FAILED
    assert [error.code for error in state.errors] == [api.MISSING_LLM_KEY_CODE]
    assert state.evidence is None
    assert state.planning_proposal is None


def test_agent_run_without_baidu_ak_reports_a_failed_run(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-a-real-key")
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)

    response = client.post(
        "/api/v1/agent/runs", json={"schema_version": "1.0", "user_message": "查一下菜市场"}
    )

    assert response.status_code == 200
    state = AgentRunState.model_validate(response.json())
    assert state.status is RunStatus.FAILED
    assert [error.code for error in state.errors] == [api.MISSING_BAIDU_KEY_CODE]


def test_agent_run_reports_missing_keys_without_leaking_them(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret-do-not-return")
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)

    response = client.post(
        "/api/v1/agent/runs", json={"schema_version": "1.0", "user_message": "查一下菜市场"}
    )

    assert "sk-secret-do-not-return" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "1.0"},
        {"schema_version": "1.0", "user_message": ""},
        {"schema_version": "2.0", "user_message": "hi"},
        {"schema_version": "1.0", "user_message": "hi", "run_id": "not-prefixed"},
    ],
)
def test_agent_run_invalid_body_is_a_422(client, payload):
    response = client.post("/api/v1/agent/runs", json=payload)

    assert response.status_code == 422


def test_agent_run_uses_a_caller_supplied_run_id(client, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("BAIDU_MAP_AK", "ak-present")

    response = client.post(
        "/api/v1/agent/runs",
        json={"schema_version": "1.0", "user_message": "查一下菜市场", "run_id": "run:caller-01"},
    )

    assert response.json()["run_id"] == "run:caller-01"


def test_agent_run_generates_a_schema_valid_run_id(client, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("BAIDU_MAP_AK", "ak-present")

    response = client.post(
        "/api/v1/agent/runs", json={"schema_version": "1.0", "user_message": "查一下菜市场"}
    )

    run_id = response.json()["run_id"]
    assert run_id.startswith("run:")
    # The schema is the authority, so re-validating proves the format holds.
    AgentRunState.model_validate({**response.json(), "run_id": run_id})


def test_agent_run_failure_is_sanitized(client, monkeypatch):
    """An unexpected runner crash must not leak its message to the caller."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-present")
    monkeypatch.setenv("BAIDU_MAP_AK", "ak-present")

    def broken(_request):
        raise RuntimeError("test-secret-do-not-return")

    api.app.dependency_overrides[api.get_agent_runner] = lambda: broken
    response = client.post(
        "/api/v1/agent/runs", json={"schema_version": "1.0", "user_message": "查一下菜市场"}
    )

    assert response.status_code == 200
    assert "test-secret-do-not-return" not in response.text
    assert response.json()["status"] == "failed"


def test_agent_run_does_not_touch_the_network_when_misconfigured(monkeypatch):
    """Building the runner must not open a socket, even with keys present."""
    import socket

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-present")
    monkeypatch.setenv("BAIDU_MAP_AK", "ak-present")
    blocked = AssertionError("runner construction must not open a socket")
    monkeypatch.setattr(socket.socket, "connect", lambda *_: (_ for _ in ()).throw(blocked))

    runner = api.get_agent_runner()

    assert callable(runner)
