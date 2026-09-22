"""Bounded real-model diagnosis of Planning contract rejections.

`docs/test-report/phase10-prep-deepseek-adapter-2026-09-22.md` records that one
of four real Planning calls was rejected by the deterministic post-checks in
`PlanningAgent.create_proposal`, and that the rejection could not be explained
because the smoke script kept only the exception text, not the model output.

This harness closes that gap without weakening the Agent or leaking the model's
raw output. It replays the same controlled-role call the smoke script uses,
then evaluates the four conditions from `app/agents/planning.py` itself, so a
rejection is attributed to a specific cause instead of a single message:

1. ``proposal.evidence_bundle_id != evidence.bundle_id``
2. ``proposal.planning_round != planning_round``
3. an ``evidence_refs`` id that is not in the current EvidenceBundle
4. an ``issues[].facility_type`` outside the Brief's facility types

Diagnostics are structural: counts, and identity *shapes* such as
``diagnosis:<other>``. Free-text model prose, ids and prompts are never
printed or written, so the output stays safe to paste into a report.

Usage (spends real model requests; no Baidu quota is used)::

    set -a; source .env; set +a
    RUN_DEEPSEEK_SMOKE=1 python -m scripts.diagnose_planning_rejections --rounds 12
    RUN_DEEPSEEK_SMOKE=1 python -m scripts.diagnose_planning_rejections \
        --rounds 12 --report data/cache/planning-rejection-diagnosis.json
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

from app.agents.planning import PLANNING_SYSTEM_PROMPT
from app.providers.deepseek import DeepSeekLLM, LLMError, api_key_from_env
from app.schemas.agent import PlanningProposal
from scripts.verify_agent_llm_live import smoke_evidence

MAX_ROUNDS = 60


class ProposalShapeError(ValueError):
    """The model output did not satisfy the PlanningProposal contract."""


@dataclass
class Attempt:
    """One model call, reduced to counts and identity shapes."""

    index: int
    outcome: str = "ok"
    error: str = ""
    fingerprint: str = ""
    causes: list[str] = field(default_factory=list)
    issue_count: int = 0
    recommendation_count: int = 0
    bundle_id_matches: bool | None = None
    round_matches: bool | None = None
    cited_evidence_shapes: list[str] = field(default_factory=list)
    unavailable_evidence_shapes: list[str] = field(default_factory=list)
    out_of_scope_facilities: list[str] = field(default_factory=list)
    degenerate: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "outcome": self.outcome,
            "error": self.error,
            "fingerprint": self.fingerprint,
            "causes": self.causes,
            "issue_count": self.issue_count,
            "recommendation_count": self.recommendation_count,
            "bundle_id_matches": self.bundle_id_matches,
            "round_matches": self.round_matches,
            "cited_evidence_shapes": self.cited_evidence_shapes,
            "unavailable_evidence_shapes": self.unavailable_evidence_shapes,
            "out_of_scope_facilities": self.out_of_scope_facilities,
            "degenerate": self.degenerate,
        }


def shape(evidence_id: str) -> str:
    """Reduce an evidence id to its kind, so identifiers never leave the process."""
    return f"{evidence_id.split(':', 1)[0]}:<other>"


def fingerprint(raw: Any) -> str:
    """Describe a malformed output structurally, never by content.

    Distinguishes the failure modes worth telling apart in a report: a schema
    echo (the model pasted the schema back), a missing key, or a key present
    with the wrong shape. Field names and counts only; no values, no prose.
    """
    if not isinstance(raw, dict):
        return f"not_an_object:{type(raw).__name__}"
    flags = [
        label
        for key, label in (
            ("$defs", "schema_echo($defs)"),
            ("properties", "has_properties"),
            ("required", "has_required"),
            ("type", "has_type"),
        )
        if key in raw
    ]
    missing = sorted(
        key
        for key in (
            "proposal_id",
            "evidence_bundle_id",
            "summary",
            "issues",
            "recommendations",
        )
        if key not in raw
    )
    parts = [f"keys={len(raw)}", *sorted(flags)]
    if missing:
        parts.append("missing=" + ",".join(missing))
    return " ".join(parts)


def smoke_brief():
    from app.schemas.agent import LifeCircleBrief
    from scripts.verify_agent_llm_live import fixture

    return LifeCircleBrief.model_validate(fixture("agent-brief.example.json"))


def realistic_evidence():
    """The smoke bundle plus one diagnosis ref per facility metric.

    The smoke bundle carries a single `diagnosis:facility-market` ref, while a
    real Runner emits `diagnosis:facility-<type>` for every metric. Planning
    that cites the pharmacy or school is therefore rejected under the smoke
    fixture for a fixture reason, not a model reason. This variant removes that
    confound so the citation behaviour can be judged honestly.
    """
    from app.schemas.agent import EvidenceBundle

    base = smoke_evidence()
    payload = base.model_dump(mode="json")
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


BUNDLES = {"smoke": smoke_evidence, "realistic": realistic_evidence}


def build_payload(planning_round: int = 1, bundle: str = "smoke") -> tuple[dict[str, Any], Any]:
    """The exact user payload PlanningAgent builds, plus its EvidenceBundle."""
    evidence = BUNDLES[bundle]()
    payload = {
        "brief": smoke_brief().model_dump(mode="json"),
        "evidence": evidence.model_dump(mode="json"),
        "planning_round": planning_round,
    }
    return payload, evidence


def classify(raw: Any, evidence, brief, planning_round: int, index: int = 0) -> Attempt:
    """Attribute one raw model output to the conditions PlanningAgent enforces."""
    attempt = Attempt(index=index)
    try:
        proposal = PlanningProposal.model_validate(raw)
    except ValidationError as exc:
        attempt.outcome = "contract_rejected"
        # Which fields failed, never the values.
        attempt.error = ",".join(
            sorted({".".join(str(part) for part in item["loc"]) for item in exc.errors()})
        )
        attempt.fingerprint = fingerprint(raw)
        return attempt

    brief_facilities = {item.value for item in brief.facility_types}
    available = {ref.evidence_id for ref in evidence.refs}
    items = [*proposal.issues, *proposal.recommendations]
    cited = {ref for item in items for ref in item.evidence_refs}
    unavailable = cited - available
    out_of_scope = sorted(
        {
            issue.facility_type.value
            for issue in proposal.issues
            if issue.facility_type is not None
            and issue.facility_type.value not in brief_facilities
        }
    )

    attempt.issue_count = len(proposal.issues)
    attempt.recommendation_count = len(proposal.recommendations)
    attempt.bundle_id_matches = proposal.evidence_bundle_id == evidence.bundle_id
    attempt.round_matches = proposal.planning_round == planning_round
    attempt.cited_evidence_shapes = sorted({shape(item) for item in cited})
    attempt.unavailable_evidence_shapes = sorted({shape(item) for item in unavailable})
    attempt.out_of_scope_facilities = out_of_scope

    causes = []
    if not attempt.bundle_id_matches:
        causes.append("bundle_id_mismatch")
    if not attempt.round_matches:
        causes.append(f"round_mismatch(round={proposal.planning_round})")
    if unavailable:
        causes.append("unknown_evidence_ref")
    if out_of_scope:
        causes.append("facility_out_of_scope")

    if not proposal.issues:
        attempt.degenerate.append("no_issues")
    if not proposal.recommendations:
        attempt.degenerate.append("no_recommendations")
    if any(not item.evidence_refs for item in items):
        attempt.degenerate.append("empty_evidence_refs")

    if causes:
        attempt.outcome = "deterministic_rejected"
        attempt.causes = causes
    return attempt


def run_round(model: DeepSeekLLM, index: int, planning_round: int, bundle: str) -> Attempt:
    payload, evidence = build_payload(planning_round, bundle)
    try:
        raw = model.generate_object(
            system_prompt=PLANNING_SYSTEM_PROMPT,
            user_message=json.dumps(payload, ensure_ascii=False),
            response_schema=PlanningProposal.model_json_schema(),
        )
    except LLMError as exc:
        return Attempt(index=index, outcome="provider_error", error=type(exc).__name__)
    return classify(raw, evidence, smoke_brief(), planning_round, index)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=12, help="模型调用次数上限")
    parser.add_argument("--planning-round", type=int, default=1)
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
    model = DeepSeekLLM(api_key)
    evidence = BUNDLES[args.bundle]()
    print(f"model={model.model} rounds={rounds} planning_round={args.planning_round}")
    print(f"bundle={args.bundle} refs={[r.evidence_id for r in evidence.refs]}")
    print("（不消耗百度配额；不打印模型原文与标识符）")
    print()

    attempts = [
        run_round(model, index, args.planning_round, args.bundle)
        for index in range(1, rounds + 1)
    ]
    outcomes: Counter[str] = Counter(attempt.outcome for attempt in attempts)
    causes: Counter[str] = Counter(
        cause for attempt in attempts for cause in attempt.causes
    )

    for attempt in attempts:
        if attempt.outcome == "ok":
            detail = (
                f"issues={attempt.issue_count} recommendations={attempt.recommendation_count} "
                f"cited={attempt.cited_evidence_shapes}"
            )
        else:
            detail = (
                f"{attempt.error} {attempt.causes or attempt.degenerate} "
                f"[{attempt.fingerprint}]"
            ).strip()
        print(f"round {attempt.index:>2}: {attempt.outcome:<22} {detail}")

    print()
    print(f"outcomes={dict(outcomes)}")
    if causes:
        print(f"causes={dict(causes)}")
    usage = model.last_usage
    if usage:
        print(f"usage(last)={usage}")
    print(f"RESULT: ok={outcomes.get('ok', 0)}/{rounds}")

    if args.report:
        report = {
            "model": model.model,
            "rounds": rounds,
            "planning_round": args.planning_round,
            "bundle": args.bundle,
            "bundle_refs": [r.evidence_id for r in evidence.refs],
            "outcomes": dict(outcomes),
            "causes": dict(causes),
            "attempts": [attempt.to_payload() for attempt in attempts],
        }
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"report written: {path}")

    return 0 if outcomes.get("ok", 0) == rounds else 1


if __name__ == "__main__":
    raise SystemExit(main())
