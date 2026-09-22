"""Turn validated diagnosis evidence into a planning proposal."""

import json

from pydantic import ValidationError

from app.agents.prompts import PLANNING_SYSTEM_PROMPT
from app.providers.llm import StructuredLLM
from app.schemas.agent import EvidenceBundle, LifeCircleBrief, PlanningProposal


class PlanningOutputError(ValueError):
    """The model returned an invalid or unrelated planning proposal."""


class PlanningAgent:
    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    def create_proposal(
        self, brief: LifeCircleBrief, evidence: EvidenceBundle, planning_round: int = 1
    ) -> PlanningProposal:
        if not brief.needs_planning or brief.missing_information:
            raise ValueError("Planning requires a complete planning brief")
        if type(planning_round) is not int or planning_round < 1:
            raise ValueError("planning_round must be a positive integer")
        if evidence.diagnosis is None or not evidence.diagnosis.root.ok:
            raise ValueError("Planning requires successful diagnosis evidence")
        diagnosed_types = {metric.facility_type for metric in evidence.diagnosis.root.data.metrics}
        if not set(brief.facility_types) <= diagnosed_types:
            raise ValueError("Diagnosis does not cover all requested facility types")

        payload = {
            "brief": brief.model_dump(mode="json"),
            "evidence": evidence.model_dump(mode="json"),
            "planning_round": planning_round,
        }
        raw = self._llm.generate_object(
            system_prompt=PLANNING_SYSTEM_PROMPT,
            user_message=json.dumps(payload, ensure_ascii=False),
            response_schema=PlanningProposal.model_json_schema(),
        )
        try:
            proposal = PlanningProposal.model_validate(raw)
        except ValidationError:
            raise PlanningOutputError("Model output does not satisfy the PlanningProposal contract") from None
        available_refs = {ref.evidence_id for ref in evidence.refs}
        items = [*proposal.issues, *proposal.recommendations]
        if (
            proposal.evidence_bundle_id != evidence.bundle_id
            or proposal.planning_round != planning_round
            or any(not set(item.evidence_refs) <= available_refs for item in items)
            or any(
                issue.facility_type is not None and issue.facility_type not in brief.facility_types
                for issue in proposal.issues
            )
        ):
            raise PlanningOutputError("Model output conflicts with the planning input or evidence")
        return proposal
