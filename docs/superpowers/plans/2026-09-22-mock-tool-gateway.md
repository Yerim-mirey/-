# Mock Tool Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give future Agents a stable Tool 1–6 interface and deterministic offline exchanges before real Tool integration.

**Architecture:** `ToolGateway` is a typed Protocol over the six existing v1 request/result pairs. `MockToolGateway` stores validated request/result exchanges keyed by method plus canonical request JSON; bundled contract examples provide defaults. A separate fixture loader keeps test data out of the protocol and core lookup logic.

**Tech Stack:** Python 3.12, Pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-mock-tool-gateway-design.md`

## Global Constraints

- Preserve existing Tool 1–6 and Agent v1 schemas and services.
- No live Provider, Service, network, Agent, Runtime, API, or frontend calls in the Mock.
- Unconfigured exchanges raise `MockScenarioError`; business failure is returned as a typed `ok=false` result.
- Every result is a defensive copy, and calls record the method plus canonical request.
- Use the same contract tests for future `MVPToolGateway`.

---

### Task 1: Stable Gateway Protocol

**Files:** Create `app/agent_tools/__init__.py`, `app/agent_tools/gateway.py`; test `tests/test_mock_tool_gateway.py`.

**Interfaces:** Produce `ToolGateway` with `resolve_location(LocationRequest)->LocationResult`, `search_pois(POISearchRequest)->POISearchResult`, `calculate_walking_times(RoutingRequest)->RoutingResult`, `generate_isochrone(IsochroneRequest)->IsochroneResult`, `detect_blindspots(BlindspotRequest)->BlindspotResult`, and `diagnose_community(DiagnosisRequest)->DiagnosisResult`.

- [ ] Write a test that imports `ToolGateway` and confirms the six typed methods are present.
- [ ] Run focused pytest; confirm the missing module is the reason for failure.
- [ ] Add only the Protocol and package file, with each method as an ellipsis body.
- [ ] Run focused pytest and confirm it passes.

### Task 2: Validated Request/Result Exchange Store

**Files:** Create `app/agent_tools/mock_gateway.py`; extend `tests/test_mock_tool_gateway.py`.

**Interfaces:** Produce `MockExchange(method, request, result)`, `MockCall(method, request_json)`, `MockScenarioError`, and `MockToolGateway(exchanges=None)` with the six protocol methods. `exchanges=None` loads built-in examples; an explicit iterable replaces the defaults.

- [ ] Test canonical request matching, six typed returns, JSON round-trip, call recording, missing scenarios, business failure, and defensive copies. Test exchange validation rejects wrong method/model and mismatched centers/categories/targets/thresholds.
- [ ] Run focused pytest; confirm behavior fails because the Mock is absent.
- [ ] Implement a method-to-model registry, canonical JSON keys, registration validation, immutable stored result copies, and six delegating methods. Use existing Blindspot and Diagnosis exchange validators.
- [ ] Run focused pytest and fix only failures in these behaviors.

### Task 3: Bundled Scenarios and Reusable Contract Tests

**Files:** Create `app/agent_tools/mock_scenarios.py`; extend `tests/test_mock_tool_gateway.py`; modify `README.md`.

**Interfaces:** `load_contract_exchanges(diagnosis_outcome="success")` loads the six existing `contracts/v1/*` request/result pairs; diagnosis outcome may be `success`, `partial`, or `failure`. Keep fixture paths inside the loader.

- [ ] Test default six exchanges, diagnosis success/partial/failure, and no Service/Provider/network imports or calls. Define reusable `assert_gateway_contract` test helper taking any `ToolGateway` implementation.
- [ ] Run focused pytest; confirm the missing scenario loader or behavior causes failure.
- [ ] Implement the loader using existing fixture JSON and Pydantic models; document injection and the real-adapter boundary in README.
- [ ] Run focused pytest, complete offline suite, `scripts/validate_contracts.py`, and `git diff --check`.

## Review

Check every spec requirement against the final diff. Verify no live path was introduced and record exact passing counts before integrating the branch into the Desktop checkout.
