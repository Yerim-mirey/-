"""Phase 3 Orchestrator behavior with a controlled structured model."""

import socket

import pytest

from app.agents.orchestrator import OrchestratorAgent, OrchestratorOutputError
from app.schemas.agent import AgentIntent, BriefMissingField, LifeCircleBrief


ADDRESS = {"type": "address", "query": "上海市虹口区四川北路街道", "city": "上海市"}


class RecordingLLM:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = []

    def generate_object(self, *, system_prompt, user_message, response_schema):
        self.calls.append((system_prompt, user_message, response_schema))
        if self.error is not None:
            raise self.error
        return self.output


def extraction(intent="planning_analysis", location=ADDRESS, facility_types=None, use_standard_facilities=False):
    return {
        "intent": intent,
        "user_goal": "体检社区并提出设施改善建议",
        "location": location,
        "facility_types": ["market", "pharmacy", "primary_school"] if facility_types is None else facility_types,
        "use_standard_facilities": use_standard_facilities,
    }


def test_orchestrator_returns_valid_brief_and_sends_only_semantic_schema():
    llm = RecordingLLM(extraction())
    brief = OrchestratorAgent(llm).create_brief("  请体检我的社区并提出建议  ")
    assert isinstance(brief, LifeCircleBrief)
    assert brief.schema_version == "1.0"
    assert brief.intent is AgentIntent.PLANNING_ANALYSIS
    assert (brief.needs_full_diagnosis, brief.needs_planning, brief.needs_review) == (True, True, True)
    assert brief.location.query == ADDRESS["query"]
    assert brief.missing_information == []
    assert len(llm.calls) == 1
    system_prompt, user_message, schema = llm.calls[0]
    assert system_prompt
    assert user_message == "请体检我的社区并提出建议"
    assert set(schema["properties"]) == {"intent", "user_goal", "location", "facility_types", "use_standard_facilities"}


@pytest.mark.parametrize(
    ("intent", "flags"),
    [
        ("facility_query", (False, False, False)),
        ("accessibility_query", (False, False, False)),
        ("blindspot_query", (True, False, False)),
        ("community_diagnosis", (True, False, False)),
        ("planning_analysis", (True, True, True)),
    ],
)
def test_intent_determines_flags_in_python(intent, flags):
    brief = OrchestratorAgent(RecordingLLM(extraction(intent=intent))).create_brief("查询社区设施")
    assert (brief.needs_full_diagnosis, brief.needs_planning, brief.needs_review) == flags


@pytest.mark.parametrize("intent", ["community_diagnosis", "planning_analysis"])
def test_broad_request_uses_core_diagnosis_facility_defaults(intent):
    brief = OrchestratorAgent(RecordingLLM(extraction(intent=intent, facility_types=[], use_standard_facilities=True))).create_brief("体检社区")
    assert [item.value for item in brief.facility_types] == ["market", "pharmacy", "primary_school"]
    assert brief.missing_information == []


@pytest.mark.parametrize("intent", ["facility_query", "accessibility_query", "blindspot_query"])
def test_focused_query_without_facility_type_requests_input(intent):
    brief = OrchestratorAgent(RecordingLLM(extraction(intent=intent, facility_types=[]))).create_brief("查一下附近设施")
    assert brief.facility_types == []
    assert [item.field for item in brief.missing_information] == [BriefMissingField.FACILITY_TYPES]


def test_missing_location_is_reported_without_geocoding():
    brief = OrchestratorAgent(RecordingLLM(extraction(location=None))).create_brief("帮我规划设施")
    assert brief.location is None
    assert [item.field for item in brief.missing_information] == [BriefMissingField.LOCATION]


def test_duplicate_facilities_are_removed_in_first_seen_order():
    brief = OrchestratorAgent(RecordingLLM(extraction(facility_types=["pharmacy", "market", "pharmacy"]))).create_brief("查药店和菜场")
    assert [item.value for item in brief.facility_types] == ["pharmacy", "market"]


def test_blank_message_is_rejected_before_model_call():
    llm = RecordingLLM(extraction())
    with pytest.raises(ValueError, match="user_message"):
        OrchestratorAgent(llm).create_brief(" \n ")
    assert llm.calls == []


@pytest.mark.parametrize(
    "bad_output",
    [
        {"user_goal": "缺少意图", "location": ADDRESS, "facility_types": ["market"]},
        extraction(facility_types=["hospital"]),
        {**extraction(), "needs_review": False},
        extraction(location={"type": "coordinate", "lng": 121.5, "lat": 31.2}),
        {**extraction(), "user_goal": "   "},
    ],
)
def test_invalid_model_output_fails_without_echoing_payload(bad_output):
    with pytest.raises(OrchestratorOutputError) as caught:
        OrchestratorAgent(RecordingLLM(bad_output)).create_brief("用户私有地址 12345")
    assert "用户私有地址 12345" not in str(caught.value)
    assert "hospital" not in str(caught.value)


def test_provider_error_is_preserved_for_later_runtime_handling():
    provider_error = TimeoutError("model timeout")
    with pytest.raises(TimeoutError) as caught:
        OrchestratorAgent(RecordingLLM(error=provider_error)).create_brief("体检社区")
    assert caught.value is provider_error


def test_orchestrator_does_not_open_network_connection(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network attempted")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    OrchestratorAgent(RecordingLLM(extraction())).create_brief("体检社区")


@pytest.mark.parametrize("user_message", ["体检社区医院可达性", "体检社区医院和药店可达性"])
def test_broad_request_with_unsupported_named_facility_asks_clarification(user_message):
    payload = extraction(intent="community_diagnosis", facility_types=[])
    payload["use_standard_facilities"] = False
    brief = OrchestratorAgent(RecordingLLM(payload)).create_brief(user_message)
    assert brief.facility_types == []
    assert [item.field for item in brief.missing_information] == [BriefMissingField.FACILITY_TYPES]


def test_model_must_explicitly_select_standard_facility_scope():
    payload = extraction(intent="community_diagnosis", facility_types=[])
    payload["use_standard_facilities"] = True
    brief = OrchestratorAgent(RecordingLLM(payload)).create_brief("做完整社区体检")
    assert [item.value for item in brief.facility_types] == ["market", "pharmacy", "primary_school"]


@pytest.mark.parametrize(
    ("intent", "facility_types"),
    [
        ("facility_query", []),
        ("planning_analysis", ["market"]),
    ],
)
def test_inconsistent_standard_scope_is_rejected(intent, facility_types):
    payload = extraction(intent=intent, facility_types=facility_types, use_standard_facilities=True)
    with pytest.raises(OrchestratorOutputError):
        OrchestratorAgent(RecordingLLM(payload)).create_brief("查询设施")
