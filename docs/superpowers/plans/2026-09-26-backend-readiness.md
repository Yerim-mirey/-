# Backend Product Readiness Implementation Plan

> **For agentic workers:** Execute this plan task-by-task. Preserve the order below; each task is independently reviewable.

**Goal:** Make the existing backend safe and understandable for frontend integration and a controlled competition demo without replacing the six Core tools or changing their frozen contracts.

**Architecture:** Keep the synchronous Agent endpoint for compatibility and add a bounded, single-process job API for long runs. Preserve existing Tool v1 and AgentRunState semantics; improve consumer documentation, acceptance scripts, error observability, and deployment automation around them.

**Tech Stack:** FastAPI, Pydantic v2, Python 3.12, pytest, GitHub Actions, Docker Compose.

**Spec:** `docs/DeepSeek接手交接_截至AgentPhase9_2026-09-22.md` sections 6.C and 7, interpreted against current code and the user-approved review sequence in the task conversation.

## Global Constraints

- Keep `POST /api/v1/agent/runs` synchronous and response-compatible.
- Do not alter Core Tool 1–6 contracts or the shared AgentRunState v1 schema.
- Add job-specific API DTOs under `app/schemas/api.py`; `queued` belongs to the job API, not Runtime `RunStatus`.
- The job manager is a bounded single-process demo facility; process restart loses jobs/results; do not claim cancellation.
- Preserve server-only credentials; never log prompts, AKs, model keys, request URLs, or provider response bodies.
- Preserve the semantic distinction among `completion_mode`, confidence, stage status, unknown ratios, and warnings.
- All automated tests and CI remain offline; real Baidu/DeepSeek calls stay behind explicit live flags.

---

### Task 1: Bounded Agent job submission and polling API

**Files:**
- Modify: `app/schemas/api.py`
- Create: `app/api/agent_jobs.py`
- Modify: `app/api/main.py`
- Test: `tests/test_api.py`
- Modify: `README.md`

**Interfaces:**
- `POST /api/v1/agent/jobs` accepts the existing `AgentRunRequest`, returns HTTP 202 with `job_id`, `run_id`, initial `status`, and `status_url`.
- `GET /api/v1/agent/jobs/{job_id}` returns a job DTO with `queued|running|completed|waiting_for_input|failed`, timestamps, and the final `AgentRunState` when available.
- Keep existing `POST /api/v1/agent/runs` unchanged.
- Use one worker and a bounded capacity of at most one running plus one queued job; reject overflow with 429. Keep completed results for a documented short TTL and bound total retained records. Unknown/expired IDs return 404. No cancellation endpoint.

- [ ] Add request/response schema definitions without extending Runtime enums.
- [ ] Add the locked job registry, one-worker executor, bounded admission, state transitions, TTL cleanup, and sanitized runner-exception handling.
- [ ] Register both routes and ensure polling responses disable caching.
- [ ] Document request, response, capacity, process-restart, and no-cancellation behavior.
- [ ] Add offline API checks for queued/running/final transitions, capacity overflow, unknown ID, and runner failure.
- [ ] Run focused API checks and contract validation.

### Task 2: Clarify data-quality semantics for API consumers

**Files:**
- Modify: `README.md`
- Modify: `docs/DeepSeek接手交接_截至AgentPhase9_2026-09-22.md`
- Modify: `docs/test-report/phase10-semantic-sampling-supplement-2026-09-24.md`

**Interfaces:** No schema or Tool algorithm change.

- [ ] State that `completion_mode=normal` means the planning/review flow was approved, not that every evidence source is complete.
- [ ] State that `unknown_ratio=0` means no grid was classified as unknown; it does not assert every route succeeded.
- [ ] Require clients to display non-empty warnings alongside stage status, confidence, and per-facility ratios.
- [ ] Record that a derived completeness field, if later needed, requires a separately versioned API DTO.

### Task 3: Correct Phase 10 scorer and live routing probe

**Files:**
- Modify: `scripts/sample_planning_quality.py`
- Modify: `scripts/verify_routing_live.py`
- Test: `tests/test_planning_quality_scorer.py`
- Test: `tests/test_phase10_acceptance_checks.py`

**Interfaces:** Keep the eight rubric checks and JSON output fields unchanged.

- [ ] Recognize truthful inequality language such as “90% 以上” by checking it against the reported metric values rather than requiring exact equality.
- [ ] Recognize semantically explicit partial-data caveats such as unavailable walking routes while still rejecting unrelated caveats.
- [ ] Ensure A8 uses the corrected decisive A5/A6 outcome, so it does not inherit a false A5 failure.
- [ ] Install the same IPv4-only networking setup in the standalone routing probe that production provider construction uses.
- [ ] Add targeted offline regressions for the observed S2/S3 false positives and inspect their outputs.

### Task 4: Safe diagnostics and single-process operating limits

**Files:**
- Modify: `app/api/main.py`
- Modify: `README.md`
- Modify: `.env.example` only if a new documented limit is added

- [ ] Log sanitized endpoint/run identifiers and exception class for unexpected failures without logging user text, credentials, URLs, or provider bodies.
- [ ] Make the demo’s supported process model explicit: one API worker/instance while using the in-process quota guard and job manager.
- [ ] Document that multiple workers/replicas each have a separate quota guard; shared distributed rate limiting is deferred until deployment needs multiple processes.
- [ ] Keep explicit-origin CORS behavior and document the production `CORS_ORIGINS` value.
- [ ] Add a release note to rotate the Baidu AK before sharing/deployment if the historically exposed key has not already been rotated; do not print or request the key.

### Task 5: Reproducible CI and deployment handoff

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `compose.yaml`
- Modify: `README.md`
- Modify: old handoff snapshots to mark them historical and link current status

- [ ] CI installs the pinned Python runtime dependencies, runs contract validation, and runs offline pytest only.
- [ ] Docker image runs the FastAPI app as a non-root user; `.dockerignore` excludes `.env`, `.venv`, Git data, local cache, and local demo files.
- [ ] Compose passes secrets only through an untracked local `.env` and publishes the API port for local demo use.
- [ ] README gives one local and one Compose startup path, exact API paths, CORS setup, and current status links.
- [ ] Verify the image/compose configuration where Docker is available; never run live Baidu or DeepSeek calls in CI.

### Final review

- [ ] Run the affected offline tests, full offline pytest suite, and `scripts/validate_contracts.py`.
- [ ] Check `git diff --check`, inspect all changed files, and scan tracked changes for secrets.
- [ ] Report any unavailable Docker/CI execution as unverified rather than passing.
