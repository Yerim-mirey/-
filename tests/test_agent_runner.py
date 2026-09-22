"""Runner execution loop over the Agent graph, with controlled models and Mock Tools."""

import json
from copy import deepcopy
from pathlib import Path

from app.agent_runtime.runner import run_agent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.planning import PlanningAgent
from app.agents.reviewer import ReviewerAgent
from app.agent_tools.mock_gateway import MockToolGateway
from app.schemas.location import CoordinateInput, LocationResult
from app.schemas.poi import POISearchResult


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"
CENTER = {"lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"}


def load(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def failure(kind, retryable):
    payload = deepcopy(load(f"{kind}-failure.example.json"))
    payload["error"]["retryable"] = retryable
    return payload


def market_poi_result():
    """One confirmed market at the requested center, matching POI contract rules."""
    return {
        "ok": True,
        "data": {
            "center": dict(CENTER),
            "pois": [{
                "poi_id": "baidu:mock-market",
                "name": "示例菜市场",
                "facility_type": "market",
                "location": dict(CENTER),
                "address": None,
                "source": "baidu",
            }],
            "counts": {"market": 1, "pharmacy": 0, "primary_school": 0},
        },
        "warnings": [],
        "meta": {"schema_version": "1.0", "provider": "baidu"},
    }


class ControlledLLM:
    """Return one prepared object per Agent role and record every call."""

    def __init__(self, *, extraction=None, proposal=None, review=None, callables=None):
        self._outputs = {
            "orchestrator": extraction,
            "planning": proposal,
            "reviewer": review,
        }
        self._callables = callables or {}
        self.calls = []

    def _role(self, system_prompt):
        if "Orchestrator" in system_prompt:
            return "orchestrator"
        if "Planning Agent" in system_prompt:
            return "planning"
        return "reviewer"

    def generate_object(self, *, system_prompt, user_message, response_schema):
        role = self._role(system_prompt)
        self.calls.append((role, user_message))
        if role in self._callables:
            return self._callables[role](user_message)
        output = self._outputs[role]
        assert output is not None, f"no controlled output for {role}"
        return output

    def calls_for(self, role):
        return [message for name, message in self.calls if name == role]


def extraction(**changes):
    payload = {
        "intent": "facility_query",
        "user_goal": "查询菜市场分布",
        "location": {"type": "coordinate", **CENTER},
        "facility_types": ["market"],
        "use_standard_facilities": False,
    }
    payload.update(changes)
    return payload


def proposal_for(round_value, bundle_id, evidence_id="diagnosis:facility-market"):
    payload = load("agent-planning-proposal.example.json")
    payload["planning_round"] = round_value
    payload["evidence_bundle_id"] = bundle_id
    for item in [*payload["issues"], *payload["recommendations"]]:
        item["evidence_refs"] = [evidence_id]
    return payload


def review_for(status, round_value, bundle_id, **changes):
    payload = load("agent-review-result.example.json")
    payload["status"] = status
    payload["reviewed_planning_round"] = round_value
    payload["reviewed_evidence_bundle_id"] = bundle_id
    payload.update(changes)
    return payload


def insufficient_review(round_value, bundle_id):
    return review_for(
        "insufficient_evidence",
        round_value,
        bundle_id,
        issues=[],
        revision_instructions=[],
        missing_evidence=[{
            "request_id": "request:diagnosis-refresh",
            "kind": "diagnosis",
            "reason": "需要重新确认诊断指标",
            "required_json_pointers": ["/diagnosis/data/metrics"],
            "facility_types": ["market"],
        }],
    )


def revision_review(round_value, bundle_id):
    return review_for(
        "revision_required",
        round_value,
        bundle_id,
        issues=[],
        revision_instructions=["收窄建议范围"],
        missing_evidence=[],
    )


class FakeGateway:
    """Offline ToolGateway stub that delegates to the Mock exchange table.

    The bundled Mock only serves the ``contracts/v1`` address-based Location
    request, so coordinate Briefs are answered by the same deterministic center.
    """

    def __init__(self, *, poi_result=None, diagnosis_result=None):
        self._mock = MockToolGateway()
        self._poi_result = poi_result
        self._diagnosis_result = diagnosis_result
        self.calls = []

    def resolve_location(self, request):
        self.calls.append("resolve_location")
        if isinstance(request.input, CoordinateInput):
            return LocationResult.model_validate({
                "ok": True,
                "data": {
                    "center": {
                        "lng": request.input.lng,
                        "lat": request.input.lat,
                        "crs": request.input.crs,
                    },
                    "label": None,
                    "formatted_address": None,
                    "source": "input_coordinate",
                },
                "warnings": [],
                "meta": {"schema_version": "1.0", "provider": None},
            })
        return self._mock.resolve_location(request)

    def search_pois(self, request):
        self.calls.append("search_pois")
        if self._poi_result is not None:
            return POISearchResult.model_validate(self._poi_result)
        return self._mock.search_pois(request)

    def calculate_walking_times(self, request):
        return self._mock.calculate_walking_times(request)

    def generate_isochrone(self, request):
        return self._mock.generate_isochrone(request)

    def detect_blindspots(self, request):
        return self._mock.detect_blindspots(request)

    def diagnose_community(self, request):
        self.calls.append("diagnose_community")
        if self._diagnosis_result is not None:
            from app.schemas.diagnosis import DiagnosisResult

            return DiagnosisResult.model_validate(self._diagnosis_result)
        return self._mock.diagnose_community(request)


def run(
    gateway,
    *,
    extraction_output=None,
    proposal=None,
    review=None,
    callables=None,
    run_id="run:test-0001",
):
    llm = ControlledLLM(
        extraction=extraction_output, proposal=proposal, review=review, callables=callables
    )
    state = run_agent(
        run_id=run_id,
        user_message="请体检该社区并提出规划建议",
        orchestrator=OrchestratorAgent(llm),
        gateway=gateway,
        planning_agent=PlanningAgent(llm),
        reviewer_agent=ReviewerAgent(llm),
    )
    return state, llm


def planning_extraction():
    return extraction(
        intent="planning_analysis", facility_types=[], use_standard_facilities=True
    )


def planning_brief_json():
    payload = load("agent-brief.example.json")
    payload["location"] = {"type": "coordinate", **CENTER}
    return payload


def payload(message):
    return json.loads(message)


def proposal_from_message(message):
    """Build a proposal that matches the round and bundle id in the prompt."""
    data = payload(message)
    return proposal_for(data["planning_round"], data["evidence"]["bundle_id"])


def review_from_message(message, status):
    data = payload(message)
    proposal = data["proposal"]
    bundle_id = data["evidence"]["bundle_id"]
    if status == "approved":
        return review_for(status, proposal["planning_round"], bundle_id)
    if status == "revision_required":
        return revision_review(proposal["planning_round"], bundle_id)
    return insufficient_review(proposal["planning_round"], bundle_id)


def test_missing_location_waits_for_input_without_tool_calls():
    gateway = FakeGateway()
    state, llm = run(gateway, extraction_output=extraction(location=None))
    assert state.status.value == "waiting_for_input"
    assert state.completion_mode is None
    assert gateway.calls == []
    assert state.brief.missing_information[0].field.value == "location"
    assert [role for role, _ in llm.calls] == ["orchestrator"]


def test_facility_query_finalizes_with_poi_evidence():
    gateway = FakeGateway(poi_result=market_poi_result())
    state, llm = run(gateway, extraction_output=extraction())
    assert state.status.value == "completed"
    assert state.completion_mode.value == "normal"
    assert state.evidence.poi is not None
    assert state.evidence.location is not None
    assert state.planning_round == 0
    assert gateway.calls == ["resolve_location", "search_pois"]
    assert {ref.evidence_id for ref in state.evidence.refs} == {
        "location:center",
        "poi:facility-0",
    }
    assert [role for role, _ in llm.calls] == ["orchestrator"]


def test_planning_analysis_runs_diagnosis_plan_and_review():
    bundle_id = "evidence:test-0001-d1"
    gateway = FakeGateway()
    state, llm = run(
        gateway,
        extraction_output=planning_extraction(),
        proposal=proposal_for(1, bundle_id),
        review=review_for("approved", 1, bundle_id),
    )
    assert state.status.value == "completed"
    assert state.completion_mode.value == "normal"
    assert state.planning_round == 1
    assert state.evidence.diagnosis is not None
    assert gateway.calls == ["resolve_location", "diagnose_community"]
    assert [role for role, _ in llm.calls] == ["orchestrator", "planning", "reviewer"]
    assert {ref.kind.value for ref in state.evidence.refs} == {"diagnosis"}
    assert state.evidence.diagnosis.root.data.center.lat == CENTER["lat"]


def test_retryable_tool_failure_retries_then_completes():
    attempts = {"count": 0}

    class FlakyGateway(FakeGateway):
        def resolve_location(self, request):
            attempts["count"] += 1
            if attempts["count"] == 1:
                self.calls.append("resolve_location")
                raise RuntimeError("mock transport failure")
            return super().resolve_location(request)

    state, _ = run(
        FlakyGateway(poi_result=market_poi_result()), extraction_output=extraction()
    )
    assert state.status.value == "completed"
    assert state.tool_retry_count == 1
    assert attempts["count"] == 2


def test_exhausted_tool_retries_fail_the_run():
    class DownGateway(FakeGateway):
        def resolve_location(self, request):
            self.calls.append("resolve_location")
            raise RuntimeError("mock transport failure")

    state, _ = run(DownGateway(), extraction_output=extraction())
    assert state.status.value == "failed"
    assert state.tool_retry_count == 2
    assert state.errors[0].code == "GATEWAY_ERROR"
    assert state.completion_mode is None


def test_non_retryable_poi_failure_fails_without_evidence():
    gateway = FakeGateway(poi_result=failure("poi-search", retryable=False))
    state, _ = run(gateway, extraction_output=extraction())
    assert state.status.value == "failed"
    assert state.evidence is None
    assert state.errors[0].code == "TOOL_FAILED"
    assert "search_pois failed" in state.errors[0].message
    assert gateway.calls == ["resolve_location", "search_pois"]


def test_retryable_poi_failure_is_retried_then_fails():
    gateway = FakeGateway(poi_result=failure("poi-search", retryable=True))
    state, _ = run(gateway, extraction_output=extraction())
    assert state.status.value == "failed"
    assert state.tool_retry_count == 2
    assert gateway.calls == [
        "resolve_location",
        "search_pois",
        "resolve_location",
        "search_pois",
    ]


def test_retryable_diagnosis_failure_recovers_on_retry():
    attempts = {"count": 0}

    class FlakyDiagnosisGateway(FakeGateway):
        def diagnose_community(self, request):
            attempts["count"] += 1
            if attempts["count"] == 1:
                self._diagnosis_result = failure("diagnosis", retryable=True)
            else:
                self._diagnosis_result = None
            return super().diagnose_community(request)

    bundle_id = "evidence:test-0001-d2"
    state, _ = run(
        FlakyDiagnosisGateway(),
        extraction_output=planning_extraction(),
        proposal=proposal_for(1, bundle_id),
        review=review_for("approved", 1, bundle_id),
    )
    assert state.status.value == "completed"
    assert state.tool_retry_count == 1
    assert attempts["count"] == 2
    assert state.evidence.bundle_id == bundle_id


def test_revision_required_advances_the_planning_round_and_can_approve():
    planned = []

    def planning_call(user_message):
        proposal = proposal_from_message(user_message)
        planned.append(proposal["planning_round"])
        return proposal

    def review_call(user_message):
        status = "approved" if payload(user_message)["proposal"]["planning_round"] == 2 else "revision_required"
        return review_from_message(user_message, status)

    state, llm = run(
        FakeGateway(),
        extraction_output=planning_extraction(),
        callables={"planning": planning_call, "reviewer": review_call},
    )
    assert state.status.value == "completed"
    assert state.completion_mode.value == "normal"
    assert planned == [1, 2]
    assert state.planning_round == 2
    assert state.planning_proposal.planning_round == 2
    assert state.review_result.status.value == "approved"
    assert [role for role, _ in llm.calls] == [
        "orchestrator", "planning", "reviewer", "planning", "reviewer",
    ]


def test_planning_round_cap_completes_with_limitations():
    planned = []

    def planning_call(user_message):
        proposal = proposal_from_message(user_message)
        planned.append(proposal["planning_round"])
        return proposal

    def review_call(user_message):
        return review_from_message(user_message, "revision_required")

    state, _ = run(
        FakeGateway(),
        extraction_output=planning_extraction(),
        callables={"planning": planning_call, "reviewer": review_call},
    )
    assert state.status.value == "completed"
    assert state.completion_mode.value == "with_limitations"
    assert state.planning_round == 3
    assert planned == [1, 2, 3]
    assert state.review_result.status.value == "revision_required"


def test_insufficient_evidence_consumes_tool_retry_budget():
    gateway = FakeGateway()

    def planning_call(user_message):
        return proposal_from_message(user_message)

    def review_call(user_message):
        return review_from_message(user_message, "insufficient_evidence")

    state, _ = run(
        gateway,
        extraction_output=planning_extraction(),
        callables={"planning": planning_call, "reviewer": review_call},
    )
    assert state.status.value == "completed"
    assert state.completion_mode.value == "with_limitations"
    assert state.tool_retry_count == 2
    assert state.planning_round == 1
    assert gateway.calls == [
        "resolve_location",
        "diagnose_community",
        "resolve_location",
        "diagnose_community",
        "resolve_location",
        "diagnose_community",
    ]
    assert any(item.code == "INSUFFICIENT_EVIDENCE" for item in state.warnings)
