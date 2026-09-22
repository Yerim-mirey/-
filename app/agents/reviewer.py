"""Review one planning proposal against its evidence bundle."""

import json

from pydantic import ValidationError

from app.agents.prompts import REVIEWER_SYSTEM_PROMPT
from app.providers.llm import StructuredLLM
from app.schemas.agent import EvidenceBundle, PlanningProposal, ReviewResult


class ReviewerOutputError(ValueError):
    """The model returned an invalid or unrelated review."""


class ReviewerAgent:
    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    def review_proposal(self, evidence: EvidenceBundle, proposal: PlanningProposal) -> ReviewResult:
        evidence_ids = {ref.evidence_id for ref in evidence.refs}
        if proposal.evidence_bundle_id != evidence.bundle_id or any(
            not set(item.evidence_refs) <= evidence_ids
            for item in [*proposal.issues, *proposal.recommendations]
        ):
            raise ValueError("Proposal does not belong to the supplied evidence")
        raw = self._llm.generate_object(
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            user_message=json.dumps({
                "evidence": evidence.model_dump(mode="json"),
                "proposal": proposal.model_dump(mode="json"),
            }, ensure_ascii=False),
            response_schema=ReviewResult.model_json_schema(),
        )
        try:
            result = ReviewResult.model_validate(raw)
        except ValidationError:
            raise ReviewerOutputError("Model output does not satisfy the ReviewResult contract") from None
        recommendation_ids = {item.recommendation_id for item in proposal.recommendations}
        if (
            result.reviewed_proposal_id != proposal.proposal_id
            or result.reviewed_planning_round != proposal.planning_round
            or result.reviewed_evidence_bundle_id != evidence.bundle_id
            or any(
                not set(issue.recommendation_ids) <= recommendation_ids
                or not set(issue.evidence_refs) <= evidence_ids
                for issue in result.issues
            )
        ):
            raise ReviewerOutputError("Model output conflicts with the reviewed proposal or evidence")
        return result
