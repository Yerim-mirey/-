"""Offline tests for the Phase 10 semantic-sampling scorer.

The rubric's verdict is the last Phase 10 exit condition, so a scorer that
cannot actually detect a defect would silently certify a bad proposal. Every
decidable check is therefore tested against a synthetic state that breaks
exactly that check, plus a clean state that must pass all of them.
"""

import json
import socket
from pathlib import Path

import pytest

from app.schemas.agent import AgentRunState
from scripts.sample_planning_quality import (
    build_message,
    score_state,
)


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"
FACILITIES = ["market", "pharmacy", "primary_school"]


def example(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def diagnosis_evidence() -> dict:
    """A diagnosis-backed bundle, which is what a planning Run actually gets.

    The plain `agent-evidence.example.json` carries only a location, so every
    metric-dependent check would be vacuous.
    """
    from app.agent_runtime.runner import _diagnosis_evidence
    from app.schemas.diagnosis import DiagnosisResult

    diagnosis = DiagnosisResult.model_validate(
        example("diagnosis-result.example.json")
    )
    return _diagnosis_evidence("evidence:sample", diagnosis).model_dump(mode="json")


def clean_state() -> AgentRunState:
    """A planning Run whose proposal is grounded, honest and complete."""
    evidence = diagnosis_evidence()
    bundle_id = evidence["bundle_id"]
    evidence_id = evidence["refs"][0]["evidence_id"]
    proposal = example("agent-planning-proposal.example.json")
    proposal["evidence_bundle_id"] = bundle_id
    proposal["planning_round"] = 1
    for item in (*proposal["issues"], *proposal["recommendations"]):
        item["evidence_refs"] = [evidence_id]
    proposal["limitations"] = ["本提案不指定候选点位，也未做情景模拟。"]
    for item in proposal["recommendations"]:
        item["limitations"] = ["本建议不重新计算 GIS。"]
    review = example("agent-review-result.example.json")
    review["reviewed_proposal_id"] = proposal["proposal_id"]
    review["reviewed_planning_round"] = 1
    review["reviewed_evidence_bundle_id"] = bundle_id
    return AgentRunState.model_validate({
        "schema_version": "1.0",
        "run_id": "run:sample-test",
        "status": "completed",
        "completion_mode": "normal",
        "user_message": "请给规划建议",
        "brief": example("agent-brief.example.json"),
        "evidence": evidence,
        "planning_proposal": proposal,
        "review_result": review,
        "planning_round": 1,
        "tool_retry_count": 0,
        "warnings": [],
        "errors": [],
    })


def scored(state: AgentRunState, **kwargs):
    return score_state(
        state,
        lng=kwargs.pop("lng", 121.506123),
        lat=kwargs.pop("lat", 31.282456),
        facilities=kwargs.pop("facilities", FACILITIES),
    )


# --- message construction ---------------------------------------------------


def test_message_names_the_coordinate_system():
    """Measured behaviour: a bare coordinate yields location=None."""
    message = build_message(121.5, 31.2, FACILITIES)

    assert "BD09LL" in message
    assert "121.5,31.2" in message


def test_partial_facility_message_asks_only_for_those_facilities():
    message = build_message(121.5, 31.2, ["market"])

    assert "菜市场" in message
    assert "药店" not in message
    assert "小学" not in message


# --- clean state ------------------------------------------------------------


def test_a_clean_run_passes_every_decidable_check():
    score = scored(clean_state())

    assert score.decidable_failures == []
    assert set(score.checks.values()) <= {"pass", "review"}
    assert score.checks["A8"] == "pass"


# --- A1 intent --------------------------------------------------------------


def test_a_non_planning_intent_fails_a1():
    """A planning sample must actually be recognised as planning."""
    brief = example("agent-brief.example.json")
    brief["intent"] = "facility_query"
    brief["needs_planning"] = False
    brief["needs_review"] = False
    brief["needs_full_diagnosis"] = False
    state = clean_state()
    object.__setattr__(state, "brief", type(state.brief).model_validate(brief))

    score = scored(state)

    assert score.checks["A1"] == "fail"
    assert any("intent" in note for note in score.notes)


# --- A2 facilities ----------------------------------------------------------


def test_a_missing_requested_facility_fails_a2():
    state = clean_state()
    object.__setattr__(state.brief, "facility_types", [state.brief.facility_types[0]])

    score = scored(state)

    assert score.checks["A2"] == "fail"
    assert any("facility_types" in note for note in score.notes)


# --- A3 evidence refs -------------------------------------------------------


def test_an_unknown_evidence_reference_fails_a3():
    state = clean_state()
    state.planning_proposal.recommendations[0].evidence_refs = ["diagnosis:nope"]

    score = scored(state)

    assert score.checks["A3"] == "fail"
    assert score.checks["A8"] == "fail"
    assert any("不存在的证据" in note for note in score.notes)


# --- A4 out-of-scope wording ------------------------------------------------


def test_another_facility_in_advice_is_flagged_for_review_not_auto_failed():
    state = clean_state()
    state.planning_proposal.recommendations[0].title = "建议补建社区医院"

    score = scored(state)

    assert score.checks["A4"] == "review"
    assert any("医院" in hit for hit in score.out_of_scope_hits)
    # Flagging for a human must not count as a decided failure.
    assert "A4" not in score.decidable_failures


def test_a_disclaimer_about_other_facilities_is_still_reported_but_marked():
    state = clean_state()
    state.planning_proposal.limitations = ["本提案只讨论菜市场，未涉及医院或地铁。"]

    score = scored(state, facilities=["market"])

    assert score.checks["A4"] == "review"
    assert any("免责声明" in hit for hit in score.out_of_scope_hits)


def test_advice_about_an_excluded_facility_is_flagged_for_a_partial_brief():
    """A partial Brief must not be answered with advice about what it excluded."""
    state = clean_state()
    # The Brief itself must be narrowed; the scorer reads scope from the Brief.
    brief_payload = state.brief.model_dump(mode="json")
    brief_payload["facility_types"] = ["market"]
    object.__setattr__(state, "brief", type(state.brief).model_validate(brief_payload))
    state.planning_proposal.recommendations[0].title = "建议新增小学学位"
    state.planning_proposal.recommendations[0].actions = ["协调小学用地"]

    score = scored(state, facilities=["market"])

    assert score.checks["A4"] == "review"
    assert any("小学" in hit for hit in score.out_of_scope_hits)


def test_in_scope_facility_mentions_are_not_flagged():
    state = clean_state()
    state.planning_proposal.recommendations[0].title = "优先核查小学盲区网格"

    score = scored(state, facilities=["primary_school"])

    assert score.checks["A4"] == "pass"
    assert score.out_of_scope_hits == []


# --- A5 numbers -------------------------------------------------------------


def test_an_invented_percentage_fails_a5():
    state = clean_state()
    state.planning_proposal.summary = "该区域覆盖率为 87.5%，明显偏低。"

    score = scored(state)

    assert score.checks["A5"] == "fail"
    assert any("87.5" in note or "0.875" in note for note in score.notes)


def test_a_reported_ratio_does_not_fail_a5():
    """A figure the run actually reported must pass, including its complement."""
    state = clean_state()
    metric = state.evidence.diagnosis.root.data.metrics[0]
    state.planning_proposal.summary = (
        f"覆盖率 {metric.covered_ratio:.4f}，盲区 {metric.blind_ratio:.4f}。"
    )

    score = scored(state)

    assert score.checks["A5"] == "pass"


def test_a_large_facility_count_is_not_mistaken_for_a_ratio():
    state = clean_state()
    state.planning_proposal.summary = "共检索到 35 个菜市场，最近距离 381 米。"

    score = scored(state)

    assert score.checks["A5"] == "pass"


def test_a_truthful_all_facility_blind_ratio_floor_passes_a5_and_a8():
    """A lower bound is valid when every cited blind ratio clears it."""
    state = clean_state()
    blind_ratios = (0.963167, 0.912205, 0.915672)
    for metric, blind_ratio in zip(
        state.evidence.diagnosis.root.data.metrics, blind_ratios, strict=True
    ):
        object.__setattr__(metric, "blind_ratio", blind_ratio)
        object.__setattr__(metric, "covered_ratio", 1 - blind_ratio)
        object.__setattr__(metric, "unknown_ratio", 0.0)
    state.planning_proposal.summary = "三类设施的盲区比例均在 90% 以上。"

    score = scored(state)

    assert score.checks["A5"] == "pass"
    assert score.checks["A8"] == "pass"


def test_an_all_facility_floor_fails_if_one_blind_ratio_is_below_it():
    state = clean_state()
    blind_ratios = (0.90, 0.89, 0.92)
    for metric, blind_ratio in zip(
        state.evidence.diagnosis.root.data.metrics, blind_ratios, strict=True
    ):
        object.__setattr__(metric, "blind_ratio", blind_ratio)
        object.__setattr__(metric, "covered_ratio", 1 - blind_ratio)
        object.__setattr__(metric, "unknown_ratio", 0.0)
    state.planning_proposal.summary = "三类设施的盲区比例均在 90% 以上。"

    score = scored(state)

    assert score.checks["A5"] == "fail"
    assert score.checks["A8"] == "fail"


# --- A6 unknown respect -----------------------------------------------------


def test_unknown_cells_without_any_caveat_fail_a6():
    """unknown_ratio > 0 forbids calling a zero blind ratio settled."""
    state = clean_state()
    metrics = state.evidence.diagnosis.root.data.metrics
    object.__setattr__(metrics[0], "unknown_ratio", 0.2)
    state.planning_proposal.summary = "菜市场不存在盲区，无需处理。"
    state.planning_proposal.limitations = []
    for item in state.planning_proposal.recommendations:
        item.limitations = []
        item.rationale = "覆盖良好。"

    score = scored(state)

    assert score.checks["A6"] == "fail"
    assert score.checks["A8"] == "fail"
    assert any("未知" in note for note in score.notes)


def test_mentioning_unknown_cells_satisfies_a6():
    state = clean_state()
    object.__setattr__(
        state.evidence.diagnosis.root.data.metrics[0], "unknown_ratio", 0.2
    )
    state.planning_proposal.summary = "存在约 20% 未知网格，不能据此判定为已覆盖。"

    score = scored(state)

    assert score.checks["A6"] == "pass"


def test_a6_is_not_applicable_when_nothing_is_unknown():
    state = clean_state()
    for metric in state.evidence.diagnosis.root.data.metrics:
        object.__setattr__(metric, "unknown_ratio", 0.0)
    state.planning_proposal.summary = "三类设施均无盲区。"

    score = scored(state)

    assert score.checks["A6"] == "pass"


# --- A7 limitations ---------------------------------------------------------


def test_low_confidence_without_a_caveat_fails_a7():
    state = clean_state()
    data = state.evidence.diagnosis.root.data
    payload = data.model_dump(mode="json")
    payload["quality"]["confidence"] = "low"
    object.__setattr__(data, "quality",
                       type(data.quality).model_validate(payload["quality"]))
    state.planning_proposal.limitations = []
    for item in state.planning_proposal.recommendations:
        item.limitations = []

    score = scored(state)

    assert score.checks["A7"] == "fail"
    assert any("limitations" in note for note in score.notes)


def test_partial_evidence_without_a_caveat_fails_a7():
    """Isolated from the confidence caveat so the partial code is what fails."""
    state = clean_state()
    data = state.evidence.diagnosis.root.data
    payload = data.model_dump(mode="json")
    payload["quality"]["confidence"] = "high"
    object.__setattr__(data, "quality",
                       type(data.quality).model_validate(payload["quality"]))
    from app.schemas.common import WarningItem

    state.warnings.append(
        WarningItem(code="PARTIAL_DIAGNOSIS", message="部分设施数据不完整。")
    )
    state.planning_proposal.limitations = []
    for item in state.planning_proposal.recommendations:
        item.limitations = []

    score = scored(state)

    assert score.checks["A7"] == "fail"


def test_partial_walking_routes_declared_unavailable_pass_a7():
    """S3's actual caveat explains which data are partial and how."""
    state = clean_state()
    from app.schemas.common import WarningItem

    state.warnings.append(
        WarningItem(code="PARTIAL_ROUTING_RESULTS", message="采样路线部分不可用。")
    )
    state.planning_proposal.limitations = [
        "存在部分目标点与部分网格步行路线不可用的警告，个别单元可达性判断可能受影响。"
    ]

    score = scored(state)

    assert score.checks["A7"] == "pass"


def test_unrelated_unavailable_item_does_not_cover_partial_routes():
    state = clean_state()
    from app.schemas.common import WarningItem

    state.warnings.append(
        WarningItem(code="PARTIAL_ROUTING_RESULTS", message="采样路线部分不可用。")
    )
    state.planning_proposal.limitations = ["步行路线已核查，部分设备不可用。"]

    score = scored(state)

    assert score.checks["A7"] == "fail"


# --- A8 reviewer verdict ----------------------------------------------------


def test_a_reviewer_approving_a_defective_proposal_fails_a8():
    """The Reviewer must not wave through a proposal with a decided defect."""
    state = clean_state()
    state.planning_proposal.summary = "覆盖率 91.3%，一切正常。"
    assert state.review_result.status.value == "approved"

    score = scored(state)

    assert score.checks["A5"] == "fail"
    assert score.checks["A8"] == "fail"
    assert any("放行" in note for note in score.notes)


def test_a_reviewer_rejecting_a_defective_proposal_still_passes_a8():
    state = clean_state()
    state.planning_proposal.summary = "覆盖率 91.3%，一切正常。"
    review_payload = state.review_result.model_dump(mode="json")
    review_payload["status"] = "revision_required"
    # The schema requires a rejected review to state what must change.
    review_payload["revision_instructions"] = ["请修正覆盖率数字。"]
    object.__setattr__(
        state, "review_result",
        type(state.review_result).model_validate(review_payload),
    )

    score = scored(state)

    assert score.checks["A5"] == "fail"
    assert score.checks["A8"] == "pass"


# --- standalone live probe setup (with offline transport) ------------------


def test_standalone_route_probe_uses_ipv4_before_routing(monkeypatch, capsys):
    """The probe must observe the same address family as production routing."""
    from app.providers.baidu import net
    from app.providers.baidu.routing import BaiduRoutingProvider, ProviderRoute
    from scripts.verify_routing_live import main as routing_probe_main

    addresses = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]
    observed_families = []

    def offline_dns(*args, **kwargs):
        return addresses

    def offline_route(self, origin, destinations):
        observed_families.extend(
            item[0] for item in socket.getaddrinfo("offline.invalid", 443)
        )
        return [ProviderRoute(distance_m=800, duration_s=600) for _ in destinations]

    monkeypatch.setenv("BAIDU_MAP_AK", "offline-test-key")
    monkeypatch.setenv("BAIDU_FORCE_IPV4", "1")
    monkeypatch.setattr(net, "_original_getaddrinfo", offline_dns)
    monkeypatch.setattr(socket, "getaddrinfo", offline_dns)
    monkeypatch.setattr(BaiduRoutingProvider, "route_matrix", offline_route)

    routing_probe_main()

    assert observed_families == [socket.AF_INET]
    assert "ok: True" in capsys.readouterr().out


# --- incomplete run ---------------------------------------------------------


def test_a_run_without_planning_outputs_fails_the_downstream_checks():
    state = clean_state()
    object.__setattr__(state, "planning_proposal", None)
    object.__setattr__(state, "review_result", None)

    score = scored(state)

    assert score.decidable_failures
    assert any("规划路径未完成" in note for note in score.notes)


def test_a_run_without_a_brief_fails_a1():
    state = AgentRunState.model_validate({
        "schema_version": "1.0",
        "run_id": "run:no-brief",
        "status": "failed",
        "completion_mode": None,
        "user_message": "查一下",
        "brief": None,
        "evidence": None,
        "planning_proposal": None,
        "review_result": None,
        "planning_round": 0,
        "tool_retry_count": 0,
        "warnings": [],
        "errors": [{"code": "UNEXPECTED_ERROR", "message": "no brief", "retryable": False}],
    })

    score = scored(state)

    assert score.checks["A1"] == "fail"
    assert any("没有 Brief" in note for note in score.notes)
