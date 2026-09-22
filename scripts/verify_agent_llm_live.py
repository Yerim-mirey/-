"""Opt-in, bounded real-model smoke check for the three Agent roles.

Run with DEEPSEEK_API_KEY and RUN_DEEPSEEK_SMOKE=1. It sends a small, fixed
number of requests and prints only public summary data: no API key, no raw
prompt, and no full model output. It checks whether real output satisfies the
public contracts and reports the observed failure rate; it is not a quality
evaluation.

    set -a; source .env; set +a
    RUN_DEEPSEEK_SMOKE=1 RUN_DEEPSEEK_SMOKE_ROUNDS=3 python -m scripts.verify_agent_llm_live
"""

import json
import os
from collections import Counter
from pathlib import Path

from app.agents.orchestrator import OrchestratorAgent
from app.agents.planning import PlanningAgent
from app.agents.reviewer import ReviewerAgent
from app.providers.deepseek import DeepSeekLLM, LLMError
from app.providers.deepseek import api_key_from_env
from app.schemas.agent import EvidenceBundle, LifeCircleBrief, PlanningProposal

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"
SMOKE_BUNDLE_ID = "evidence:llm-smoke"
SMOKE_EVIDENCE_REFS = [{
    "evidence_id": "diagnosis:facility-market",
    "kind": "diagnosis",
    "json_pointer": "/diagnosis/data/metrics/0/blind_ratio",
    "summary": "菜市场盲区比例来自 Diagnosis 指标",
}]
MAX_ROUNDS = 5


def fixture(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def smoke_evidence() -> EvidenceBundle:
    return EvidenceBundle.model_validate({
        "schema_version": "1.0",
        "bundle_id": SMOKE_BUNDLE_ID,
        "refs": SMOKE_EVIDENCE_REFS,
        "diagnosis": fixture("diagnosis-result.example.json"),
    })


def smoke_proposal() -> PlanningProposal:
    """A proposal that matches the smoke bundle and only cites its evidence."""
    payload = fixture("agent-planning-proposal.example.json")
    payload["evidence_bundle_id"] = SMOKE_BUNDLE_ID
    for issue in payload["issues"]:
        issue["facility_type"] = "market"
    for planned in [*payload["issues"], *payload["recommendations"]]:
        planned["evidence_refs"] = ["diagnosis:facility-market"]
    return PlanningProposal.model_validate(payload)


def check_orchestrator(model: DeepSeekLLM) -> str:
    brief = OrchestratorAgent(model).create_brief(
        "请帮我看看上海市虹口区四川北路街道的菜场、药店和小学，并给出改善建议"
    )
    facilities = [item.value for item in brief.facility_types]
    missing = [item.field.value for item in brief.missing_information]
    return f"intent={brief.intent.value} facilities={facilities} missing={missing}"


def check_planning(model: DeepSeekLLM) -> str:
    proposal = PlanningAgent(model).create_proposal(
        LifeCircleBrief.model_validate(fixture("agent-brief.example.json")),
        smoke_evidence(),
        1,
    )
    return (
        f"issues={len(proposal.issues)} recommendations={len(proposal.recommendations)} "
        f"round={proposal.planning_round}"
    )


def check_reviewer(model: DeepSeekLLM) -> str:
    review = ReviewerAgent(model).review_proposal(smoke_evidence(), smoke_proposal())
    return (
        f"status={review.status.value} issues={len(review.issues)} "
        f"missing_evidence={len(review.missing_evidence)}"
    )


CHECKS = (
    ("orchestrator", check_orchestrator),
    ("planning", check_planning),
    ("reviewer", check_reviewer),
)


def run_check(model: DeepSeekLLM, check) -> tuple[bool, str]:
    """Return (contract_valid, summary). Failures are summarised, never raw."""
    try:
        return True, check(model)
    except LLMError as exc:
        return False, f"PROVIDER_ERROR {type(exc).__name__}: {exc}"
    except ValueError as exc:
        return False, f"CONTRACT_REJECTED {exc}"


def main() -> int:
    if os.environ.get("RUN_DEEPSEEK_SMOKE") != "1":
        print("Skipped: set RUN_DEEPSEEK_SMOKE=1 to permit real model requests.")
        return 0
    api_key = api_key_from_env()
    if api_key is None:
        print("Skipped: DEEPSEEK_API_KEY is not configured.")
        return 0

    rounds = max(1, min(MAX_ROUNDS, int(os.environ.get("RUN_DEEPSEEK_SMOKE_ROUNDS", "1"))))
    model = DeepSeekLLM(api_key)
    print(f"model={model.model} rounds={rounds} requests={rounds * len(CHECKS)}")

    failures: Counter = Counter()
    reasons: dict[str, str] = {}
    for round_index in range(1, rounds + 1):
        for role, check in CHECKS:
            valid, summary = run_check(model, check)
            print(f"round {round_index} {role}: {summary}")
            if not valid:
                failures[role] += 1
                reasons.setdefault(role, summary.split(" ", 1)[1])

    total = rounds * len(CHECKS)
    failed = sum(failures.values())
    print(f"usage(last)={model.last_usage}")
    if reasons:
        print("rejection reasons:")
        for role, reason in reasons.items():
            print(f"  {role}: {reason}")
    print(f"RESULT: {total - failed}/{total} contract-valid; failures={dict(failures) or '{}'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
