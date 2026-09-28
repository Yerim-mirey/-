"""Offline semantic extraction tests for the catalog-backed V2 brief."""

import pytest

from app.agents.orchestrator_v2 import OrchestratorAgentV2, OrchestratorOutputErrorV2
from app.modules.poi.facility_catalog import load_facility_catalog


ADDRESS = {"type": "address", "query": "武汉市武昌区中南路", "city": "武汉市"}


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


def extraction(**overrides):
    value = {
        "intent": "community_diagnosis",
        "user_goal": "诊断社区设施供给",
        "location": ADDRESS,
        "facility_mentions": ["菜市场"],
        "exploratory_terms": [],
        "unresolved_mentions": [],
        "use_standard_facilities": False,
    }
    value.update(overrides)
    return value


def test_alias_mentions_are_resolved_deterministically_to_catalog_ids():
    llm = RecordingLLM(extraction(facility_mentions=["药房", "菜市场", "药店"]))
    brief = OrchestratorAgentV2(llm).create_brief("请诊断武昌中南路附近的药房和菜市场")
    assert brief.facility_ids == ["pharmacy", "market"]
    assert brief.unresolved_mentions == []
    assert brief.exploratory_terms == []
    assert brief.needs_diagnosis is True
    assert brief.needs_planning is False
    assert llm.calls[0][1] == "请诊断武昌中南路附近的药房和菜市场"


def test_broad_standard_scope_selects_all_catalog_entries_and_reports_capability_limits():
    catalog = load_facility_catalog()
    expected = [item.id for item in catalog.facilities]
    brief = OrchestratorAgentV2(RecordingLLM(extraction(
        intent="planning_analysis",
        facility_mentions=[],
        use_standard_facilities=True,
    ))).create_brief("请对这个社区做生活圈全面诊断并提出规划建议")
    assert brief.facility_ids == expected
    assert brief.needs_diagnosis is True
    assert brief.needs_diagnosis is any("diagnosis" in catalog.get(item).capabilities for item in expected)
    assert brief.needs_planning is any("planning" in catalog.get(item).capabilities for item in expected)
    assert brief.needs_review is brief.needs_planning
    assert brief.missing_information == []
    limitation = next(item for item in brief.capability_limitations if item.facility_id == "kindergarten")
    assert limitation.unsupported_capabilities == ["diagnosis", "planning"]
    assert limitation.message


def test_broad_facility_discovery_returns_catalog_without_triggering_diagnosis():
    catalog = load_facility_catalog()
    brief = OrchestratorAgentV2(RecordingLLM(extraction(
        intent="facility_query",
        user_goal="查询附近各类生活服务设施",
        facility_mentions=[],
        use_standard_facilities=True,
    ))).create_brief("列出附近各种生活服务设施")
    assert brief.facility_ids == [item.id for item in catalog.facilities]
    assert brief.needs_diagnosis is False
    assert brief.needs_planning is False
    assert brief.missing_information == []


def test_catalog_facility_without_diagnosis_capability_cannot_enter_diagnosis_or_planning():
    brief = OrchestratorAgentV2(RecordingLLM(extraction(
        intent="planning_analysis",
        facility_mentions=["宠物医院"],
        exploratory_terms=["宠物医院"],
    ))).create_brief("请评估附近宠物医院并提出规划建议")
    assert brief.facility_ids == []
    assert brief.exploratory_terms == ["宠物医院"]
    assert brief.needs_diagnosis is False
    assert brief.needs_planning is False
    assert brief.needs_review is False
    assert brief.exploratory_only is True


def test_named_catalog_facility_without_diagnosis_exposes_capability_limit():
    brief = OrchestratorAgentV2(RecordingLLM(extraction(
        intent="community_diagnosis",
        facility_mentions=["幼儿园"],
    ))).create_brief("请诊断附近幼儿园供给")
    assert brief.facility_ids == ["kindergarten"]
    assert brief.needs_diagnosis is False
    assert brief.needs_planning is False
    assert len(brief.capability_limitations) == 1
    limit = brief.capability_limitations[0]
    assert limit.facility_id == "kindergarten"
    assert limit.unsupported_capabilities == ["diagnosis", "planning"]
    assert "诊断" in limit.message
    assert "规划" in limit.message


def test_out_of_catalog_explicit_facility_is_separate_exploration_and_not_planning_input():
    brief = OrchestratorAgentV2(RecordingLLM(extraction(
        intent="facility_query",
        facility_mentions=[],
        exploratory_terms=["宠物医院"],
    ))).create_brief("查一下附近的宠物医院")
    assert brief.facility_ids == []
    assert brief.exploratory_terms == ["宠物医院"]
    assert brief.exploratory_only is True
    assert brief.needs_diagnosis is False
    assert brief.needs_planning is False
    assert brief.missing_information == []


def test_unknown_general_theme_asks_for_facility_clarification():
    brief = OrchestratorAgentV2(RecordingLLM(extraction(
        intent="community_diagnosis",
        user_goal="提升生活品质",
        facility_mentions=[],
        exploratory_terms=[],
        unresolved_mentions=["生活品质"],
    ))).create_brief("请帮我提升生活品质")
    assert brief.facility_ids == []
    assert brief.exploratory_terms == []
    assert brief.needs_diagnosis is False
    assert brief.needs_planning is False
    assert [item.field for item in brief.missing_information] == ["facility_ids"]


def test_unresolved_request_part_prevents_silent_diagnosis_of_only_recognized_part():
    brief = OrchestratorAgentV2(RecordingLLM(extraction(
        facility_mentions=["药店", "未明确的服务"],
        unresolved_mentions=["未明确的服务"],
    ))).create_brief("请诊断药店和未明确的服务")
    assert brief.facility_ids == ["pharmacy"]
    assert brief.unresolved_mentions == ["未明确的服务"]
    assert brief.needs_diagnosis is False
    assert brief.needs_planning is False
    assert [item.field for item in brief.missing_information] == ["facility_ids"]


def test_model_cannot_inject_unknown_canonical_ids():
    payload = extraction()
    payload["facility_ids"] = ["invented_facility"]
    with pytest.raises(OrchestratorOutputErrorV2):
        OrchestratorAgentV2(RecordingLLM(payload)).create_brief("请诊断社区设施")


def test_model_cannot_mark_an_unknown_general_mention_as_exploratory_without_explicit_type():
    with pytest.raises(OrchestratorOutputErrorV2):
        OrchestratorAgentV2(RecordingLLM(extraction(
            facility_mentions=[],
            exploratory_terms=["生活品质"],
        ))).create_brief("提升生活品质")


def test_broad_scope_accepts_explicitly_named_focus_without_losing_other_categories():
    catalog = load_facility_catalog()
    brief = OrchestratorAgentV2(RecordingLLM(extraction(
        facility_mentions=["药店"],
        use_standard_facilities=True,
    ))).create_brief("做全面体检，重点看药店")

    assert brief.facility_ids == [item.id for item in catalog.facilities]
    assert brief.missing_information == []


@pytest.mark.parametrize("user_message", [
    "只诊断附近药店",
    "请全面诊断附近药店",
    "请全面诊断附近药店，重点看药店",
])
def test_model_cannot_expand_a_named_only_request_to_the_full_catalog(user_message):
    with pytest.raises(OrchestratorOutputErrorV2):
        OrchestratorAgentV2(RecordingLLM(extraction(
            facility_mentions=["药店"],
            use_standard_facilities=True,
        ))).create_brief(user_message)


def test_invalid_output_does_not_echo_user_message_or_model_payload():
    private_text = "隐私地址XYZ"
    with pytest.raises(OrchestratorOutputErrorV2) as caught:
        OrchestratorAgentV2(RecordingLLM({"oops": private_text})).create_brief(private_text)
    assert private_text not in str(caught.value)
