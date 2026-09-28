from fastapi.testclient import TestClient

from app.api import main as api
from app.api import v2 as v2_api
from app.modules.poi.facility_catalog import load_facility_catalog
from app.modules.poi.service_v2 import POIServiceV2
from app.providers.baidu.poi import ProviderPOI
from app.schemas.agent_v2 import AgentBriefV2


def test_v2_catalog_exposes_capabilities_and_metric_policy(monkeypatch):
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    client = TestClient(api.app)
    response = client.get("/api/v2/facilities")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "2.0"
    assert len(body["groups"]) == 7
    kindergarten = next(item for item in body["facilities"] if item["id"] == "kindergarten")
    assert "poi_search" in kindergarten["capabilities"]
    assert "diagnosis" not in kindergarten["capabilities"]
    assert kindergarten["metric_profile"] is None


def test_v2_poi_rejects_unknown_ids_instead_of_silently_searching(monkeypatch):
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    client = TestClient(api.app)
    response = client.post("/api/v2/pois/search", json={
        "schema_version": "2.0",
        "center": {"lng": 114.3, "lat": 30.5, "crs": "BD09LL"},
        "facility_ids": ["invented_facility"],
        "search_radius_m": 1500,
    })

    assert response.status_code == 422


def test_v2_explore_is_explicit_and_returns_unavailable_without_live_ak(monkeypatch):
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    client = TestClient(api.app)
    response = client.post("/api/v2/pois/explore", json={
        "schema_version": "2.0",
        "query": "宠物医院",
        "center": {"lng": 114.3, "lat": 30.5, "crs": "BD09LL"},
        "search_radius_m": 1500,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "MISSING_AK"


def test_v2_explore_rejects_general_topics_without_a_named_facility(monkeypatch):
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    response = TestClient(api.app).post("/api/v2/pois/explore", json={
        "schema_version": "2.0",
        "query": "附近有什么好吃的",
        "center": {"lng": 114.3, "lat": 30.5, "crs": "BD09LL"},
        "search_radius_m": 1500,
    })

    assert response.status_code == 422


def test_v2_explore_does_not_bypass_a_catalog_facility(monkeypatch):
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    response = TestClient(api.app).post("/api/v2/pois/explore", json={
        "schema_version": "2.0",
        "query": "幼儿园",
        "center": {"lng": 114.3, "lat": 30.5, "crs": "BD09LL"},
        "search_radius_m": 1500,
    })

    assert response.status_code == 422


def test_v2_explore_does_not_bypass_catalog_facility_with_context_text(monkeypatch):
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    response = TestClient(api.app).post("/api/v2/pois/explore", json={
        "schema_version": "2.0",
        "query": "附近幼儿园",
        "center": {"lng": 114.3, "lat": 30.5, "crs": "BD09LL"},
        "search_radius_m": 1500,
    })

    assert response.status_code == 422


def test_v2_walking_route_uses_generic_facility_ids_without_live_ak(monkeypatch):
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    response = TestClient(api.app).post("/api/v2/routing/walking", json={
        "schema_version": "2.0",
        "origin": {"lng": 114.3, "lat": 30.5, "crs": "BD09LL"},
        "targets": [{
            "target_id": "library-1",
            "facility_id": "library",
            "location": {"lng": 114.31, "lat": 30.5, "crs": "BD09LL"},
        }],
        "travel_mode": "walking",
    })

    assert response.status_code == 200
    assert response.json()["schema_version"] == "2.0"
    assert response.json()["ok"] is False
    assert response.json()["error_code"] == "PROVIDER_ERROR"


def test_v2_walking_route_rejects_unknown_facility_id(monkeypatch):
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    response = TestClient(api.app).post("/api/v2/routing/walking", json={
        "schema_version": "2.0",
        "origin": {"lng": 114.3, "lat": 30.5, "crs": "BD09LL"},
        "targets": [{
            "target_id": "target-1",
            "facility_id": "not-in-catalog",
            "location": {"lng": 114.31, "lat": 30.5, "crs": "BD09LL"},
        }],
        "travel_mode": "walking",
    })

    assert response.status_code == 422


def test_v2_poi_search_serializes_a_catalog_category_without_live_calls(monkeypatch):
    class FakeProvider:
        def search(self, keyword, center, radius_m):
            return [ProviderPOI("lib-1", "社区图书室", center.lng, center.lat, "社区内")]

    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    api.app.dependency_overrides[v2_api.get_poi_service_v2] = lambda: POIServiceV2(
        FakeProvider(), load_facility_catalog()
    )
    try:
        response = TestClient(api.app).post("/api/v2/pois/search", json={
            "schema_version": "2.0",
            "center": {"lng": 114.3, "lat": 30.5, "crs": "BD09LL"},
            "facility_ids": ["library"],
            "search_radius_m": 1500,
        })
    finally:
        api.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "2.0"
    assert body["ok"] is True
    assert body["data"]["counts"] == [
        {"facility_id": "library", "count": 1, "status": "complete"}
    ]


def test_v2_agent_brief_uses_server_resolved_canonical_facilities(monkeypatch):
    class FakeOrchestrator:
        def create_brief(self, message):
            assert message == "查附近的幼儿园"
            return AgentBriefV2.model_validate({
                "schema_version": "2.0",
                "intent": "facility_query",
                "user_goal": "查询附近幼儿园",
                "location": {"type": "address", "query": "武昌中南路", "city": "武汉市"},
                "facility_ids": ["kindergarten"],
                "unresolved_mentions": [],
                "exploratory_terms": [],
                "exploratory_only": False,
                "capability_limitations": [{
                    "facility_id": "kindergarten",
                    "unsupported_capabilities": ["diagnosis", "planning"],
                    "message": "当前没有适用的诊断与规划能力。",
                }],
                "needs_diagnosis": False,
                "needs_planning": False,
                "needs_review": False,
                "missing_information": [],
            })

    api.app.dependency_overrides[v2_api.get_orchestrator_v2] = lambda: FakeOrchestrator()
    try:
        response = TestClient(api.app).post("/api/v2/agent/brief", json={
            "schema_version": "2.0",
            "user_message": "查附近的幼儿园",
        })
    finally:
        api.app.dependency_overrides.pop(v2_api.get_orchestrator_v2, None)

    assert response.status_code == 200
    assert response.json()["facility_ids"] == ["kindergarten"]
