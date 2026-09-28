# Community Facility Coverage v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standards-led, extensible community-facility catalog and generic V2 lookup/analysis API while preserving the current three-category V1 API.

**Architecture:** A versioned registry owns canonical facility IDs, aliases, Baidu search terms, capabilities, metric profiles, and evidence notes. Generic V2 schemas and services consume that registry; V1 retains its existing strict contracts and maps only the original three IDs. Unknown but clear facility terms use explicitly exploratory POI lookup without standard diagnosis.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, existing Baidu Web Service providers, pytest.

**Spec:** `docs/superpowers/specs/2026-09-28-community-facility-coverage-v2-design.md`

## Global Constraints

- V1 paths and response contracts for `market`, `pharmacy`, and `primary_school` remain unchanged.
- All new facility IDs are catalog-validated canonical strings; the LLM cannot invent IDs.
- No diagnosis metric is applied unless its facility profile states the threshold and basis.
- Empty, failed, partial, unknown, and confirmed-zero results remain distinct.
- Do not make live Baidu or DeepSeek calls during implementation or offline verification.
- Do not claim POI coordinates establish capacity, opening status, service quality, prices, or population coverage.

---

### Task 1: Facility catalog and validation

**Files:**
- Create: `app/modules/poi/facility_catalog.json`
- Create: `app/modules/poi/facility_catalog.py`
- Test: `app/modules/poi/tests/test_facility_catalog.py`
- Modify: `app/modules/poi/__init__.py` only if package exports are required.

**Interfaces:**
- `FacilityDefinition`: canonical `id`, `group_id`, `name_zh`, `aliases`, `search_terms`, `capabilities`, `metric_profile`, `data_notes`, `basis`.
- `load_facility_catalog() -> FacilityCatalog` validates IDs, aliases, groups, capabilities, and policy references.
- `resolve_facility_mentions(mentions: list[str]) -> (resolved_ids, unresolved_mentions)` is deterministic and case/whitespace normalized.

- [ ] Write failing tests for unique IDs, alias resolution, duplicate aliases, and capability/profile consistency.
- [ ] Run `.venv/bin/python -m pytest app/modules/poi/tests/test_facility_catalog.py -q` and verify expected failures.
- [ ] Add the seven groups and the baseline entries from the spec; mark capabilities explicitly per entry.
- [ ] Run the focused catalog tests and verify all pass.

### Task 2: Generic V2 facility and POI contracts

**Files:**
- Create: `app/schemas/facility_catalog.py`
- Create: `app/schemas/poi_v2.py`
- Test: `tests/test_poi_v2_contracts.py`
- Modify: `app/modules/poi/service.py` only where a shared helper can be reused without changing V1 behavior.

**Interfaces:**
- `FacilityCatalogResultV2` returns groups, canonical IDs, capabilities, and metric-profile summaries.
- `POISearchRequestV2` accepts `facility_ids: list[str]` and `search_radius_m`.
- `POICountV2` stores `facility_id` and `count: int | None`; `None` represents incomplete/unknown, never zero.
- `POIResultV2` includes `source`, `query_terms`, `exploratory`, and classification/data-confidence metadata.

- [ ] Write failing schema tests for an ID beyond the original three, dynamic counts, unknown counts, and the exploratory marker.
- [ ] Run the focused tests and verify expected failures.
- [ ] Implement V2 contracts and catalog-backed validation without changing `app/schemas/poi.py` V1 contracts.
- [ ] Run focused tests and `tests/test_poi_contracts.py`.

### Task 3: Registry-backed POI search and exploratory fallback

**Files:**
- Create: `app/modules/poi/service_v2.py`
- Test: `app/modules/poi/tests/test_poi_service_v2.py`
- Modify: `app/modules/poi/category_mapping.py` only to expose legacy mapping for the V1 adapter; do not replace V1 data.

**Interfaces:**
- `POIServiceV2.search(request: POISearchRequestV2) -> POISearchResultV2` looks up catalog terms, calls the existing provider, deduplicates by Baidu UID then normalized record key, and preserves per-category unknown/partial status.
- `POIServiceV2.search_exploratory(term, center, radius_m) -> POISearchResultV2` returns records marked exploratory and does not emit standard facility counts or metric eligibility.

- [ ] Write failing tests for multi-term deduplication, provider partial failure, confirmed empty versus unavailable, and custom exploratory exclusion from standard counts.
- [ ] Run focused tests and verify expected failures.
- [ ] Implement registry-backed search by composing the existing Baidu provider and the new V2 result schemas.
- [ ] Run the focused POI V2 tests and all existing POI provider/service tests.

### Task 4: V2 Agent extraction and response contract

**Files:**
- Create: `app/schemas/agent_v2.py`
- Create: `app/agents/orchestrator_v2.py`
- Create: `app/agent_runtime/runner_v2.py` or a shared generic runner module if the existing graph can be reused without weakening V1 validation.
- Create: `tests/test_orchestrator_v2.py`
- Create: `tests/test_agent_v2.py`

**Interfaces:**
- V2 Brief stores normalized `facility_ids`, unresolved mentions, intent, and missing fields.
- Catalog aliases are resolved deterministically after model extraction; unknown IDs cannot pass runtime validation.
- Custom explicit facilities use a separate exploratory intent flag and cannot enter standard Diagnosis/Planning.
- V2 RunState reports generic evidence, capability limits, and quality dimensions.

- [ ] Write failing tests for all seven groups, aliases, broad standard-scope requests, ambiguous category clarification, unsupported arbitrary topics, and custom POI exploration.
- [ ] Run focused tests and verify the intended failures.
- [ ] Implement V2 extraction/runtime behavior while retaining V1 `OrchestratorAgent` and `run_agent` semantics.
- [ ] Verify no new facility query triggers Diagnosis unless its intent requests it and the catalog permits it.
- [ ] Run focused V2 Agent tests and existing V1 orchestrator/runtime tests.

### Task 5: Capability-aware Diagnosis and metric quality

**Files:**
- Create: `app/schemas/diagnosis_v2.py`
- Create: `app/modules/diagnosis/service_v2.py`
- Test: `tests/test_diagnosis_v2.py`
- Modify: `app/agents/planning.py` and V2 planning prompt only for registry-scoped claims and capability limitations.

**Interfaces:**
- `FacilityMetricV2` identifies `facility_id`, metric values, applicable profile, evidence quality, and unsupported dimensions.
- `DiagnosisServiceV2` runs only catalog-authorized stages and returns `unavailable`/`unknown` when policy or evidence is missing.
- No universal 900-second/1,000-meter policy is applied to all new categories.

- [ ] Write failing tests for routing-only categories, coverage-enabled categories, missing metric profiles, budget exhaustion, and mixed category quality.
- [ ] Run focused tests and verify expected failures.
- [ ] Implement capability gating, profile-based metrics, and quality dimensions.
- [ ] Add planner tests proving unsupported metrics are named as limits and no unsupported numerical claim appears.
- [ ] Run all diagnosis, blindspot, and planning tests.

### Task 6: Versioned V2 API, V1 compatibility, docs, and offline acceptance

**Files:**
- Create: `app/api/v2.py`
- Modify: `app/api/main.py`
- Modify: `tests/test_api.py`
- Modify: `README.md`, `API_交接说明_2026-09-22.md`, and relevant V2 contract examples.
- Create: `contracts/v2/facility-catalog.example.json`
- Create: `contracts/v2/agent-run-state.example.json`

**Interfaces:**
- `GET /api/v2/facilities` exposes the catalog and capability metadata.
- V2 Agent run/job endpoints mirror V1 resource lifecycle with V2 RunState DTOs.
- `/api/v1` keeps its old request/response shapes and original three categories.

- [ ] Write failing API tests for catalog listing, new IDs through V2, V1 unchanged, V1 unsupported-category clarification, and V2 async job result serialization.
- [ ] Run the API tests and verify expected failures.
- [ ] Register V2 routes using shared providers/services, implement boundary DTO adapters, and document the frontend integration path.
- [ ] Run `.venv/bin/python -m pytest -q -p no:cacheprovider` and `.venv/bin/python scripts/validate_contracts.py` with live keys/flags absent.
- [ ] Inspect generated contract examples and verify no credential, raw provider payload, or unsupported metric is exposed.

## Delivery rule

Do not run the Beijing or any other live Baidu/LLM test during this implementation. After the offline acceptance passes, report the ready state and wait for the user to request live testing.
