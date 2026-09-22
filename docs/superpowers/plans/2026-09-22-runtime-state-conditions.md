# Phase 6 Runtime State + Conditions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide validated run-state updates and deterministic next-step decisions for the future Graph.

**Architecture:** Reuse `AgentRunState` rather than wrapping it in another class. Two small modules create/update state atomically and compute pure routing decisions.

**Tech Stack:** Python 3.12, Pydantic v2, pytest; no new dependency.

**Spec:** `docs/superpowers/specs/2026-09-22-runtime-state-conditions-design.md`

## Global Constraints

- No Graph, Runner, Tool calls, model calls, persistence, or new public schema.
- `MAX_PLANNING_ROUNDS = 3`; `MAX_TOOL_RETRIES = 2`.
- State transitions must revalidate the whole public contract and leave the source instance unchanged.

---

### Task 1: State Creation and Atomic Updates

**Files:** Create `app/agent_runtime/__init__.py`, `app/agent_runtime/state.py`, `tests/test_runtime_state.py`.

**Interfaces:** `new_run(run_id: str, user_message: str) -> AgentRunState`; `update_run(state: AgentRunState, **changes: object) -> AgentRunState`.

- [x] Write tests for a pending new run, an atomic Brief/status update, rejection without mutating the original, immutable identity, and clearing stale Proposal/Review when Evidence changes.

```python
pending = new_run("run:test", "体检社区")
running = update_run(pending, status="running", brief=brief)
assert pending.status.value == "pending"
assert running.brief == brief
```

- [x] Run focused pytest; confirm RED because the runtime module is absent.
- [x] Implement the two functions using `AgentRunState.model_validate` on the merged state payload.
- [x] Run focused pytest; confirm GREEN.

### Task 2: Pure Conditions

**Files:** Create `app/agent_runtime/conditions.py`, `tests/test_runtime_conditions.py`; modify `README.md`.

**Interfaces:** `after_brief(state)`, `after_evidence(state)`, `after_review(state) -> str`; `can_retry_tool(state) -> bool`.

- [x] Write failing tests for missing input, both Brief branches, both Evidence branches, three Review statuses, the planning-round cap, and retry-count boundary.

```python
assert after_review(reviewed_approved) == "finalize"
assert after_review(reviewed_revision_at_limit) == "finalize_with_limitations"
```

- [x] Run focused pytest; confirm RED because the conditions module is absent.
- [x] Implement direct `if`/`return` decisions with the two constants and no routing side effects.
- [x] Run focused and full offline pytest, `scripts/validate_contracts.py`, and `git diff --check`; document only the new boundary in README.
