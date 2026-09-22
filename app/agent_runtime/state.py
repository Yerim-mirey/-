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
    return AgentRunState.model_validate(payload)
