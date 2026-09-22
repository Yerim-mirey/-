"""Pure decisions for the future Agent graph."""

from app.schemas.agent import AgentRunState, ReviewStatus


MAX_PLANNING_ROUNDS = 3
MAX_TOOL_RETRIES = 2


def after_brief(state: AgentRunState) -> str:
    if state.brief is None:
        raise ValueError("Brief is required before routing")
    return "wait_for_input" if state.brief.missing_information else "fetch_evidence"


def after_evidence(state: AgentRunState) -> str:
    if state.brief is None or state.evidence is None:
        raise ValueError("Brief and evidence are required before routing")
    return "plan" if state.brief.needs_planning else "finalize"


def after_review(state: AgentRunState) -> str:
    if state.review_result is None:
        raise ValueError("ReviewResult is required before routing")
    if state.review_result.status is ReviewStatus.APPROVED:
        return "finalize"
    if state.planning_round >= MAX_PLANNING_ROUNDS:
        return "finalize_with_limitations"
    if state.review_result.status is ReviewStatus.REVISION_REQUIRED:
        return "plan"
    return "fetch_evidence"


def can_retry_tool(state: AgentRunState) -> bool:
    return state.tool_retry_count < MAX_TOOL_RETRIES
