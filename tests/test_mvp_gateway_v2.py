"""V2 Agent access to Core tools stays behind the documented gateway."""

from app.agent_runtime.runner_v2 import run_agent_v2
from app.agents.orchestrator_v2 import OrchestratorAgentV2
from app.modules.poi.facility_catalog import load_facility_catalog
from app.modules.poi.service_v2 import POIServiceV2
from app.modules.routing.service import RoutingService
from app.schemas.agent_v2 import AgentBriefV2
from app.schemas.common import CenterPoint
from app.schemas.diagnosis_v2 import DiagnosisRequestV2, FacilityEvidenceV2
from app.schemas.location import LocationRequest
from app.schemas.poi_v2 import POISearchRequestV2
from app.schemas.routing import RoutingRequest


def test_v2_gateway_adapts_existing_core_services_without_network():
    from app.agent_tools.mvp_gateway_v2 import MVPToolGatewayV2

    class EmptyPOIProvider:
        def search(self, _term, _center, _radius):
            return []

    class EmptyRoutingProvider:
        def route_matrix(self, _origin, destinations):
            return [None] * len(destinations)

    catalog = load_facility_catalog()
    gateway = MVPToolGatewayV2(
        catalog=catalog,
        poi_service=POIServiceV2(EmptyPOIProvider(), catalog),
        routing_service=RoutingService(EmptyRoutingProvider()),
        geocode=None,
    )
    center = CenterPoint(lng=116.35, lat=39.99, crs="BD09LL")
    location = gateway.resolve_location(LocationRequest(schema_version="1.0", input={
        "type": "coordinate", "lng": center.lng, "lat": center.lat, "crs": "BD09LL",
    }))
    poi = gateway.search_pois(POISearchRequestV2(
        schema_version="2.0", center=center, facility_ids=["market"], search_radius_m=1000,
    ))
    diagnosis = gateway.diagnose_community(
        DiagnosisRequestV2(schema_version="2.0", facility_ids=["market"]),
        {"market": FacilityEvidenceV2(status="unavailable")},
    )

    assert location.root.data.center == center
    assert poi.ok is True
    assert poi.data.counts[0].status in {"complete", "partial"}
    assert diagnosis.metrics[0].status == "unavailable"


def test_v2_runtime_calls_gateway_for_all_core_tool_work():
    class Gateway:
        def __init__(self):
            self.calls = []

        def resolve_location(self, request, *, deadline_monotonic=None):
            from app.modules.location.service import resolve_location

            self.calls.append("location")
            return resolve_location(request)

        def search_pois(self, request, *, deadline_monotonic=None, priority_facility_ids=()):
            from app.schemas.poi_v2 import FacilityCountV2, POISearchDataV2, POISearchSuccessV2

            self.calls.append("poi")
            return POISearchSuccessV2(ok=True, data=POISearchDataV2(
                center=request.center,
                pois=[],
                counts=[FacilityCountV2(facility_id="market", count=0, status="complete")],
            ))

        def explore_pois(self, term, center, radius_m, *, deadline_monotonic=None):
            raise AssertionError("No exploratory facility was requested")

        def calculate_walking_times(self, request, *, deadline_monotonic=None):
            raise AssertionError("No point was returned, so routing must be skipped")

        def diagnose_community(self, request, evidence_by_id):
            from app.modules.diagnosis.service_v2 import DiagnosisServiceV2

            self.calls.append("diagnosis")
            return DiagnosisServiceV2().diagnose(
                request,
                evidence_provider=lambda definition: evidence_by_id[definition.id],
                budget_units=len(request.facility_ids),
            )

    class Orchestrator:
        def create_brief(self, _message):
            return AgentBriefV2(
                schema_version="2.0", intent="community_diagnosis", user_goal="诊断菜市场",
                location={"type": "coordinate", "lng": 116.35, "lat": 39.99, "crs": "BD09LL"},
                facility_ids=["market"], unresolved_mentions=[], exploratory_terms=[],
                exploratory_only=False, capability_limitations=[], needs_diagnosis=True,
                needs_planning=False, needs_review=False, missing_information=[],
            )

    gateway = Gateway()
    result = run_agent_v2(
        run_id="run:v2-gateway", user_message="诊断菜市场", orchestrator=Orchestrator(),
        gateway=gateway, catalog=load_facility_catalog(),
    )

    assert result.status == "completed"
    assert gateway.calls == ["location", "poi", "diagnosis"]
    assert result.diagnosis.metrics[0].status == "partial"
