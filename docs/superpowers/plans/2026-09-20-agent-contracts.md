# Agent Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the versioned, strictly validated Agent v1 contracts shared by the future Orchestrator, Planning, Reviewer, Gateway, and Runtime layers.

**Architecture:** Keep all Agent v1 public models in `app/schemas/agent.py` and inherit the existing strict `ContractModel`. Reuse Core MVP Tool results as the only fact payloads, identify facts with stable IDs plus serialized JSON Pointers, represent missing evidence structurally, and prevent impossible run-state combinations before Runtime code is written.

**Tech Stack:** Python 3.12, Pydantic 2.x, pytest 8.x, existing Core MVP schemas.

**Spec:** `docs/architecture/15分钟生活圈_Agent_App_Architecture_v1.0_交接文档.md`

## Global Constraints

- Tool 1–6 and their schemas remain unchanged and are the only deterministic fact layer.
- Agent v1 public contracts stay in one file: `app/schemas/agent.py`.
- Agent contract versioning is independent from Core Tool contract versioning through `AgentSchemaVersion = Literal["1.0"]`.
- Unknown fields are forbidden, scalar coercion stays disabled, whitespace is stripped, and assignment remains validated through `ContractModel`.
- `EvidenceRef.json_pointer` always addresses the serialized `EvidenceBundle` JSON. It never contains Pydantic's internal `RootModel.root` attribute.
- An `EvidenceBundle` contains either one high-level Diagnosis result or lower-level Tool results, never both.
- Every planning issue and recommendation has at least one evidence reference.
- Full JSON-Pointer resolution and cross-object reference validation remain the responsibility of future `agent_runtime/validation.py`; Phase 1 validates ID syntax, pointer root, local uniqueness, and fixture consistency.
- `insufficient_evidence` uses structured `EvidenceRequest` values. Agent v1 only permits high-level `diagnosis` supplementation; Runtime reuses the current Brief location/facility types and the existing fixed 900 s, 1000 m, and 200 m Diagnosis thresholds.
- Missing required user information is represented by `RunStatus.WAITING_FOR_INPUT`; it is not treated as failure or as a completed run.
- No LLM, Gateway, API, Baidu Provider, Agent behavior, or Runtime routing is implemented in this phase.
- All tests are forced offline and must not call real Baidu services.
- Run all relative-path commands from `/Users/liuyanhe/Desktop/15分钟生活圈智能体检与规划助手`.

---

### Task 1: Prepare an offline Python environment and establish the baseline

**Files:**
- Modify: `.gitignore`
- No production code changes

**Interfaces:**
- Consumes: `requirements.txt` and the bundled Python 3.12 runtime.
- Produces: a local untracked `.venv/` capable of running the existing offline suite.

- [ ] **Step 1: Confirm the project and interpreter**

Run:

```bash
cd '/Users/liuyanhe/Desktop/15分钟生活圈智能体检与规划助手'
PROJECT_PYTHON='/Users/liuyanhe/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
"$PROJECT_PYTHON" --version
git status --short
```

Expected: Python 3.12.x; the worktree contains only the supplied Agent architecture document as an untracked file.

- [ ] **Step 2: Create the Phase 1 feature branch**

```bash
git switch -c feat/agent-contracts
```

Expected: development continues on `feat/agent-contracts`; local `main` remains the comparison baseline.

- [ ] **Step 3: Ignore the local environment**

Append this exact entry to `.gitignore`:

```gitignore
.venv/
```

- [ ] **Step 4: Create the environment and install declared dependencies**

```bash
"$PROJECT_PYTHON" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Expected: dependency installation succeeds. This is the only step that may need network permission.

- [ ] **Step 5: Run the existing suite with networking disabled**

```bash
env RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest -q -p no:cacheprovider
```

Expected: the existing offline baseline has zero failures and live Baidu tests are skipped. If it fails, stop and report pre-existing failures before writing Agent contracts.

- [ ] **Step 6: Commit the environment rule and architecture spec**

```bash
git add .gitignore docs/architecture/15分钟生活圈_Agent_App_Architecture_v1.0_交接文档.md
git commit -m "docs: add agent architecture handoff"
```

---

### Task 2: Brief and evidence contracts

**Files:**
- Create: `app/schemas/agent.py`
- Create: `contracts/v1/agent-brief.example.json`
- Create: `contracts/v1/agent-evidence.example.json`
- Create: `tests/test_agent_contracts.py`

**Interfaces:**
- Consumes: `ContractModel`, `WarningItem`, `LocationInput`, `FacilityType`, and the six existing Tool result types.
- Produces: `AgentSchemaVersion`, `AgentIntent`, `BriefMissingField`, `MissingInformation`, `EvidenceKind`, `LifeCircleBrief`, `EvidenceRef`, and `EvidenceBundle`.

- [ ] **Step 1: Add valid brief and evidence examples**

Create `contracts/v1/agent-brief.example.json`:

```json
{
  "schema_version": "1.0",
  "intent": "planning_analysis",
  "user_goal": "体检该社区并提出有证据支持的设施改善建议",
  "location": {"type": "address", "query": "上海市虹口区四川北路街道", "city": "上海市"},
  "facility_types": ["market", "pharmacy", "primary_school"],
  "needs_full_diagnosis": true,
  "needs_planning": true,
  "needs_review": true,
  "missing_information": []
}
```

Create `contracts/v1/agent-evidence.example.json`:

```json
{
  "schema_version": "1.0",
  "bundle_id": "evidence:location-center",
  "refs": [{
    "evidence_id": "location:center",
    "kind": "location",
    "json_pointer": "/location/data/center",
    "summary": "中心点已由地址解析得到"
  }],
  "location": {
    "ok": true,
    "data": {
      "center": {"lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"},
      "label": "四川北路街道",
      "formatted_address": "上海市虹口区四川北路街道",
      "source": "baidu_geocoding"
    },
    "warnings": [],
    "meta": {"schema_version": "1.0", "provider": "baidu"}
  },
  "poi": null,
  "routing": null,
  "isochrone": null,
  "blindspot": null,
  "diagnosis": null,
  "warnings": []
}
```

- [ ] **Step 2: Write the first failing contract tests**

Create `tests/test_agent_contracts.py`:

```python
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.agent import EvidenceBundle, LifeCircleBrief


CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "v1"


def load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_brief_example_validates():
    brief = LifeCircleBrief.model_validate(load("agent-brief.example.json"))
    assert brief.intent.value == "planning_analysis"
    assert (brief.needs_full_diagnosis, brief.needs_planning, brief.needs_review) == (True, True, True)


def test_evidence_example_validates_without_root_in_public_pointer():
    evidence = EvidenceBundle.model_validate(load("agent-evidence.example.json"))
    assert evidence.refs[0].json_pointer == "/location/data/center"
    assert "/root/" not in evidence.refs[0].json_pointer
    assert evidence.location.root.ok is True


@pytest.mark.parametrize("field", ["needs_full_diagnosis", "needs_planning", "needs_review"])
def test_planning_intent_requires_all_planning_flags(field):
    payload = load("agent-brief.example.json")
    payload[field] = False
    with pytest.raises(ValidationError):
        LifeCircleBrief.model_validate(payload)


def test_review_cannot_run_without_planning():
    payload = load("agent-brief.example.json")
    payload.update({"intent": "facility_query", "needs_full_diagnosis": False, "needs_planning": False})
    with pytest.raises(ValidationError):
        LifeCircleBrief.model_validate(payload)


def test_missing_location_is_structured():
    payload = load("agent-brief.example.json")
    payload["location"] = None
    payload["missing_information"] = [{"field": "location", "message": "需要社区地址或中心点"}]
    LifeCircleBrief.model_validate(payload)


def test_location_can_coexist_with_other_missing_information():
    payload = load("agent-brief.example.json")
    payload["facility_types"] = []
    payload["missing_information"] = [{"field": "facility_types", "message": "需要设施类别"}]
    LifeCircleBrief.model_validate(payload)


def test_duplicate_facility_types_are_rejected():
    payload = load("agent-brief.example.json")
    payload["facility_types"] = ["market", "market"]
    with pytest.raises(ValidationError):
        LifeCircleBrief.model_validate(payload)


def test_evidence_id_kind_and_pointer_root_must_agree():
    payload = load("agent-evidence.example.json")
    payload["refs"][0]["kind"] = "diagnosis"
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(payload)


def test_evidence_pointer_requires_present_tool_result():
    payload = load("agent-evidence.example.json")
    payload["location"] = None
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(payload)


def test_diagnosis_cannot_be_mixed_with_lower_level_results():
    payload = load("agent-evidence.example.json")
    payload["diagnosis"] = load("diagnosis-result.example.json")
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(payload)


def test_unknown_fields_and_scalar_coercion_are_rejected():
    brief = load("agent-brief.example.json")
    brief["unexpected"] = True
    with pytest.raises(ValidationError):
        LifeCircleBrief.model_validate(brief)
    evidence = load("agent-evidence.example.json")
    evidence["refs"][0]["summary"] = 123
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(evidence)


def test_assignment_validation_is_inherited():
    brief = LifeCircleBrief.model_validate(load("agent-brief.example.json"))
    with pytest.raises(ValidationError):
        brief.user_goal = ""
```

- [ ] **Step 3: Run the focused test and verify red**

```bash
env RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest tests/test_agent_contracts.py -q -p no:cacheprovider
```

Expected: collection fails because `app.schemas.agent` does not exist.

- [ ] **Step 4: Implement brief and evidence models**

Create `app/schemas/agent.py` with:

```python
"""Shared Agent v1 contracts built on deterministic Core MVP results."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.blindspot import BlindspotResult
from app.schemas.common import ContractModel, ErrorDetail, WarningItem
from app.schemas.diagnosis import DiagnosisResult
from app.schemas.isochrone import IsochroneResult
from app.schemas.location import LocationInput, LocationResult
from app.schemas.poi import FacilityType, POISearchResult
from app.schemas.routing import RoutingResult


AgentSchemaVersion = Literal["1.0"]
NonEmptyText = Annotated[str, Field(min_length=1)]
Facility = Annotated[FacilityType, Field(strict=False)]


class AgentIntent(str, Enum):
    FACILITY_QUERY = "facility_query"
    ACCESSIBILITY_QUERY = "accessibility_query"
    BLINDSPOT_QUERY = "blindspot_query"
    COMMUNITY_DIAGNOSIS = "community_diagnosis"
    PLANNING_ANALYSIS = "planning_analysis"


class BriefMissingField(str, Enum):
    LOCATION = "location"
    FACILITY_TYPES = "facility_types"


class EvidenceKind(str, Enum):
    LOCATION = "location"
    POI = "poi"
    ROUTING = "routing"
    ISOCHRONE = "isochrone"
    BLINDSPOT = "blindspot"
    DIAGNOSIS = "diagnosis"


class MissingInformation(ContractModel):
    field: BriefMissingField = Field(strict=False)
    message: NonEmptyText


class LifeCircleBrief(ContractModel):
    schema_version: AgentSchemaVersion
    intent: AgentIntent = Field(strict=False)
    user_goal: NonEmptyText
    location: LocationInput | None = None
    facility_types: list[Facility] = Field(default_factory=list)
    needs_full_diagnosis: bool
    needs_planning: bool
    needs_review: bool
    missing_information: list[MissingInformation] = Field(default_factory=list)

    @model_validator(mode="after")
    def intent_is_actionable(self) -> "LifeCircleBrief":
        if len(set(self.facility_types)) != len(self.facility_types):
            raise ValueError("facility_types 不能重复。")
        missing = [item.field for item in self.missing_information]
        if len(set(missing)) != len(missing):
            raise ValueError("missing_information.field 不能重复。")
        if (self.location is None) != (BriefMissingField.LOCATION in missing):
            raise ValueError("location 与对应 missing_information 必须一致。")
        if (not self.facility_types) != (BriefMissingField.FACILITY_TYPES in missing):
            raise ValueError("facility_types 与对应 missing_information 必须一致。")
        expected = {
            AgentIntent.FACILITY_QUERY: (False, False, False),
            AgentIntent.ACCESSIBILITY_QUERY: (False, False, False),
            AgentIntent.BLINDSPOT_QUERY: (True, False, False),
            AgentIntent.COMMUNITY_DIAGNOSIS: (True, False, False),
            AgentIntent.PLANNING_ANALYSIS: (True, True, True),
        }[self.intent]
        if (self.needs_full_diagnosis, self.needs_planning, self.needs_review) != expected:
            raise ValueError("Intent 与 diagnosis/planning/review 标志不一致。")
        return self


class EvidenceRef(ContractModel):
    evidence_id: str = Field(pattern=r"^(location|poi|routing|isochrone|blindspot|diagnosis):[a-z0-9][a-z0-9._-]*$")
    kind: EvidenceKind = Field(strict=False)
    json_pointer: str = Field(
        min_length=2,
        pattern=r"^/(location|poi|routing|isochrone|blindspot|diagnosis)(?:/(?:[^/~]|~[01])*)*$",
    )
    summary: NonEmptyText

    @model_validator(mode="after")
    def identity_matches_kind(self) -> "EvidenceRef":
        if self.evidence_id.split(":", 1)[0] != self.kind.value:
            raise ValueError("evidence_id 前缀必须与 kind 一致。")
        if self.json_pointer.split("/", 2)[1] != self.kind.value:
            raise ValueError("json_pointer 根必须与 kind 一致。")
        return self


class EvidenceBundle(ContractModel):
    schema_version: AgentSchemaVersion
    bundle_id: str = Field(pattern=r"^evidence:[a-z0-9][a-z0-9._-]*$")
    refs: list[EvidenceRef] = Field(min_length=1)
    location: LocationResult | None = None
    poi: POISearchResult | None = None
    routing: RoutingResult | None = None
    isochrone: IsochroneResult | None = None
    blindspot: BlindspotResult | None = None
    diagnosis: DiagnosisResult | None = None
    warnings: list[WarningItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_is_traceable(self) -> "EvidenceBundle":
        lower_level = (self.location, self.poi, self.routing, self.isochrone, self.blindspot)
        if self.diagnosis is not None and any(item is not None for item in lower_level):
            raise ValueError("Diagnosis 证据不能与其低层 Tool 结果重复出现。")
        if self.diagnosis is None and not any(item is not None for item in lower_level):
            raise ValueError("EvidenceBundle 至少包含一个 Tool Result。")
        if len({item.evidence_id for item in self.refs}) != len(self.refs):
            raise ValueError("evidence_id 不能重复。")
        if any(getattr(self, item.kind.value) is None for item in self.refs):
            raise ValueError("EvidenceRef 必须指向 Bundle 中存在的 Tool Result。")
        if len({(item.code, item.message) for item in self.warnings}) != len(self.warnings):
            raise ValueError("EvidenceBundle warnings 必须去重。")
        return self
```

- [ ] **Step 5: Run Task 2 tests and verify green**

```bash
env RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest tests/test_agent_contracts.py -q -p no:cacheprovider
```

Expected: all Task 2 tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add app/schemas/agent.py tests/test_agent_contracts.py contracts/v1/agent-brief.example.json contracts/v1/agent-evidence.example.json
git commit -m "feat: define agent brief and evidence contracts"
```

---

### Task 3: Planning, review, and structured evidence requests

**Files:**
- Modify: `app/schemas/agent.py`
- Create: `contracts/v1/agent-planning-proposal.example.json`
- Create: `contracts/v1/agent-review-result.example.json`
- Modify: `tests/test_agent_contracts.py`

**Interfaces:**
- Consumes: `EvidenceBundle.bundle_id` and stable `EvidenceRef.evidence_id` values.
- Produces: `PlanningPriority`, `PlanningIssue`, `PlanningRecommendation`, `PlanningProposal`, `EvidenceRequest`, `ReviewStatus`, `ReviewIssueCategory`, `ReviewIssue`, and `ReviewResult`.

- [ ] **Step 1: Add planning and review examples**

Create `contracts/v1/agent-planning-proposal.example.json`:

```json
{
  "schema_version": "1.0",
  "proposal_id": "proposal:market-gap-r1",
  "planning_round": 1,
  "evidence_bundle_id": "evidence:diagnosis-example",
  "summary": "社区基础服务存在需要优先核查的空间短板。",
  "issues": [{
    "issue_id": "issue:market-gap",
    "title": "菜市场服务存在空间缺口",
    "description": "现有证据显示部分分析区域未被菜市场服务覆盖。",
    "priority": "high",
    "facility_type": "market",
    "evidence_refs": ["diagnosis:market-coverage"]
  }],
  "recommendations": [{
    "recommendation_id": "recommendation:market-study",
    "title": "优先开展菜市场补点可行性研究",
    "rationale": "该方向直接对应已识别的菜市场覆盖问题。",
    "priority": "high",
    "actions": ["在已识别服务缺口内筛选候选点位", "补充用地和人口数据后再开展情景模拟"],
    "evidence_refs": ["diagnosis:market-coverage"],
    "limitations": ["当前结果不代表候选点位已具备建设条件"]
  }],
  "limitations": ["建议仅基于当前 Tool 结果，不包含新增设施后的确定性模拟"]
}
```

Create `contracts/v1/agent-review-result.example.json`:

```json
{
  "schema_version": "1.0",
  "status": "approved",
  "reviewed_proposal_id": "proposal:market-gap-r1",
  "reviewed_planning_round": 1,
  "reviewed_evidence_bundle_id": "evidence:diagnosis-example",
  "issues": [],
  "revision_instructions": [],
  "missing_evidence": []
}
```

- [ ] **Step 2: Add failing tests without module-level imports**

Append to `tests/test_agent_contracts.py`:

```python
def diagnosis_evidence_payload() -> dict:
    return {
        "schema_version": "1.0",
        "bundle_id": "evidence:diagnosis-example",
        "refs": [{
            "evidence_id": "diagnosis:market-coverage",
            "kind": "diagnosis",
            "json_pointer": "/diagnosis/data/metrics/0/blind_ratio",
            "summary": "菜市场覆盖比例来自 Diagnosis 指标"
        }],
        "location": None,
        "poi": None,
        "routing": None,
        "isochrone": None,
        "blindspot": None,
        "diagnosis": load("diagnosis-result.example.json"),
        "warnings": []
    }


def test_planning_and_evidence_examples_form_a_closed_fixture():
    from app.schemas.agent import PlanningProposal

    evidence = EvidenceBundle.model_validate(diagnosis_evidence_payload())
    proposal = PlanningProposal.model_validate(load("agent-planning-proposal.example.json"))
    assert proposal.evidence_bundle_id == evidence.bundle_id
    refs = {ref for item in proposal.issues + proposal.recommendations for ref in item.evidence_refs}
    assert refs <= {ref.evidence_id for ref in evidence.refs}


def test_issue_and_recommendation_require_unique_evidence_refs():
    from app.schemas.agent import PlanningProposal

    payload = load("agent-planning-proposal.example.json")
    payload["issues"][0]["evidence_refs"] = []
    with pytest.raises(ValidationError):
        PlanningProposal.model_validate(payload)
    payload = load("agent-planning-proposal.example.json")
    payload["recommendations"][0]["evidence_refs"] *= 2
    with pytest.raises(ValidationError):
        PlanningProposal.model_validate(payload)


def test_planning_ids_are_unique():
    from app.schemas.agent import PlanningProposal

    payload = load("agent-planning-proposal.example.json")
    payload["recommendations"].append(payload["recommendations"][0])
    with pytest.raises(ValidationError):
        PlanningProposal.model_validate(payload)


def test_approved_review_example_validates():
    from app.schemas.agent import ReviewResult

    review = ReviewResult.model_validate(load("agent-review-result.example.json"))
    assert review.status.value == "approved"


def test_revision_requires_issue_or_instruction_and_no_evidence_request():
    from app.schemas.agent import ReviewResult

    payload = load("agent-review-result.example.json")
    payload["status"] = "revision_required"
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)
    payload["revision_instructions"] = ["收窄建议范围"]
    payload["missing_evidence"] = [{
        "request_id": "request:diagnosis-refresh",
        "kind": "diagnosis",
        "reason": "需要新的诊断结果",
        "required_json_pointers": ["/diagnosis/data/metrics"],
        "facility_types": ["market"]
    }]
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)


def test_insufficient_evidence_requires_structured_request():
    from app.schemas.agent import ReviewResult

    payload = load("agent-review-result.example.json")
    payload["status"] = "insufficient_evidence"
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)
    payload["missing_evidence"] = [{
        "request_id": "request:routing-refresh",
        "kind": "routing",
        "reason": "需要新的路径结果",
        "required_json_pointers": ["/routing/data/routes"],
        "facility_types": ["market"]
    }]
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)
    payload["missing_evidence"] = [{
        "request_id": "request:diagnosis-refresh",
        "kind": "diagnosis",
        "reason": "需要新的诊断结果",
        "required_json_pointers": ["/location/data/center"],
        "facility_types": ["market"]
    }]
    with pytest.raises(ValidationError):
        ReviewResult.model_validate(payload)
```

- [ ] **Step 3: Run focused tests and verify red**

```bash
env RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest tests/test_agent_contracts.py -q -p no:cacheprovider
```

Expected: Task 2 tests run and pass; new Task 3 tests fail when their function-local imports cannot find the new models.

- [ ] **Step 4: Implement planning and review models**

Append to `app/schemas/agent.py`:

```python
class PlanningPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PlanningIssue(ContractModel):
    issue_id: str = Field(pattern=r"^issue:[a-z0-9][a-z0-9._-]*$")
    title: NonEmptyText
    description: NonEmptyText
    priority: PlanningPriority = Field(strict=False)
    facility_type: Facility | None = None
    evidence_refs: list[NonEmptyText] = Field(min_length=1)


class PlanningRecommendation(ContractModel):
    recommendation_id: str = Field(pattern=r"^recommendation:[a-z0-9][a-z0-9._-]*$")
    title: NonEmptyText
    rationale: NonEmptyText
    priority: PlanningPriority = Field(strict=False)
    actions: list[NonEmptyText] = Field(min_length=1)
    evidence_refs: list[NonEmptyText] = Field(min_length=1)
    limitations: list[NonEmptyText] = Field(default_factory=list)


class PlanningProposal(ContractModel):
    schema_version: AgentSchemaVersion
    proposal_id: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    planning_round: int = Field(ge=1)
    evidence_bundle_id: str = Field(pattern=r"^evidence:[a-z0-9][a-z0-9._-]*$")
    summary: NonEmptyText
    issues: list[PlanningIssue] = Field(min_length=1)
    recommendations: list[PlanningRecommendation] = Field(min_length=1)
    limitations: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def identifiers_and_refs_are_unique(self) -> "PlanningProposal":
        if len({item.issue_id for item in self.issues}) != len(self.issues):
            raise ValueError("issue_id 不能重复。")
        if len({item.recommendation_id for item in self.recommendations}) != len(self.recommendations):
            raise ValueError("recommendation_id 不能重复。")
        for item in [*self.issues, *self.recommendations]:
            if len(set(item.evidence_refs)) != len(item.evidence_refs):
                raise ValueError("单个规划项的 evidence_refs 不能重复。")
        return self


class EvidenceRequest(ContractModel):
    request_id: str = Field(pattern=r"^request:[a-z0-9][a-z0-9._-]*$")
    kind: Literal["diagnosis"]
    reason: NonEmptyText
    required_json_pointers: list[NonEmptyText] = Field(min_length=1)
    facility_types: list[Facility] = Field(min_length=1)

    @model_validator(mode="after")
    def requested_paths_match_kind(self) -> "EvidenceRequest":
        if len(set(self.required_json_pointers)) != len(self.required_json_pointers):
            raise ValueError("required_json_pointers 不能重复。")
        if len(set(self.facility_types)) != len(self.facility_types):
            raise ValueError("EvidenceRequest facility_types 不能重复。")
        if any(not pointer.startswith("/diagnosis/") for pointer in self.required_json_pointers):
            raise ValueError("Agent v1 补证据只能请求 Diagnosis JSON Pointer。")
        return self


class ReviewStatus(str, Enum):
    APPROVED = "approved"
    REVISION_REQUIRED = "revision_required"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ReviewIssueCategory(str, Enum):
    UNSUPPORTED_FACT = "unsupported_fact"
    NUMERIC_MISMATCH = "numeric_mismatch"
    OVERREACH = "overreach"
    WARNING_IGNORED = "warning_ignored"
    ABSENCE_OVERSTATED = "absence_overstated"
    PROBLEM_MISMATCH = "problem_mismatch"


class ReviewIssue(ContractModel):
    issue_id: str = Field(pattern=r"^review:[a-z0-9][a-z0-9._-]*$")
    category: ReviewIssueCategory = Field(strict=False)
    message: NonEmptyText
    recommendation_ids: list[NonEmptyText] = Field(default_factory=list)
    evidence_refs: list[NonEmptyText] = Field(default_factory=list)


class ReviewResult(ContractModel):
    schema_version: AgentSchemaVersion
    status: ReviewStatus = Field(strict=False)
    reviewed_proposal_id: str = Field(pattern=r"^proposal:[a-z0-9][a-z0-9._-]*$")
    reviewed_planning_round: int = Field(ge=1)
    reviewed_evidence_bundle_id: str = Field(pattern=r"^evidence:[a-z0-9][a-z0-9._-]*$")
    issues: list[ReviewIssue]
    revision_instructions: list[NonEmptyText]
    missing_evidence: list[EvidenceRequest]

    @model_validator(mode="after")
    def status_matches_payload(self) -> "ReviewResult":
        if len({item.issue_id for item in self.issues}) != len(self.issues):
            raise ValueError("Review issue_id 不能重复。")
        if len({item.request_id for item in self.missing_evidence}) != len(self.missing_evidence):
            raise ValueError("EvidenceRequest request_id 不能重复。")
        if self.status is ReviewStatus.APPROVED and (self.issues or self.revision_instructions or self.missing_evidence):
            raise ValueError("approved 不能携带问题、修改要求或缺失证据。")
        if self.status is ReviewStatus.REVISION_REQUIRED and not (self.issues or self.revision_instructions):
            raise ValueError("revision_required 必须说明问题或修改要求。")
        if self.status is ReviewStatus.REVISION_REQUIRED and self.missing_evidence:
            raise ValueError("需要补证据时应使用 insufficient_evidence。")
        if self.status is ReviewStatus.INSUFFICIENT_EVIDENCE and not self.missing_evidence:
            raise ValueError("insufficient_evidence 必须包含结构化 EvidenceRequest。")
        return self
```

- [ ] **Step 5: Run Task 3 tests and verify green**

```bash
env RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest tests/test_agent_contracts.py -q -p no:cacheprovider
```

Expected: all Task 2–3 tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add app/schemas/agent.py tests/test_agent_contracts.py contracts/v1/agent-planning-proposal.example.json contracts/v1/agent-review-result.example.json
git commit -m "feat: define planning and review contracts"
```

---

### Task 4: Validated runtime state and public exports

**Files:**
- Modify: `app/schemas/agent.py`
- Modify: `app/schemas/__init__.py`
- Create: `contracts/v1/agent-run-state.example.json`
- Modify: `tests/test_agent_contracts.py`

**Interfaces:**
- Consumes: all Agent contracts from Tasks 2–3 plus existing `ErrorDetail` and `WarningItem`.
- Produces: `RunStatus`, `CompletionMode`, and `AgentRunState`; exports every Agent contract from `app.schemas`.

- [ ] **Step 1: Add an initialized run-state example**

Create `contracts/v1/agent-run-state.example.json`:

```json
{
  "schema_version": "1.0",
  "run_id": "run:20260920-0001",
  "status": "pending",
  "completion_mode": null,
  "user_message": "请体检四川北路街道并提出规划建议",
  "brief": null,
  "evidence": null,
  "planning_proposal": null,
  "review_result": null,
  "planning_round": 0,
  "tool_retry_count": 0,
  "warnings": [],
  "errors": []
}
```

- [ ] **Step 2: Add failing state tests with function-local imports**

Append:

```python
def completed_state_payload() -> dict:
    payload = load("agent-run-state.example.json")
    payload.update({
        "status": "completed",
        "completion_mode": "normal",
        "brief": load("agent-brief.example.json"),
        "evidence": diagnosis_evidence_payload(),
        "planning_proposal": load("agent-planning-proposal.example.json"),
        "review_result": load("agent-review-result.example.json"),
        "planning_round": 1
    })
    return payload


def test_initialized_run_state_example_validates():
    from app.schemas.agent import AgentRunState, RunStatus

    state = AgentRunState.model_validate(load("agent-run-state.example.json"))
    assert state.status is RunStatus.PENDING


def test_missing_brief_information_has_explicit_waiting_state():
    from app.schemas.agent import AgentRunState, RunStatus

    brief = load("agent-brief.example.json")
    brief["location"] = None
    brief["missing_information"] = [{"field": "location", "message": "需要社区地址或中心点"}]
    payload = load("agent-run-state.example.json")
    payload.update({"status": "waiting_for_input", "brief": brief})
    state = AgentRunState.model_validate(payload)
    assert state.status is RunStatus.WAITING_FOR_INPUT
    payload["status"] = "running"
    with pytest.raises(ValidationError):
        AgentRunState.model_validate(payload)


@pytest.mark.parametrize("missing", ["brief", "evidence"])
def test_completed_run_requires_brief_and_evidence(missing):
    from app.schemas.agent import AgentRunState

    payload = completed_state_payload()
    payload[missing] = None
    with pytest.raises(ValidationError):
        AgentRunState.model_validate(payload)


def test_proposal_requires_matching_evidence_and_round():
    from app.schemas.agent import AgentRunState

    payload = completed_state_payload()
    payload.update({"status": "running", "completion_mode": None, "review_result": None, "planning_round": 2})
    with pytest.raises(ValidationError):
        AgentRunState.model_validate(payload)


def test_review_requires_matching_proposal_identity():
    from app.schemas.agent import AgentRunState

    payload = completed_state_payload()
    payload["review_result"]["reviewed_proposal_id"] = "proposal:wrong-r1"
    with pytest.raises(ValidationError):
        AgentRunState.model_validate(payload)


def test_failed_run_requires_error_and_pending_requires_zero_counters():
    from app.schemas.agent import AgentRunState

    failed = load("agent-run-state.example.json")
    failed["status"] = "failed"
    with pytest.raises(ValidationError):
        AgentRunState.model_validate(failed)
    pending = load("agent-run-state.example.json")
    pending["tool_retry_count"] = 1
    with pytest.raises(ValidationError):
        AgentRunState.model_validate(pending)


def test_all_agent_contracts_are_publicly_exported():
    import app.schemas as schemas

    expected = {
        "AgentIntent", "AgentRunState", "AgentSchemaVersion", "BriefMissingField",
        "CompletionMode", "EvidenceBundle", "EvidenceKind", "EvidenceRef",
        "EvidenceRequest", "LifeCircleBrief", "MissingInformation", "PlanningIssue",
        "PlanningPriority", "PlanningProposal", "PlanningRecommendation", "ReviewIssue",
        "ReviewIssueCategory", "ReviewResult", "ReviewStatus", "RunStatus"
    }
    assert expected <= set(schemas.__all__)
    assert all(hasattr(schemas, name) for name in expected)
```

- [ ] **Step 3: Run focused tests and verify red**

```bash
env RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest tests/test_agent_contracts.py -q -p no:cacheprovider
```

Expected: earlier tests run and pass; new state/export tests fail because the models and exports do not exist.

- [ ] **Step 4: Implement run-state invariants**

Append to `app/schemas/agent.py`:

```python
class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    COMPLETED = "completed"
    FAILED = "failed"


class CompletionMode(str, Enum):
    NORMAL = "normal"
    WITH_LIMITATIONS = "with_limitations"


class AgentRunState(ContractModel):
    schema_version: AgentSchemaVersion
    run_id: str = Field(pattern=r"^run:[a-z0-9][a-z0-9._-]*$")
    status: RunStatus = Field(strict=False)
    completion_mode: Annotated[CompletionMode, Field(strict=False)] | None = None
    user_message: NonEmptyText
    brief: LifeCircleBrief | None = None
    evidence: EvidenceBundle | None = None
    planning_proposal: PlanningProposal | None = None
    review_result: ReviewResult | None = None
    planning_round: int = Field(default=0, ge=0)
    tool_retry_count: int = Field(default=0, ge=0)
    warnings: list[WarningItem] = Field(default_factory=list)
    errors: list[ErrorDetail] = Field(default_factory=list)

    @model_validator(mode="after")
    def state_is_reachable(self) -> "AgentRunState":
        outputs = (self.brief, self.evidence, self.planning_proposal, self.review_result)
        if self.status is RunStatus.PENDING and (
            any(item is not None for item in outputs)
            or self.planning_round != 0
            or self.tool_retry_count != 0
            or self.completion_mode is not None
            or self.warnings
            or self.errors
        ):
            raise ValueError("pending Run 必须保持初始化状态。")
        if self.status is RunStatus.FAILED and not self.errors:
            raise ValueError("failed Run 必须包含 error。")
        if self.status is not RunStatus.FAILED and self.errors:
            raise ValueError("非 failed Run 不能包含 error。")
        if self.status is RunStatus.WAITING_FOR_INPUT and (
            self.brief is None
            or not self.brief.missing_information
            or any(item is not None for item in (self.evidence, self.planning_proposal, self.review_result))
            or self.planning_round != 0
            or self.tool_retry_count != 0
        ):
            raise ValueError("waiting_for_input 必须只包含带缺失信息的 Brief。")
        if self.brief is not None and self.brief.missing_information and self.status not in {
            RunStatus.WAITING_FOR_INPUT,
            RunStatus.FAILED,
        }:
            raise ValueError("存在 missing_information 时 Run 必须等待用户输入。")
        if self.status is RunStatus.COMPLETED and self.completion_mode is None:
            raise ValueError("completed Run 必须声明 completion_mode。")
        if self.status is not RunStatus.COMPLETED and self.completion_mode is not None:
            raise ValueError("只有 completed Run 可以声明 completion_mode。")
        if self.evidence is not None and self.brief is None:
            raise ValueError("Evidence 必须依赖 Brief。")
        if self.evidence is not None and self.brief.missing_information:
            raise ValueError("缺少必要用户信息时不能获取 Evidence。")
        if self.planning_proposal is not None and (self.brief is None or self.evidence is None):
            raise ValueError("PlanningProposal 必须依赖 Brief 和 Evidence。")
        if self.review_result is not None and self.planning_proposal is None:
            raise ValueError("ReviewResult 必须依赖 PlanningProposal。")
        if self.planning_proposal is not None and (
            not self.brief.needs_planning
            or self.planning_proposal.planning_round != self.planning_round
            or self.planning_proposal.evidence_bundle_id != self.evidence.bundle_id
        ):
            raise ValueError("PlanningProposal 与 Brief、round 或 Evidence 不一致。")
        if self.review_result is not None and (
            self.review_result.reviewed_proposal_id != self.planning_proposal.proposal_id
            or self.review_result.reviewed_planning_round != self.planning_round
            or self.review_result.reviewed_evidence_bundle_id != self.evidence.bundle_id
        ):
            raise ValueError("ReviewResult 必须绑定当前 Proposal、round 和 Evidence。")
        if self.brief is not None and not self.brief.needs_planning and (
            self.planning_round != 0 or self.planning_proposal is not None or self.review_result is not None
        ):
            raise ValueError("非规划任务不能携带规划或审查状态。")
        if self.status is RunStatus.COMPLETED:
            if self.brief is None or self.evidence is None:
                raise ValueError("completed Run 必须包含 Brief 和 Evidence。")
            if self.brief.needs_planning:
                if self.planning_proposal is None or self.review_result is None:
                    raise ValueError("规划任务完成前必须生成 Proposal 和 Review。")
                approved = self.review_result.status is ReviewStatus.APPROVED
                if (self.completion_mode is CompletionMode.NORMAL) != approved:
                    raise ValueError("normal 完成要求 approved；未通过只能带限制完成。")
            elif self.completion_mode is not CompletionMode.NORMAL:
                raise ValueError("非规划任务只能 normal 完成。")
        if len({(item.code, item.message) for item in self.warnings}) != len(self.warnings):
            raise ValueError("Run warnings 必须去重。")
        return self
```

- [ ] **Step 5: Export every Agent contract**

Add all names asserted by `test_all_agent_contracts_are_publicly_exported` to the imports and `__all__` list in `app/schemas/__init__.py`, preserving alphabetical order.

- [ ] **Step 6: Run Task 4 tests and verify green**

```bash
env RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest tests/test_agent_contracts.py -q -p no:cacheprovider
```

Expected: all Agent contract tests pass.

- [ ] **Step 7: Commit Task 4**

```bash
git add app/schemas/agent.py app/schemas/__init__.py tests/test_agent_contracts.py contracts/v1/agent-run-state.example.json
git commit -m "feat: add validated agent run state"
```

---

### Task 5: Contract validator, README, and full offline regression

**Files:**
- Modify: `scripts/validate_contracts.py`
- Modify: `README.md`
- Test: all existing offline tests plus `tests/test_agent_contracts.py`

**Interfaces:**
- Consumes: all Agent v1 examples and models.
- Produces: one offline validation entry point covering Core Tool 1–6 and Agent v1.

- [ ] **Step 1: Prove the current validator does not cover Agent examples**

```bash
.venv/bin/python scripts/validate_contracts.py
```

Expected: it exits 0 but prints only `Core MVP Tool 1-6 v1 contracts validated successfully.`

- [ ] **Step 2: Extend the lightweight validator**

Import:

```python
from app.schemas.agent import (
    AgentRunState,
    EvidenceBundle,
    LifeCircleBrief,
    PlanningProposal,
    ReviewResult,
)
```

After Diagnosis validation, add:

```python
LifeCircleBrief.model_validate(load("agent-brief.example.json"))
EvidenceBundle.model_validate(load("agent-evidence.example.json"))
PlanningProposal.model_validate(load("agent-planning-proposal.example.json"))
ReviewResult.model_validate(load("agent-review-result.example.json"))
AgentRunState.model_validate(load("agent-run-state.example.json"))
```

Replace the final message with:

```python
print("Core MVP Tool 1-6 and Agent v1 contracts validated successfully.")
```

- [ ] **Step 3: Document the new phase without rewriting the Core baseline**

In `README.md`, add after `当前内容`:

```markdown
### Agent v1 Phase 1

Agent 层已进入 Contract 阶段：`app/schemas/agent.py` 定义 Brief、Evidence、Planning、Review 与 Run State 的共享结构，示例位于 `contracts/v1/`。这一阶段只建立数据契约，不实现 Agent 行为、流程路由、真实 LLM 调用或 MVP Gateway；Tool 1–6 仍是唯一的确定性事实层。
```

Keep `docs/architecture/core-mvp.md` unchanged; it remains the historical Core MVP boundary document.

- [ ] **Step 4: Run contract validation**

```bash
.venv/bin/python scripts/validate_contracts.py
```

Expected: `Core MVP Tool 1-6 and Agent v1 contracts validated successfully.`

- [ ] **Step 5: Run the complete suite with networking forced off**

```bash
env RUN_BAIDU_SMOKE=0 BAIDU_MAP_AK= .venv/bin/python -m pytest -q -p no:cacheprovider
```

Expected: zero failures and live Baidu tests skipped.

- [ ] **Step 6: Review the complete Phase 1 diff**

Run:

```bash
git status --short
git diff --check main
git diff --stat main
git log --oneline main..HEAD
```

Expected: changes are limited to the architecture handoff, `.gitignore`, Agent schemas/examples/tests/exports, validator, and README. No Tool, Provider, API, Agent behavior, Gateway, or Runtime implementation file changed.

- [ ] **Step 7: Commit Task 5**

```bash
git add scripts/validate_contracts.py README.md
git commit -m "docs: document agent contract phase"
```
