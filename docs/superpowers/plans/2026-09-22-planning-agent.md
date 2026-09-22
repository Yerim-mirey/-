# Phase 4 Planning Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a planning Brief and validated diagnosis EvidenceBundle into an evidence-linked `PlanningProposal`.

**Architecture:** Reuse the existing `StructuredLLM` and public Agent v1 models. A single Planning Agent checks inputs and model output against the supplied evidence; the model owns prose and priorities.

**Tech Stack:** Python 3.12, Pydantic v2, pytest; no new dependency.

**Spec:** `docs/superpowers/specs/2026-09-22-planning-agent-design.md`

## Global Constraints

- No map Tool calls, GIS recomputation, Runtime routing, Reviewer behavior, or real model adapter.
- Keep the public Agent v1 schemas unchanged.
- Reuse `StructuredLLM`; reject bad model output without including private input or raw output in the error.

---

### Task 1: Valid Planning Proposal

**Files:** Create `app/agents/planning.py`, `tests/test_planning.py`; modify `app/agents/prompts.py`.

**Interfaces:** `PlanningAgent(llm: StructuredLLM).create_proposal(brief: LifeCircleBrief, evidence: EvidenceBundle, planning_round: int = 1) -> PlanningProposal`.

- [x] Write a test with the existing brief and proposal JSON fixtures plus a diagnosis evidence bundle. It must assert the returned public proposal and passed input JSON contain the matching bundle ID and round.

```python
proposal = PlanningAgent(fake_llm).create_proposal(brief, evidence)
assert proposal.evidence_bundle_id == evidence.bundle_id
assert proposal.planning_round == 1
```

- [x] Run `pytest -q tests/test_planning.py -p no:cacheprovider`; confirm RED because Planning Agent is absent.
- [x] Implement the smallest class, one planning prompt, and `PlanningProposal.model_validate(raw)`; send the existing schema to the model.
- [x] Run the focused test; confirm GREEN.

### Task 2: Trust Boundaries

**Files:** Modify `app/agents/planning.py`, `tests/test_planning.py`, `README.md`.

**Interfaces:** `PlanningOutputError(ValueError)` for malformed or cross-inconsistent model output; invalid inputs raise `ValueError` before model call.

- [x] Add failing tests: non-planning or incomplete brief, missing/failed diagnosis, dangling pointer, mismatched coordinate, bad round, wrong bundle/round, unresolved refs, out-of-scope issue type, malformed output, provider exception.

```python
bad = dict(valid_proposal)
bad["recommendations"][0]["evidence_refs"] = ["diagnosis:unknown"]
with pytest.raises(PlanningOutputError):
    PlanningAgent(fake_llm_returning(bad)).create_proposal(brief, evidence)
```

- [x] Run focused pytest and confirm these fail for the intended missing guards.
- [x] Add input guards and cross-output checks; keep provider errors unchanged and output errors sanitized.
- [x] Run focused pytest, full offline pytest, `scripts/validate_contracts.py`, and `git diff --check`; document only the new usage boundary in README.
