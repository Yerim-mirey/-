"""Shared offline assembly for Agent v1 Mock E2E scenarios.

Scenario data must match the requests the Runner actually sends, because the
Mock Gateway matches canonical requests exactly. Everything here is
deterministic and offline: no Provider, no model service, no network.
"""

import json
from pathlib import Path
from typing import Any, Callable

from app.agent_runtime.runner import run_agent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.planning import PlanningAgent
from app.agents.reviewer import ReviewerAgent
from app.agent_tools.mock_gateway import MockExchange, MockToolGateway, _METHOD_MODELS
from app.schemas.agent import AgentRunState, _decoded_json_pointer_tokens
from app.schemas.diagnosis import DiagnosisResult
from app.schemas.location import LocationRequest, LocationResult
from app.schemas.poi import POISearchRequest, POISearchResult
from app.schemas.routing import RoutingRequest, RoutingResult

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "v1"

# The address resolver fixture centre; every Diagnosis fixture shares it, so the
# address path can reach a configured Diagnosis exchange.
CENTER = {"lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"}
ADDRESS = {"type": "address", "query": "同济大学四平路校区", "city": "上海市"}
ALL_FACILITIES = ["market", "pharmacy", "primary_school"]
DEFAULT_RADIUS_M = 1000


def load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


# --- Requests the Runner is expected to send --------------------------------


def location_request(input_value: dict | None = None) -> LocationRequest:
    return LocationRequest(schema_version="1.0", input=copy_json(input_value or ADDRESS))


def poi_request(
    center: dict | None = None,
    radius_m: int = DEFAULT_RADIUS_M,
    facility_types: list[str] | None = None,
) -> POISearchRequest:
    return POISearchRequest(
        schema_version="1.0",
        center=dict(center or CENTER),
        facility_types=list(facility_types or ALL_FACILITIES),
        search_radius_m=radius_m,
    )


def routing_request(pois: list[dict] | None = None, center: dict | None = None) -> RoutingRequest:
    points = NEARBY_POIS if pois is None else pois
    return RoutingRequest.model_validate({
        "schema_version": "1.0",
        "origin": dict(center or CENTER),
        "targets": [
            {"target_id": point["poi_id"], "location": point["location"]}
            for point in points
        ],
        "travel_mode": "walking",
    })


# --- Deterministic Tool results ---------------------------------------------


def location_result(center: dict | None = None, source: str = "baidu_geocoding") -> LocationResult:
    point = dict(center or CENTER)
    coordinate_source = source == "input_coordinate"
    return LocationResult.model_validate({
        "ok": True,
        "data": {
            "center": point,
            "label": None if coordinate_source else "同济大学四平路校区",
            "formatted_address": None if coordinate_source else "上海市杨浦区四平路1239号",
            "source": source,
        },
        "warnings": [],
        "meta": {
            "schema_version": "1.0",
            "provider": None if coordinate_source else "baidu",
        },
    })


def poi_point(poi_id: str, name: str, facility_type: str, lng: float, lat: float) -> dict:
    return {
        "poi_id": poi_id,
        "name": name,
        "facility_type": facility_type,
        "location": {"lng": lng, "lat": lat, "crs": "BD09LL"},
        "address": None,
        "source": "baidu",
    }


# Three facilities inside the 1000 m query radius: the market is inside the
# 15-minute walk, the pharmacy is not, and the school is closest.
NEARBY_POIS = [
    poi_point("baidu:e2e-market", "示例菜市场", "market", 121.5074, 31.2829),
    poi_point("baidu:e2e-pharmacy", "示例药店", "pharmacy", 121.5092, 31.2875),
    poi_point("baidu:e2e-school", "示例小学", "primary_school", 121.506423, 31.282456),
]


def poi_result(
    center: dict | None = None,
    pois: list[dict] | None = None,
    counts: dict | None = None,
    warnings: list[dict] | None = None,
    requested: list[str] | None = None,
) -> POISearchResult:
    """POI facts for the requested facilities only; other categories stay unknown."""
    points = copy_json(NEARBY_POIS if pois is None else pois)
    types = list(requested or ALL_FACILITIES)
    observed = {
        facility: (
            sum(point["facility_type"] == facility for point in points)
            if facility in types
            else None
        )
        for facility in ALL_FACILITIES
    }
    return POISearchResult.model_validate({
        "ok": True,
        "data": {"center": dict(center or CENTER), "pois": points, "counts": counts or observed},
        "warnings": warnings or [],
        "meta": {"schema_version": "1.0", "provider": "baidu"},
    })


def empty_poi_result() -> POISearchResult:
    """Confirmed absence: every requested facility count is a real zero."""
    return poi_result(
        pois=[],
        counts={"market": 0, "pharmacy": 0, "primary_school": 0},
    )


def partial_poi_result(requested: list[str]) -> POISearchResult:
    """A subset query whose POIs are exactly the requested facilities."""
    points = [point for point in NEARBY_POIS if point["facility_type"] in requested]
    return poi_result(pois=points, requested=requested)


def refresh_gateway(*, complete_first: bool) -> "ScriptedToolGateway":
    """Serve a complete Diagnosis first, then partial facts on every refresh.

    The first Diagnosis still comes from the real Mock, so its request must
    match the configured sample exactly; the refresh is an explicit partial
    sample so the run cannot silently look complete.
    """
    scripted = ScriptedToolGateway({}, fallback=MockToolGateway())
    served = {"diagnosed": not complete_first}

    def diagnose(request):
        if served["diagnosed"]:
            return diagnosis_result("partial")
        served["diagnosed"] = True
        return scripted._fallback.diagnose_community(request)

    scripted._results["diagnose_community"] = []
    scripted.diagnose_strategy = diagnose
    return scripted


def unknown_count_poi_result() -> POISearchResult:
    """Unknown inventory: only the market is a confirmed zero, others are null."""
    return poi_result(
        pois=[NEARBY_POIS[0]],
        counts={"market": 1, "pharmacy": None, "primary_school": None},
        warnings=[{
            "code": "PARTIAL_POI_RESULTS",
            "message": "药店与小学设施数据可能不完整。",
        }],
        requested=["market", "pharmacy", "primary_school"],
    )


def poi_failure_result(*, retryable: bool = True) -> POISearchResult:
    """The bundled POI failure is retryable; scenarios can request the hard one."""
    payload = load("poi-search-failure.example.json")
    payload["error"]["retryable"] = retryable
    return POISearchResult.model_validate(payload)


def diagnosis_failure_result() -> DiagnosisResult:
    return DiagnosisResult.model_validate(load("diagnosis-failure.example.json"))


def routing_result(pois: list[dict] | None = None, center: dict | None = None) -> RoutingResult:
    points = NEARBY_POIS if pois is None else pois
    # 15-minute scale (900 s): market and school inside, pharmacy outside.
    walking = {
        "baidu:e2e-market": (700, 560),
        "baidu:e2e-pharmacy": (1050, 1012),
        "baidu:e2e-school": (120, 95),
    }
    routes = [
        {
            "target_id": point["poi_id"],
            "location": point["location"],
            "status": "success",
            "distance_m": walking[point["poi_id"]][0],
            "duration_s": walking[point["poi_id"]][1],
        }
        for point in points
    ]
    return RoutingResult.model_validate({
        "ok": True,
        "data": {
            "origin": dict(center or CENTER),
            "travel_mode": "walking",
            "routes": routes,
            "summary": {
                "requested": len(routes),
                "success": len(routes),
                "no_route": 0,
                "unavailable": 0,
            },
        },
        "warnings": [],
        "meta": {"schema_version": "1.0", "provider": "baidu"},
    })


def diagnosis_result(outcome: str = "success") -> DiagnosisResult:
    """One bundled v1 Diagnosis sample: ``success``, ``partial``, or ``failure``."""
    name = "diagnosis-result.example.json" if outcome == "success" else f"diagnosis-{outcome}.example.json"
    return DiagnosisResult.model_validate(load(name))


# --- Exchanges and gateways -------------------------------------------------


def exchange(method: str, request, result) -> MockExchange:
    """Build one exchange from the exact request the Runner is expected to send."""
    request_type, result_type = _METHOD_MODELS[method]
    return MockExchange(
        method,
        request_type.model_validate(request.model_dump(mode="json")),
        result_type.model_validate(result.model_dump(mode="json")),
    )


def gateway(*exchanges: MockExchange) -> MockToolGateway:
    return MockToolGateway(list(exchanges))


def location_exchange(input_value: dict | None = None) -> MockExchange:
    value = input_value or ADDRESS
    coordinate = value.get("type") == "coordinate"
    center = {"lng": value["lng"], "lat": value["lat"], "crs": value["crs"]} if coordinate else CENTER
    return exchange(
        "resolve_location",
        location_request(value),
        location_result(center=center,
                        source="input_coordinate" if coordinate else "baidu_geocoding"),
    )


def facility_query_gateway(
    poi_result_value: POISearchResult | None = None,
    facility_types: list[str] | None = None,
) -> MockToolGateway:
    return gateway(
        location_exchange(),
        exchange(
            "search_pois",
            poi_request(facility_types=facility_types),
            poi_result_value or poi_result(),
        ),
    )


def accessibility_gateway(
    pois: list[dict] | None = None,
    facility_types: list[str] | None = None,
) -> MockToolGateway:
    points = NEARBY_POIS if pois is None else pois
    types = facility_types or ALL_FACILITIES
    return gateway(
        location_exchange(),
        exchange(
            "search_pois",
            poi_request(facility_types=types),
            partial_poi_result(types),
        ),
        exchange("calculate_walking_times", routing_request(points), routing_result(points)),
    )


class ScriptedToolGateway:
    """A ToolGateway that returns prepared results in order per method."""

    def __init__(self, results: dict[str, list[Any]], fallback: Any | None = None):
        self._results = {method: list(items) for method, items in results.items()}
        self._fallback = fallback
        self.calls: list[str] = []

    def _next(self, method: str, request):
        queue = self._results.get(method) or []
        if queue:
            self.calls.append(method)
            prepared = queue.pop(0)
            if isinstance(prepared, BaseException):
                raise prepared
            return prepared
        assert self._fallback is not None, f"{method} 没有准备结果，也没有回退 Gateway"
        result = getattr(self._fallback, method)(request)
        fallback_calls = getattr(self._fallback, "calls", None)
        self.calls.append(
            fallback_calls[-1] if fallback_calls else method
        )
        return result

    def queue_after(self, method: str, *results) -> None:
        self._results.setdefault(method, []).extend(results)

    def resolve_location(self, request):
        return self._next("resolve_location", request)

    def search_pois(self, request):
        return self._next("search_pois", request)

    def calculate_walking_times(self, request):
        return self._next("calculate_walking_times", request)

    def generate_isochrone(self, request):
        return self._next("generate_isochrone", request)

    def detect_blindspots(self, request):
        return self._next("detect_blindspots", request)

    def diagnose_community(self, request):
        strategy = getattr(self, "diagnose_strategy", None)
        if strategy is not None:
            self.calls.append("diagnose_community")
            return strategy(request)
        return self._next("diagnose_community", request)


# --- Controlled structured model --------------------------------------------


class SceneLLM:
    """Return prepared objects per Agent role, optionally computed per call."""

    def __init__(
        self,
        outputs: dict[str, Any] | None = None,
        callables: dict[str, Callable] | None = None,
    ):
        self._outputs = dict(outputs or {})
        self._callables = dict(callables or {})
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def role_of(system_prompt: str) -> str:
        if "Orchestrator" in system_prompt:
            return "orchestrator"
        if "Planning Agent" in system_prompt:
            return "planning"
        return "reviewer"

    def generate_object(self, *, system_prompt, user_message, response_schema):
        role = self.role_of(system_prompt)
        self.calls.append((role, user_message))
        if role in self._callables:
            return self._callables[role](user_message)
        if role not in self._outputs:
            raise AssertionError(f"场景没有为 {role} 准备输出")
        return self._outputs[role]

    def roles_called(self) -> list[str]:
        return [role for role, _ in self.calls]

    def calls_for(self, role: str) -> list[str]:
        return [message for name, message in self.calls if name == role]


# --- Brief, Proposal, and Review builders -----------------------------------


def extraction(**changes) -> dict:
    payload = {
        "intent": "facility_query",
        "user_goal": "了解社区设施分布",
        "location": copy_json(ADDRESS),
        "facility_types": list(ALL_FACILITIES),
        "use_standard_facilities": False,
    }
    payload.update(changes)
    return payload


def standard_extraction(intent: str, user_goal: str) -> dict:
    """A broad request that names no facility type; Python applies the three defaults."""
    return {
        "intent": intent,
        "user_goal": user_goal,
        "location": copy_json(ADDRESS),
        "facility_types": [],
        "use_standard_facilities": True,
    }


def payload(message: str) -> dict:
    return json.loads(message)


def proposal_for(round_value: int, bundle_id: str, facilities: list[str]) -> dict:
    item = load("agent-planning-proposal.example.json")
    item["planning_round"] = round_value
    item["evidence_bundle_id"] = bundle_id
    refs = [f"diagnosis:facility-{facility}" for facility in facilities]
    for issue in item["issues"]:
        issue["facility_type"] = facilities[0]
    for planned in [*item["issues"], *item["recommendations"]]:
        planned["evidence_refs"] = list(refs)
    return item


def proposal_from_message(message: str) -> dict:
    data = payload(message)
    facilities = [
        metric["facility_type"] for metric in data["evidence"]["diagnosis"]["data"]["metrics"]
    ]
    return proposal_for(data["planning_round"], data["evidence"]["bundle_id"], facilities)


def review_for(status: str, proposal: dict, bundle_id: str) -> dict:
    item = load("agent-review-result.example.json")
    item["status"] = status
    item["reviewed_proposal_id"] = proposal["proposal_id"]
    item["reviewed_planning_round"] = proposal["planning_round"]
    item["reviewed_evidence_bundle_id"] = bundle_id
    if status == "approved":
        item["issues"] = []
        item["revision_instructions"] = []
        item["missing_evidence"] = []
    elif status == "revision_required":
        item["issues"] = []
        item["revision_instructions"] = ["请把建议收窄到已确认可达的设施"]
        item["missing_evidence"] = []
    else:
        item["issues"] = []
        item["revision_instructions"] = []
        item["missing_evidence"] = [{
            "request_id": "request:diagnosis-refresh",
            "kind": "diagnosis",
            "reason": "需要补齐未知的设施计数",
            "required_json_pointers": ["/diagnosis/data/metrics"],
            "facility_types": ["market", "pharmacy", "primary_school"],
        }]
    return item


def review_from_message(message: str, status: str) -> dict:
    data = payload(message)
    return review_for(status, data["proposal"], data["evidence"]["bundle_id"])


# --- Scenario execution -----------------------------------------------------


def run_scene(*, llm: SceneLLM, gateway, user_message: str, run_id: str = "run:e2e-0001") -> AgentRunState:
    return run_agent(
        run_id=run_id,
        user_message=user_message,
        orchestrator=OrchestratorAgent(llm),
        gateway=gateway,
        planning_agent=PlanningAgent(llm),
        reviewer_agent=ReviewerAgent(llm),
    )


# --- State-chain assertions -------------------------------------------------


def resolve_pointer(document: Any, pointer: str) -> Any:
    target = document
    for token in _decoded_json_pointer_tokens(pointer):
        if isinstance(target, list):
            assert token.isdecimal(), f"{pointer} 的数组下标无效"
            target = target[int(token)]
        else:
            assert token in target, f"{pointer} 在证据包中不存在"
            target = target[token]
    return target


def assert_evidence_refs_resolve(state: AgentRunState) -> None:
    bundle = state.evidence
    assert bundle is not None, "终态必须包含 Evidence"
    document = bundle.model_dump(mode="json")
    assert bundle.refs, "EvidenceBundle 必须至少包含一条引用"
    for ref in bundle.refs:
        assert resolve_pointer(document, ref.json_pointer) is not None


def assert_planned_facilities(state: AgentRunState) -> None:
    """Every planned facility type must be backed by its own Diagnosis evidence ref."""
    proposal = state.planning_proposal
    assert proposal is not None
    expected = {f"diagnosis:facility-{item.value}" for item in state.brief.facility_types}
    actual = set()
    for planned in [*proposal.issues, *proposal.recommendations]:
        actual |= set(planned.evidence_refs)
    assert actual == expected, f"规划引用应为 {expected}，实际为 {actual}"


def assert_planning_chain(state: AgentRunState) -> None:
    """Proposal and Review must be mutually consistent with the run state."""
    proposal = state.planning_proposal
    review = state.review_result
    assert proposal is not None and review is not None, "规划任务终态必须有 Proposal 和 Review"
    assert proposal.planning_round == state.planning_round
    assert proposal.evidence_bundle_id == state.evidence.bundle_id
    assert review.reviewed_proposal_id == proposal.proposal_id
    assert review.reviewed_planning_round == state.planning_round
    assert review.reviewed_evidence_bundle_id == state.evidence.bundle_id
    assert_planned_facilities(state)


def called_methods(gateway) -> list[str]:
    """Tool method names in call order, for both Mock and scripted gateways."""
    return [
        call.method if hasattr(call, "method") else str(call) for call in gateway.calls
    ]


def assert_no_tool_calls(gateway) -> None:
    assert called_methods(gateway) == [], "缺信息时不得调用任何 Tool"


def assert_planning_untouched(state: AgentRunState) -> None:
    assert state.planning_proposal is None
    assert state.review_result is None
    assert state.planning_round == 0


