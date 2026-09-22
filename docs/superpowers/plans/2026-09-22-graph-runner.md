# Phase 7 Graph + Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute one Agent v1 run end to end by connecting the existing Agents, Gateway, Runtime state, and conditions through a declarative graph and one runner.

**Architecture:** `graph.py` declares node names and deterministic transitions over the existing condition functions; `runner.py` owns the single execution loop, evidence assembly, retry budget, review loop, and terminal status. Public Agent v1 contracts stay unchanged.

**Tech Stack:** Python 3.12, Pydantic v2, pytest; no new dependency.

**Spec:** `docs/superpowers/specs/2026-09-22-graph-runner-design.md`

## Global Constraints

- No real Baidu call, model adapter, persistence, queue, new Agent, or new public schema.
- Reuse `new_run`/`update_run`, `conditions.py`, `OrchestratorAgent`, `PlanningAgent`, `ReviewerAgent`, and `ToolGateway`; the Runner never computes GIS.
- Full diagnosis goes through `diagnose_community()`; `EvidenceBundle` never mixes Diagnosis with low-level results.
- Unknown facility counts (`null`) must be reported as warnings, not blindspots.

---

### Task 1: Declarative Graph

**Files:** Create `app/agent_runtime/graph.py`, `tests/test_agent_graph.py`.

**Interfaces:** node constants `START`, `ORCHESTRATE`, `FETCH_EVIDENCE`, `PLAN`, `REVIEW`, `FINALIZE`; terminal requirement constants `REQ_FETCH_EVIDENCE`, `REQ_WAIT_FOR_INPUT`, `REQ_PLAN`, `REQ_FINALIZE`, `REQ_FINALIZE_WITH_LIMITATIONS`; `NODE_TRANSITIONS: dict[str, dict[str, str]]`; `terminal_requirement(state, node) -> str`; `next_node(node, requirement) -> str`.

- [x] Write tests for the main path, the review loop edges, the wait terminal, and rejection of unknown nodes or requirements.

```python
assert next_node(REVIEW, terminal_requirement(reviewed_revision, REVIEW)) == PLAN
assert terminal_requirement(waiting_state, ORCHESTRATE) == REQ_WAIT_FOR_INPUT
```

- [x] Run focused pytest; confirm RED because the graph module is absent.
- [x] Implement the constants, the transition table, and direct delegation to `after_brief`, `after_evidence`, and `after_review`.
- [x] Run focused pytest; confirm GREEN.

### Task 2: Runner Execution Loop

**Files:** Create `app/agent_runtime/runner.py`, `tests/test_agent_runner.py`.

**Interfaces:** `run_agent(*, run_id: str, user_message: str, orchestrator: OrchestratorAgent, gateway: ToolGateway, planning_agent: PlanningAgent | None = None, reviewer_agent: ReviewerAgent | None = None) -> AgentRunState`.

- [x] Write tests with a controlled `StructuredLLM` and the real `MockToolGateway` for: waiting for input, a non-planning POI run ending `normal`, an approved full-diagnosis planning run, and a coordinate Brief whose diagnosis covers the requested facilities.

```python
state = run_agent(run_id="run:test", user_message="体检", orchestrator=..., gateway=MockToolGateway(), planning_agent=..., reviewer_agent=...)
assert state.status is RunStatus.COMPLETED and state.completion_mode is CompletionMode.NORMAL
```

- [x] Run focused pytest; confirm RED because the runner module is absent.
- [x] Implement `run_agent` with the single node loop, evidence assembly, `update_run`-based invalidation, and terminal marking.
- [x] Run focused pytest; confirm GREEN.

### Task 3: Retry, Loop, and Failure Bound

**Files:** Modify `app/agent_runtime/runner.py`, `tests/test_agent_runner.py`; modify `README.md`.

- [x] Write tests for a retryable failure followed by success, a non-retryable failure ending `failed`, exhausted Tool retries ending `failed`, `revision_required` advancing the planning round, `insufficient_evidence` consuming the Tool retry budget and completing `with_limitations`, and the planning-round cap completing `with_limitations`.
- [x] Run focused pytest; confirm RED for the missing behavior.
- [x] Implement the retry budget, the review-loop branches, and the warnings that explain limited completion; never write half-built evidence.
- [x] Run focused and full offline pytest, `scripts/validate_contracts.py`, and `git diff --check`; document the new modules and boundaries in README.
