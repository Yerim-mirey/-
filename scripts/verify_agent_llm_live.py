"""Opt-in, bounded real-model smoke check for the three Agent roles.

Run with DEEPSEEK_API_KEY and RUN_DEEPSEEK_SMOKE=1. It sends exactly three
requests (one per role) and prints only public summary data: no API key, no raw
prompt, no full model output. This checks schema validity, not answer quality.

    set -a; source .env; set +a
    RUN_DEEPSEEK_SMOKE=1 python scripts/verify_agent_llm_live.py
"""

import json
import os
from pathlib import Path

from app.agents.orchestrator import OrchestratorAgent
from app.agents.planning import PlanningAgent
from app.agents.reviewer import ReviewerAgent
from app.providers.deepseek import DeepSeekLLM, LLMError, api_key_from_env
from app.schemas.agent import EvidenceBundle, LifeCircleBrief, PlanningProposal

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"
SMOKE_BUNDLE_ID = "evidence:llm-smoke"
SMOKE_EVIDENCE_REFS = [{
    "evidence_id": "diagnosis:facility-market",
    "kind": "diagnosis",
    "json_pointer": "/diagnosis/data/metrics/0/blind_ratio",
    "summary": "菜市场盲区比例来自 Diagnosis 指标",
}]


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


def main() -> int:
    if os.environ.get("RUN_DEEPSEEK_SMOKE") != "1":
        print("Skipped: set RUN_DEEPSEEK_SMOKE=1 to permit real model requests.")
        return 0
    api_key = api_key_from_env()
    if api_key is None:
        print("Skipped: DEEPSEEK_API_KEY is not configured.")
        return 0

    model = DeepSeekLLM(api_key)
    print(f"model={model.model}")
    results: list[tuple[str, str]] = []
    for role, check in (
        ("orchestrator", check_orchestrator),
        ("planning", check_planning),
        ("reviewer", check_reviewer),
    ):
        try:
            results.append((role, check(model)))
        except LLMError as exc:
            results.append((role, f"FAILED {type(exc).__name__}: {exc}"))
        except ValueError as exc:
            results.append((role, f"REJECTED {exc}"))

    for role, summary in results:
        print(f"{role}: {summary}")
    print(f"usage(last)={model.last_usage}")
    failed = [role for role, summary in results if not summary.startswith(("intent=", "issues=", "status="))]
    if failed:
        print(f"RESULT: not contract-valid for {failed}")
        return 1
    print("RESULT: all three roles produced contract-valid output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
