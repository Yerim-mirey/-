# Phase 5 Reviewer Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Review one evidence-linked planning proposal and return a validated `ReviewResult`.

**Architecture:** Reuse `StructuredLLM` and public Agent v1 models. The model judges factual support; one small Reviewer class enforces input/output identity and reference boundaries.

**Tech Stack:** Python 3.12, Pydantic v2, pytest; no new dependency.

**Spec:** `docs/superpowers/specs/2026-09-22-reviewer-agent-design.md`

## Global Constraints

- No Tool calls, GIS recomputation, Planning Agent calls, Runtime routing, or real model adapter.
- Keep public Agent v1 schemas unchanged; do not copy the ReviewResult validation logic.
- Reuse `StructuredLLM`; sanitize invalid model output and preserve provider exceptions.

---

### Task 1: Approved Review

**Files:** Create `app/agents/reviewer.py`, `tests/test_reviewer.py`; modify `app/agents/prompts.py`.

**Interfaces:** `ReviewerAgent(llm: StructuredLLM).review_proposal(evidence: EvidenceBundle, proposal: PlanningProposal) -> ReviewResult`.

- [x] Write a test using the existing approved ReviewResult fixture and a diagnosis EvidenceBundle; assert the public output identity and serialized input.

```python
review = ReviewerAgent(fake_llm).review_proposal(evidence, proposal)
assert review.status.value == "approved"
assert review.reviewed_proposal_id == proposal.proposal_id
```

- [x] Run focused pytest and confirm RED because Reviewer Agent is absent.
- [x] Implement the smallest class and one review prompt, then rerun focused pytest to GREEN.

### Task 2: Three Statuses and Boundaries

**Files:** Modify `app/agents/reviewer.py`, `tests/test_reviewer.py`, `README.md`.

**Interfaces:** `ReviewerOutputError(ValueError)` for invalid model output; invalid inputs raise `ValueError` before model call.

- [x] Add failing tests for revision and insufficient-evidence results, mismatched proposal/evidence, unknown proposal refs, wrong output identity, unknown issue references, malformed output, and provider errors.

```python
bad = dict(approved_review)
bad["reviewed_proposal_id"] = "proposal:other"
with pytest.raises(ReviewerOutputError):
    ReviewerAgent(fake_llm_returning(bad)).review_proposal(evidence, proposal)
```

- [x] Run focused pytest and confirm each new trust-boundary test is RED for the intended reason.
- [x] Add input and output checks without duplicating Pydantic's status rules.
- [x] Run focused and full offline pytest, `scripts/validate_contracts.py`, and `git diff --check`; add a short README boundary note.
