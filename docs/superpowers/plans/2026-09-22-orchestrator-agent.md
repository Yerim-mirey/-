# Orchestrator Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert a user message into a validated `LifeCircleBrief` through an injected structured LLM.

**Architecture:** The LLM returns only semantic extraction fields. Python derives workflow flags, facility defaults, and missing-information records, then validates the public Agent v1 contract.

**Tech Stack:** Python 3.12, Pydantic v2, pytest; no new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-09-22-orchestrator-agent-design.md`

## Global Constraints

- Preserve existing Agent v1 and Tool 1–6 contracts.
- No real LLM network call, Tool invocation, workflow routing, planning, or reviewer behavior in this phase.
- Use one injectable generic model interface; do not select an LLM vendor.
- Test new behavior offline and run the full existing suite.

---

### Task 1: Generic LLM Boundary and Prompt

**Files:** Create `app/providers/llm.py`, `app/agents/__init__.py`, `app/agents/prompts.py`; test `tests/test_orchestrator.py`.

**Interfaces:** `StructuredLLM.generate_object(*, system_prompt: str, user_message: str, response_schema: dict) -> Mapping[str, object]`; `ORCHESTRATOR_SYSTEM_PROMPT`.

- [ ] Write a failing Orchestrator test with a fake `StructuredLLM` that records user text and returns the four extraction fields. Run focused pytest and confirm the new modules are absent.
- [ ] Add the Protocol and the prompt, then rerun the focused test. It must now fail only because Orchestrator behavior is absent.

### Task 2: Brief Construction

**Files:** Create `app/agents/orchestrator.py`; extend `tests/test_orchestrator.py`.

**Interfaces:** `OrchestratorAgent(llm: StructuredLLM).create_brief(user_message: str) -> LifeCircleBrief`; `OrchestratorOutputError` for invalid model output.

- [ ] Add tests for all five intent flags, missing location, focused-query missing facility types, full-diagnosis defaults, duplicate facilities, blank input, invalid model output, and provider exception propagation.
- [ ] Run focused pytest and inspect the expected RED failures.
- [ ] Add a strict internal extraction model, derive flags and missing records, validate `LifeCircleBrief`, and wrap only model-output validation errors.
- [ ] Run focused pytest until every new behavior passes.

### Task 3: Documentation and Verification

**Files:** Modify `README.md`; use `tests/test_orchestrator.py`.

- [ ] Document the injected provider interface, offline-only Phase 3 scope, and the later model-adapter boundary.
- [ ] Run `env -u BAIDU_MAP_AK -u RUN_BAIDU_SMOKE .venv/bin/python -m pytest -q -p no:cacheprovider` and `.venv/bin/python scripts/validate_contracts.py`.
- [ ] Run `git diff --check`, review against the spec, and commit the feature branch.
