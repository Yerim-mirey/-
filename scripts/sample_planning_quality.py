"""Collect and mechanically score one Phase 10 semantic-sampling case.

The rubric (`docs/test-report/phase10-semantic-sampling-rubric.md`) fixes eight
checks. Seven of them can be decided from the run state alone, so this script
computes them instead of leaving them to a subjective read; the remaining one
(A4, whether a recommendation reaches outside the Brief's facility scope) needs
judgement and is reported as the raw material a reviewer must inspect.

Scoring the decidable checks here is deliberate: it keeps every sample measured
the same way and makes a `fail` reproducible. It does **not** replace human
review — the rubric says so, and this tool cannot judge whether advice is
sensible in planning terms.

Usage (spends real Baidu quota and real model calls)::

    set -a; source .env; set +a
    RUN_BAIDU_SMOKE=1 RUN_DEEPSEEK_SMOKE=1 python -m scripts.sample_planning_quality \
        --lng 121.506123 --lat 31.282456 --facilities market,pharmacy,primary_school
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agent_runtime.runner import run_agent
from app.agent_tools.mvp_gateway import MVPToolGateway
from app.agents.orchestrator import OrchestratorAgent
from app.agents.planning import PlanningAgent
from app.agents.reviewer import ReviewerAgent
from app.providers.deepseek import DeepSeekLLM, api_key_from_env
from app.schemas.agent import AgentRunState, RunStatus

FACILITY_LABELS = {
    "market": "菜市场",
    "pharmacy": "药店",
    "primary_school": "小学",
}

# Warning codes that mean the evidence handed to the Planner was incomplete.
INCOMPLETE_CODES = {
    "PARTIAL_DIAGNOSIS",
    "PARTIAL_POI_RESULTS",
    "PARTIAL_ROUTING_RESULTS",
    "BLINDSPOT_UNAVAILABLE",
    "UNKNOWN_BLINDSPOT_AREA",
    "LOW_CONFIDENCE",
}

# Facility vocabulary the prompt knows about. The Brief may legitimately name an
# out-of-scope facility inside a disclaimer (for example "本提案只涉及菜市场、
# 药店、小学三类，未涉及其他设施"), so a hit is reported with its sentence for a
# human to read rather than being failed automatically.
FACILITY_WORDS = (
    "菜市场", "菜场", "药店", "小学",
    "医院", "诊所", "卫生院", "社区卫生",
    "幼儿园", "中学", "初中", "高中", "大学",
    "超市", "便利店", "生鲜",
    "公园", "绿地", "体育", "运动场",
    "公交", "地铁", "车站", "停车",
    "养老", "托育", "食堂", "图书馆",
)

# Markers that make a sentence a scope disclaimer rather than a violation.
NEGATION_MARKERS = (
    "未涉及", "不涉及", "不包含", "不讨论", "只讨论",
    "只涉及", "仅涉及", "不含", "之外",
)

# In-scope synonyms, so an in-scope mention is never flagged.
IN_SCOPE_WORDS = {
    "market": ("菜市场", "菜场", "市场"),
    "pharmacy": ("药店", "药房"),
    "primary_school": ("小学",),
}


def build_message(lng: float, lat: float, facilities: list[str]) -> str:
    names = "、".join(FACILITY_LABELS.get(item, item) for item in facilities)
    if len(facilities) >= 3:
        return (
            f"我的位置是 BD09LL 坐标 {lng},{lat}，"
            f"{names}的配置是否合理？请给出改善规划建议"
        )
    return (
        f"我的位置是 BD09LL 坐标 {lng},{lat}，"
        f"只想了解{names}的配置并请给出改善建议"
    )


@dataclass
class Score:
    """The rubric outcome for one sample."""

    run_id: str
    lng: float
    lat: float
    facilities: list[str]
    status: str = ""
    completion_mode: str | None = None
    confidence: str | None = None
    unknown_ratios: dict[str, float] = field(default_factory=dict)
    metric_ratios: list[float] = field(default_factory=list)
    route_targets: int = 0
    seconds: float = 0.0
    warning_codes: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # Raw material the rubric leaves to a human reader.
    proposal_summary: str = ""
    limitations: list[str] = field(default_factory=list)
    recommendation_titles: list[str] = field(default_factory=list)
    recommendation_actions: list[list[str]] = field(default_factory=list)
    review_status: str | None = None
    review_issue_messages: list[str] = field(default_factory=list)
    out_of_scope_hits: list[str] = field(default_factory=list)

    @property
    def decidable_failures(self) -> list[str]:
        """Checks this tool can decide that did not pass."""
        return [name for name, value in self.checks.items() if value == "fail"]


def _has_any(text: str, words: tuple[str, ...]) -> list[str]:
    return [word for word in words if word in text]


def _sentences(text: str) -> list[str]:
    import re

    return [part.strip() for part in re.split(r"[。；;\n]", text) if part.strip()]


def _out_of_scope_mentions(proposal, brief) -> list[str]:
    """Facility names the Brief did not ask for, with the sentence that used them.

    Scope-relative rather than a fixed blacklist: a partial-facility Brief must
    not be met with advice about the facilities it excluded. Disclaimers are
    still reported, but with their wording, because only a reader can tell
    "未涉及" apart from an actual recommendation.
    """
    allowed = {
        word
        for facility in brief.facility_types
        for word in IN_SCOPE_WORDS.get(facility.value, ())
    }
    text = " ".join(
        [proposal.summary, *score_limitations(proposal)]
        + [item.title for item in proposal.recommendations]
        + [item.rationale for item in proposal.recommendations]
        + [action for item in proposal.recommendations for action in item.actions]
    )
    hits: list[str] = []
    for sentence in _sentences(text):
        for word in FACILITY_WORDS:
            if word in sentence and word not in allowed:
                marker = "（免责声明）" if _has_any(sentence, NEGATION_MARKERS) else ""
                hits.append(f"{word}{marker}: {sentence[:120]}")
    return hits


def score_state(
    state: AgentRunState, *, lng: float, lat: float, facilities: list[str]
) -> Score:
    """Apply the rubric's decidable checks to one finished Run."""
    score = Score(run_id=state.run_id, lng=lng, lat=lat, facilities=facilities)
    score.status = state.status.value
    score.completion_mode = state.completion_mode.value if state.completion_mode else None
    score.warning_codes = sorted({item.code for item in state.warnings})
    score.checks = {name: "n/a" for name in
                    ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8")}

    brief = state.brief
    if brief is None:
        score.notes.append("Run 没有 Brief，无法评分")
        score.checks["A1"] = "fail"
        return score

    # A1 intent recognition
    score.checks["A1"] = "pass" if brief.intent.value == "planning_analysis" else "fail"
    if score.checks["A1"] == "fail":
        score.notes.append(f"intent={brief.intent.value}，期望 planning_analysis")

    # A2 facility classification
    asked = sorted(item.value for item in brief.facility_types)
    score.checks["A2"] = "pass" if asked == sorted(facilities) else "fail"
    if score.checks["A2"] == "fail":
        score.notes.append(f"facility_types={asked}，请求={sorted(facilities)}")

    bundle = state.evidence
    proposal = state.planning_proposal
    review = state.review_result
    if bundle is None or proposal is None or review is None:
        score.notes.append("缺少 Evidence/Proposal/Review，规划路径未完成")
        for name in ("A3", "A5", "A6", "A7", "A8"):
            score.checks[name] = "fail"
        return score

    diagnosis = bundle.diagnosis
    if diagnosis is not None and diagnosis.root.ok:
        data = diagnosis.root.data
        score.confidence = data.quality.confidence.value
        score.unknown_ratios = {
            metric.facility_type.value: round(metric.unknown_ratio, 4)
            for metric in data.metrics
        }
        score.metric_ratios = sorted(_metric_ratios(data.metrics))

    # A3 every cited id exists and matches the facility it supports
    available = {ref.evidence_id for ref in bundle.refs}
    cited = {ref for item in (*proposal.issues, *proposal.recommendations)
             for ref in item.evidence_refs}
    score.checks["A3"] = "pass" if cited <= available else "fail"
    if score.checks["A3"] == "fail":
        score.notes.append(f"引用了不存在的证据：{sorted(cited - available)}")

    # A4 out-of-scope facilities: surface the evidence, do not auto-fail
    score.out_of_scope_hits = _out_of_scope_mentions(proposal, brief)
    score.checks["A4"] = "review" if score.out_of_scope_hits else "pass"

    # A5 numbers quoted must match the metrics they name
    score.checks["A5"] = "pass" if _numbers_consistent(proposal, score) else "fail"

    # A6 unknown must not be presented as a settled conclusion
    score.checks["A6"] = "pass" if _unknown_is_respected(proposal, score) else "fail"

    # A7 limitations must cover the applicable caveats
    score.checks["A7"] = "pass" if _limitations_cover(proposal, score) else "fail"

    # A8 reviewer verdict must match the proposal's quality
    severe = [name for name in ("A3", "A5", "A6") if score.checks[name] == "fail"]
    approved = review.status.value == "approved"
    score.checks["A8"] = "fail" if (approved and severe) else "pass"
    if score.checks["A8"] == "fail":
        score.notes.append(f"Reviewer 放行了存在 {severe} 缺陷的提案")

    score.proposal_summary = proposal.summary
    score.limitations = list(score_limitations(proposal))
    score.recommendation_titles = [item.title for item in proposal.recommendations]
    score.recommendation_actions = [list(item.actions) for item in proposal.recommendations]
    score.review_status = review.status.value
    score.review_issue_messages = [item.message for item in review.issues]
    return score


def score_limitations(proposal) -> list[str]:
    return [*proposal.limitations, *(
        item for rec in proposal.recommendations for item in rec.limitations
    )]


def _metric_ratios(state_metrics: list) -> set[float]:
    """Every ratio this run reported, plus its complement, to 4 decimals.

    A summary legitimately quotes a coverage figure or its complement, so both
    are accepted; an unlisted figure means the text invented a number.
    """
    values: set[float] = set()
    for metric in state_metrics:
        for raw in (
            metric.covered_ratio,
            metric.blind_ratio,
            metric.unknown_ratio,
        ):
            if raw is None:
                continue
            values.add(round(float(raw), 4))
            values.add(round(1.0 - float(raw), 4))
    return values


def _numbers_consistent(proposal, score: Score) -> bool:
    """Reject a proposal that states a ratio this run never produced.

    Only explicit percentages and 0.xxxx decimals are inspected, so facility
    counts and distances are not misread as ratios.
    """
    allowed = set(score.metric_ratios)
    text = " ".join(
        [proposal.summary]
        + [item.rationale for item in proposal.recommendations]
        + score_limitations(proposal)
    )
    for token in _percent_tokens(text):
        if not any(abs(token - candidate) < 0.005 for candidate in allowed):
            score.notes.append(
                f"正文出现比例 {token:.4f}，但本次指标只报告了 {sorted(allowed)}"
            )
            return False
    return True


def _percent_tokens(text: str) -> list[float]:
    """Decimals or percentages written in the text, as fractions."""
    import re

    found: list[float] = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*%", text):
        found.append(round(float(match.group(1)) / 100.0, 4))
    for match in re.finditer(r"(?<![\d.])0\.\d{2,4}(?![\d])", text):
        found.append(round(float(match.group(0)), 4))
    return found


def _unknown_is_respected(proposal, score: Score) -> bool:
    """With unknown cells present, a zero blind ratio must not be called settled."""
    has_unknown = any(value > 0 for value in score.unknown_ratios.values())
    if not has_unknown:
        return True
    text = " ".join(
        [proposal.summary, *score_limitations(proposal)]
        + [item.rationale for item in proposal.recommendations]
    )
    if not _has_any(text, ("未知", "无法判定", "不能判定", "暂定", "证据不足")):
        score.notes.append("指标存在未知网格，但正文/限制里没有提到未知或无法判定")
        return False
    return True


def _limitations_cover(proposal, score: Score) -> bool:
    """Applicable caveats must appear somewhere in the stated limitations."""
    text = " ".join(score_limitations(proposal))
    required: list[str] = []
    if score.confidence == "low":
        required.append("置信度")
    if INCOMPLETE_CODES & set(score.warning_codes):
        required.append("partial")
    missing = [word for word in required
               if not _has_any(text, (word, _synonym(word)))]
    if missing:
        score.notes.append(f"limitations 未覆盖：{missing}")
        return False
    return True


def _synonym(word: str) -> str:
    return {
        "置信度": "confidence",
        "partial": "不完整",
    }.get(word, word)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lng", type=float, required=True)
    parser.add_argument("--lat", type=float, required=True)
    parser.add_argument(
        "--facilities",
        default="market,pharmacy,primary_school",
        help="逗号分隔；支持 market,pharmacy,primary_school",
    )
    parser.add_argument("--report", default="", help="脱敏评分 JSON 输出路径")
    args = parser.parse_args(argv)

    facilities = [item.strip() for item in args.facilities.split(",") if item.strip()]
    if not facilities:
        parser.error("--facilities 不能为空")

    if os.environ.get("RUN_BAIDU_SMOKE") != "1":
        print("SKIPPED: 需要 RUN_BAIDU_SMOKE=1 才允许真实百度调用。")
        return 0
    model_key = api_key_from_env()
    if os.environ.get("RUN_DEEPSEEK_SMOKE") != "1" or model_key is None:
        print("SKIPPED: 需要 RUN_DEEPSEEK_SMOKE=1 且 DEEPSEEK_API_KEY 已配置。")
        return 0
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()
    if not ak:
        print("SKIPPED: BAIDU_MAP_AK 未配置。")
        return 0

    message = build_message(args.lng, args.lat, facilities)
    print(f"message={message}")
    print(f"facilities={facilities}")

    model = DeepSeekLLM(model_key)
    gateway = MVPToolGateway.from_baidu_ak(ak)
    guard = gateway.quota_guard
    before = guard.granted_routes if guard else 0
    import time

    started = time.monotonic()
    state = run_agent(
        run_id="run:sample",
        user_message=message,
        orchestrator=OrchestratorAgent(model),
        gateway=gateway,
        planning_agent=PlanningAgent(model),
        reviewer_agent=ReviewerAgent(model),
    )
    seconds = time.monotonic() - started
    route_targets = (guard.granted_routes - before) if guard else 0

    score = score_state(state, lng=args.lng, lat=args.lat, facilities=facilities)
    score.seconds = round(seconds, 1)
    score.route_targets = route_targets

    print(f"status={score.status} mode={score.completion_mode} "
          f"confidence={score.confidence}")
    print(f"unknown_ratios={score.unknown_ratios}")
    print(f"route_targets={route_targets} seconds={score.seconds}")
    print(f"warnings={score.warning_codes}")
    print("checks=" + " ".join(f"{k}:{v}" for k, v in score.checks.items()))
    if score.decidable_failures:
        print(f"DECIDED FAILURES: {score.decidable_failures}")
    if score.checks.get("A4") == "review":
        print(f"A4 需人工复核，命中词：{score.out_of_scope_hits}")
    for note in score.notes:
        print(f"  note: {note}")

    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(score.__dict__, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        # The full state stays local for a human reader; it can contain the
        # model's prose, so it is written next to the score, not committed.
        state_path = path.with_name(path.stem + "-state.json")
        state_path.write_text(
            json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"report written: {path}")
        print(f"full state written: {state_path}")

    return 0 if not score.decidable_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
