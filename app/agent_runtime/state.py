"""Create and atomically update the public AgentRunState."""

from app.schemas.agent import AgentRunState, RunStatus


def new_run(run_id: str, user_message: str) -> AgentRunState:
    return AgentRunState(
        schema_version="1.0",
        run_id=run_id,
        status=RunStatus.PENDING,
        user_message=user_message,
    )


def update_run(state: AgentRunState, **changes: object) -> AgentRunState:
    if {"schema_version", "run_id", "user_message"} & changes.keys():
        raise ValueError("Run identity cannot change")
    payload = state.model_dump(mode="json")
    payload.update(changes)
    updated = AgentRunState.model_validate(payload)
    for source, dependent in (
        ("brief", "evidence"),
        ("evidence", "planning_proposal"),
        ("planning_proposal", "review_result"),
    ):
        if (
            getattr(state, source) is not None
            and getattr(updated, source) != getattr(state, source)
            and getattr(updated, dependent) is not None
        ):
            raise ValueError(f"Changing {source} requires clearing {dependent}")
    if state.brief is not None and updated.brief != state.brief and updated.planning_round != 0:
        raise ValueError("Changing brief requires resetting planning_round")
    return updated
