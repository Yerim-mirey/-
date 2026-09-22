"""Turn validated diagnosis evidence into a planning proposal."""

import json

from pydantic import ValidationError

from app.agents.prompts import PLANNING_SYSTEM_PROMPT
from app.providers.llm import StructuredLLM
from app.schemas.agent import EvidenceBundle, LifeCircleBrief, PlanningProposal, _decoded_json_pointer_tokens
from app.schemas.location import CoordinateInput


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
        center = evidence.diagnosis.root.data.center
        if isinstance(brief.location, CoordinateInput) and (
            brief.location.lng != center.lng or brief.location.lat != center.lat
        ):
            raise ValueError("Diagnosis center does not match the requested coordinate")
        diagnosed_types = {metric.facility_type for metric in evidence.diagnosis.root.data.metrics}
        if not set(brief.facility_types) <= diagnosed_types:
            raise ValueError("Diagnosis does not cover all requested facility types")
        evidence_data = evidence.model_dump(mode="json")
        for ref in evidence.refs:
            target = evidence_data
            try:
                for token in _decoded_json_pointer_tokens(ref.json_pointer):
                    if isinstance(target, list):
                        if not token.isdecimal() or str(int(token)) != token:
                            raise ValueError("Invalid array index")
                        target = target[int(token)]
                    else:
                        target = target[token]
            except (KeyError, IndexError, TypeError, ValueError):
                raise ValueError("Evidence reference does not resolve") from None

        payload = {
            "brief": brief.model_dump(mode="json"),
            "evidence": evidence_data,
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
