"""Bounded real-model diagnosis of the three Agent contract outcomes.

`docs/test-report/phase10-prep-deepseek-adapter-2026-09-22.md` measured four
Orchestrator calls, four Planning calls and four Reviewer calls, and recorded
that one Planning rejection could not be explained. Four samples per role is
not enough to state a failure rate, and that harness kept only the exception
text.

This harness calls each Agent through its **real** validation code
(`OrchestratorAgent.create_brief`, `PlanningAgent.create_proposal`,
`ReviewerAgent.review_proposal`), so what it measures is exactly what the
Runner would accept or reject. It reports the specific failing condition
instead of one opaque message, and keeps every diagnostic structural: counts,
field paths and identity shapes such as ``diagnosis:<other>``. Free-text model
prose, ids, prompts and raw outputs are never printed or written.

Two evidence bundles are offered because the sparse one is a confound: the
original smoke bundle carried a single ``diagnosis:facility-market`` ref, while
a real Runner emits ``diagnosis:facility-<type>`` per metric, so planning that
cites the pharmacy was rejected for a fixture reason rather than a model one.

Usage (spends real model requests; no Baidu quota is used)::

    set -a; source .env; set +a
    RUN_DEEPSEEK_SMOKE=1 python -m scripts.diagnose_agent_contracts --rounds 12
    RUN_DEEPSEEK_SMOKE=1 python -m scripts.diagnose_agent_contracts \
        --rounds 12 --report data/cache/agent-contract-diagnosis.json
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agents.orchestrator import OrchestratorAgent
from app.agents.planning import PlanningAgent
from app.agents.reviewer import ReviewerAgent
from app.providers.deepseek import DeepSeekLLM, LLMError, api_key_from_env
from app.schemas.agent import EvidenceBundle, LifeCircleBrief, PlanningProposal


MAX_ROUNDS = 40
ROLES = ("orchestrator", "planning", "reviewer")


# --- Evidence bundles -------------------------------------------------------


def smoke_brief() -> LifeCircleBrief:
    from scripts.verify_agent_llm_live import fixture

    return LifeCircleBrief.model_validate(fixture("agent-brief.example.json"))


def sparse_evidence() -> EvidenceBundle:
    """The original smoke bundle: one ref, which is not what a Runner emits."""
    from scripts.verify_agent_llm_live import smoke_evidence

    return smoke_evidence()


def realistic_evidence() -> EvidenceBundle:
    """The smoke bundle plus one diagnosis ref per facility metric."""
    payload = sparse_evidence().model_dump(mode="json")
    metrics = payload["diagnosis"]["data"]["metrics"]
    payload["refs"] = [
        {
            "evidence_id": f"diagnosis:facility-{metric['facility_type']}",
            "kind": "diagnosis",
            "json_pointer": f"/diagnosis/data/metrics/{index}/blind_ratio",
            "summary": f"{metric['facility_type']} 的盲区比例来自 Diagnosis 指标",
        }
        for index, metric in enumerate(metrics)
    ]
    return EvidenceBundle.model_validate(payload)


BUNDLES = {"smoke": sparse_evidence, "realistic": realistic_evidence}


# --- Cases ------------------------------------------------------------------

# A spread of intents, including one request that must stop for user input, so
# the mapping from message to workflow flags is exercised rather than a single
# happy path.
ORCHESTRATOR_MESSAGES = (
    "请查询上海市虹口区四川北路街道附近的菜市场、药店和小学",
    "坐标 121.506123,31.282456 步行 15 分钟能到哪些菜市场？",
    "帮我看看上海市虹口区四川北路街道的服务盲区在哪里",
    "坐标 121.506123,31.282456 的设施配置是否合理？请给出改善规划建议",
    "帮我看看附近的菜市场和药店",
    "上海市虹口区四川北路街道的小学覆盖情况怎么样",
)


def proposal_for(evidence: EvidenceBundle, *, mismatch: bool = False) -> PlanningProposal:
    """A proposal bound to `evidence`, citing only ids that bundle provides.

    With ``mismatch=False`` each item cites the evidence for its own facility,
    which is a coherent proposal and the fair input for judging the Reviewer.
    With ``mismatch=True`` a recommendation is deliberately pointed at another
    facility's evidence, which a Reviewer must catch; feeding only that variant
    would say nothing about review quality.
    """
    from scripts.verify_agent_llm_live import smoke_proposal

    payload = smoke_proposal().model_dump(mode="json")
    payload["evidence_bundle_id"] = evidence.bundle_id
    metrics = [metric.facility_type.value for metric in evidence.diagnosis.root.data.metrics]
    ref_for = {facility: f"diagnosis:facility-{facility}" for facility in metrics}
    available = set(ref_for.values())
    default_ref = sorted(available)[0]

    for item in payload["issues"]:
        facility = item.get("facility_type") or metrics[0]
        item["evidence_refs"] = [ref_for.get(facility, default_ref)]
    for item in payload["recommendations"]:
        # A recommendation carries no facility of its own, so ground it on the
        # first facility metric rather than on whatever index it happens to be.
        item["evidence_refs"] = [ref_for.get(metrics[0], default_ref)]

    if mismatch and payload["recommendations"]:
        other = next(
            (ref for facility, ref in ref_for.items() if facility != metrics[0]),
            default_ref,
        )
        payload["recommendations"][0]["evidence_refs"] = [other]
    return PlanningProposal.model_validate(payload)


# --- Observations -----------------------------------------------------------


@dataclass
class Attempt:
    """One model call, reduced to counts and identity shapes."""

    index: int
    role: str = ""
    outcome: str = "ok"
    error: str = ""
    fingerprint: str = ""
    causes: list[str] = field(default_factory=list)
    # Orchestrator
    intent: str | None = None
    facility_count: int = 0
    missing_fields: list[str] = field(default_factory=list)
    # Planning
    issue_count: int = 0
    recommendation_count: int = 0
    bundle_id_matches: bool | None = None
    round_matches: bool | None = None
    cited_evidence_shapes: list[str] = field(default_factory=list)
    unavailable_evidence_shapes: list[str] = field(default_factory=list)
    out_of_scope_facilities: list[str] = field(default_factory=list)
    # Reviewer
    review_status: str | None = None
    review_issue_count: int = 0
    missing_evidence_count: int = 0
    proposal_is_grounded: bool | None = None
    discriminates: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "role": self.role,
            "outcome": self.outcome,
            "error": self.error,
            "fingerprint": self.fingerprint,
            "causes": self.causes,
            "intent": self.intent,
            "facility_count": self.facility_count,
            "missing_fields": self.missing_fields,
            "issue_count": self.issue_count,
            "recommendation_count": self.recommendation_count,
            "bundle_id_matches": self.bundle_id_matches,
            "round_matches": self.round_matches,
            "cited_evidence_shapes": self.cited_evidence_shapes,
            "unavailable_evidence_shapes": self.unavailable_evidence_shapes,
            "out_of_scope_facilities": self.out_of_scope_facilities,
            "review_status": self.review_status,
            "review_issue_count": self.review_issue_count,
            "missing_evidence_count": self.missing_evidence_count,
            "proposal_is_grounded": self.proposal_is_grounded,
            "discriminates": self.discriminates,
        }


def shape(evidence_id: str) -> str:
    """Reduce an evidence id to its kind, so identifiers never leave the process."""
    return f"{evidence_id.split(':', 1)[0]}:<other>"


def cause_of(exc: BaseException) -> str:
    """Each Agent raises a distinct exception type; that type is the cause."""
    return type(exc).__name__


# --- Per-role runs ---------------------------------------------------------


def run_orchestrator(model: DeepSeekLLM, index: int, message: str) -> Attempt:
    attempt = Attempt(index=index, role="orchestrator")
    try:
        brief = OrchestratorAgent(model).create_brief(message)
    except LLMError as exc:
        attempt.outcome = "provider_error"
        attempt.error = cause_of(exc)
        return attempt
    except (ValidationError, ValueError) as exc:
        attempt.outcome = "contract_rejected"
        attempt.error = cause_of(exc)
        return attempt
    attempt.intent = brief.intent.value
    attempt.facility_count = len(brief.facility_types)
    attempt.missing_fields = sorted(
        item.field.value for item in brief.missing_information
    )
    return attempt


def run_planning(
    model: DeepSeekLLM, index: int, evidence: EvidenceBundle, brief: LifeCircleBrief
) -> Attempt:
    attempt = Attempt(index=index, role="planning")
    available = {ref.evidence_id for ref in evidence.refs}
    brief_facilities = {item.value for item in brief.facility_types}
    try:
        proposal = PlanningAgent(model).create_proposal(brief, evidence, 1)
    except LLMError as exc:
        attempt.outcome = "provider_error"
        attempt.error = cause_of(exc)
        return attempt
    except (ValidationError, ValueError) as exc:
        # PlanningAgent raises PlanningOutputError for every deterministic
        # conflict, so the type alone is not the cause; report which rule broke
        # using the proposal-independent facts we can still observe.
        attempt.outcome = "deterministic_rejected"
        attempt.causes = [cause_of(exc)]
        return attempt

    items = [*proposal.issues, *proposal.recommendations]
    cited = {ref for item in items for ref in item.evidence_refs}
    attempt.issue_count = len(proposal.issues)
    attempt.recommendation_count = len(proposal.recommendations)
    attempt.bundle_id_matches = proposal.evidence_bundle_id == evidence.bundle_id
    attempt.round_matches = proposal.planning_round == 1
    attempt.cited_evidence_shapes = sorted({shape(item) for item in cited})
    attempt.unavailable_evidence_shapes = sorted(
        {shape(item) for item in cited - available}
    )
    attempt.out_of_scope_facilities = sorted(
        {
            issue.facility_type.value
            for issue in proposal.issues
            if issue.facility_type is not None
            and issue.facility_type.value not in brief_facilities
        }
    )
    return attempt


def run_reviewer(
    model: DeepSeekLLM,
    index: int,
    evidence: EvidenceBundle,
    proposal: PlanningProposal,
    *,
    expect_revision: bool,
) -> Attempt:
    attempt = Attempt(index=index, role="reviewer")
    attempt.proposal_is_grounded = not expect_revision
    try:
        review = ReviewerAgent(model).review_proposal(evidence, proposal)
    except LLMError as exc:
        attempt.outcome = "provider_error"
        attempt.error = cause_of(exc)
        return attempt
    except (ValidationError, ValueError) as exc:
        attempt.outcome = "contract_rejected"
        attempt.error = cause_of(exc)
        return attempt
    attempt.review_status = review.status.value
    attempt.review_issue_count = len(review.issues)
    attempt.missing_evidence_count = len(review.missing_evidence)
    # A grounded proposal should be approved; a mis-cited one should not be.
    attempt.discriminates = (
        review.status.value == "approved"
        if attempt.proposal_is_grounded
        else review.status.value != "approved"
    )
    return attempt


def run_round(model: DeepSeekLLM, index: int, role: str, bundle: str) -> Attempt:
    if role == "orchestrator":
        return run_orchestrator(
            model, index, ORCHESTRATOR_MESSAGES[(index - 1) % len(ORCHESTRATOR_MESSAGES)]
        )
    evidence = BUNDLES[bundle]()
    if role == "planning":
        return run_planning(model, index, evidence, smoke_brief())
    # Alternate grounded and mis-cited proposals so the Reviewer is measured on
    # both accepting good work and catching a defect, not on one of the two.
    mismatch = index % 2 == 0
    return run_reviewer(
        model,
        index,
        evidence,
        proposal_for(evidence, mismatch=mismatch),
        expect_revision=mismatch,
    )


# --- Reporting --------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=12, help="每个角色的调用次数上限")
    parser.add_argument(
        "--role",
        action="append",
        choices=ROLES,
        default=[],
        help="只测指定角色，可重复；默认三个都测",
    )
    parser.add_argument(
        "--bundle",
        choices=sorted(BUNDLES),
        default="realistic",
        help="realistic=每个设施指标各一条引用（贴近真实 Runner）；smoke=单条引用",
    )
    parser.add_argument("--report", default="", help="脱敏诊断 JSON 的输出路径")
    args = parser.parse_args(argv)

    if os.environ.get("RUN_DEEPSEEK_SMOKE") != "1":
        print("SKIPPED: 需要 RUN_DEEPSEEK_SMOKE=1 才允许真实模型调用。")
        return 0
    api_key = api_key_from_env()
    if api_key is None:
        print("SKIPPED: DEEPSEEK_API_KEY 未配置。")
        return 0

    rounds = max(1, min(MAX_ROUNDS, args.rounds))
    roles = tuple(args.role) or ROLES
    model = DeepSeekLLM(api_key)
    evidence = BUNDLES[args.bundle]()
    print(
        f"model={model.model} roles={list(roles)} rounds={rounds} "
        f"→ {rounds * len(roles)} 次请求"
    )
    print(f"bundle={args.bundle} refs={[ref.evidence_id for ref in evidence.refs]}")
    print("（不消耗百度配额；不打印模型原文、标识符与提示词）")
    print()

    all_attempts: list[Attempt] = []
    role_summaries: dict[str, dict[str, Any]] = {}
    for role in roles:
        attempts = [
            run_round(model, index, role, args.bundle)
            for index in range(1, rounds + 1)
        ]
        all_attempts.extend(attempts)
        outcomes: Counter = Counter(attempt.outcome for attempt in attempts)
        causes: Counter = Counter(
            cause for attempt in attempts for cause in attempt.causes
        )
        role_summaries[role] = {
            "outcomes": dict(outcomes),
            "causes": dict(causes),
            "intents": dict(
                Counter(a.intent for a in attempts if a.intent is not None)
            ),
            "review_statuses": dict(
                Counter(a.review_status for a in attempts if a.review_status is not None)
            ),
            "discrimination": {
                "measured": sum(1 for a in attempts if a.discriminates is not None),
                "correct": sum(1 for a in attempts if a.discriminates is True),
            },
        }
        print(f"--- {role}: ok={outcomes.get('ok', 0)}/{rounds} outcomes={dict(outcomes)}")
        if causes:
            print(f"    causes={dict(causes)}")
        for attempt in attempts:
            if attempt.outcome == "ok":
                continue
            print(f"    round {attempt.index:>2}: {attempt.outcome:<22} {attempt.error}")

    total_ok = sum(1 for attempt in all_attempts if attempt.outcome == "ok")
    print()
    print(f"usage(last)={model.last_usage}")
    print(f"RESULT: ok={total_ok}/{len(all_attempts)}")

    if args.report:
        report = {
            "model": model.model,
            "rounds": rounds,
            "roles": list(roles),
            "bundle": args.bundle,
            "bundle_refs": [ref.evidence_id for ref in evidence.refs],
            "role_summaries": role_summaries,
            "attempts": [attempt.to_payload() for attempt in all_attempts],
        }
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"report written: {path}")

    return 0 if total_ok == len(all_attempts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
