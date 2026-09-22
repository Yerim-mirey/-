"""Declarative Agent v1 graph: nodes, edges, and deterministic transitions.

The module holds no business logic. Every branch decision comes from the pure
condition functions in ``conditions.py``; the table here only names the node each
decision leads to.
"""

from app.agent_runtime.conditions import after_brief, after_evidence, after_review
from app.schemas.agent import AgentRunState

START = "start"
ORCHESTRATE = "orchestrate"
FETCH_EVIDENCE = "fetch_evidence"
PLAN = "plan"
REVIEW = "review"
FINALIZE = "finalize"

REQ_FETCH_EVIDENCE = "req_fetch_evidence"
REQ_WAIT_FOR_INPUT = "req_wait_for_input"
REQ_PLAN = "req_plan"
REQ_FINALIZE = "req_finalize"
REQ_FINALIZE_WITH_LIMITATIONS = "req_finalize_with_limitations"

# Run warning codes that already decide the Review route.
LIMITED_COMPLETION_CODES = {"INSUFFICIENT_EVIDENCE"}

_FINALIZE = {REQ_FINALIZE: FINALIZE, REQ_FINALIZE_WITH_LIMITATIONS: FINALIZE}

NODE_TRANSITIONS: dict[str, dict[str, str]] = {
    ORCHESTRATE: {
        REQ_FETCH_EVIDENCE: FETCH_EVIDENCE,
        REQ_WAIT_FOR_INPUT: REQ_WAIT_FOR_INPUT,
    },
    FETCH_EVIDENCE: {REQ_PLAN: PLAN, REQ_FINALIZE: FINALIZE, REQ_FETCH_EVIDENCE: FETCH_EVIDENCE},
    PLAN: {"review": REVIEW},
    REVIEW: {
        **_FINALIZE,
        REQ_PLAN: PLAN,
        REQ_FETCH_EVIDENCE: FETCH_EVIDENCE,
    },
}

_DECISIONS = {
    ORCHESTRATE: after_brief,
    FETCH_EVIDENCE: after_evidence,
    REVIEW: after_review,
}

_DECISION_REQUIREMENTS = {
    ORCHESTRATE: {"fetch_evidence": REQ_FETCH_EVIDENCE, "wait_for_input": REQ_WAIT_FOR_INPUT},
    FETCH_EVIDENCE: {"plan": REQ_PLAN, "finalize": REQ_FINALIZE},
    REVIEW: {
        "finalize": REQ_FINALIZE,
        "plan": REQ_PLAN,
        "fetch_evidence": REQ_FETCH_EVIDENCE,
        "finalize_with_limitations": REQ_FINALIZE_WITH_LIMITATIONS,
    },
}

# PLAN has a single unconditional edge; its requirement name matches the edge key.
UNCONDITIONAL_REQUIREMENTS = {PLAN: "review"}


def terminal_requirement(state: AgentRunState, node: str) -> str:
    """Return the branch requirement for ``node`` under the public state."""
    if node == REVIEW and any(
        item.code in LIMITED_COMPLETION_CODES for item in state.warnings
    ):
        return REQ_FINALIZE_WITH_LIMITATIONS
    if node in UNCONDITIONAL_REQUIREMENTS:
        return UNCONDITIONAL_REQUIREMENTS[node]
    try:
        decision = _DECISIONS[node](state)
    except KeyError:
        raise ValueError(f"Node {node} makes no runtime decision") from None
    return _DECISION_REQUIREMENTS[node][decision]


def next_node(node: str, requirement: str) -> str:
    """Return the node the graph moves to, or the terminal requirement itself."""
    try:
        return NODE_TRANSITIONS[node][requirement]
    except KeyError:
        raise ValueError(f"No graph edge for {node} -> {requirement}") from None
