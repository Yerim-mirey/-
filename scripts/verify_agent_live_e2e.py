"""Agent Phase 10: bounded, real end-to-end acceptance over the full stack.

This is the only entry point that spends real Baidu quota *and* real DeepSeek
requests through the same path a user would take:

    run_agent(...) -> MVPToolGateway -> Core Tool 1-6 -> Baidu Web Service
                   -> DeepSeekLLM -> Orchestrator / Planning / Reviewer

It is deliberately not a test-suite member: it never runs in CI, it is gated
by explicit flags, and it prints only sanitized evidence (no API key, no raw
Baidu payload, no full prompt, no model chain-of-thought).

Modes
-----
``probe``
    One geocoding call. Distinguishes "AK / IP whitelist not active" from a
    real chain fault, for almost no quota. Run this first.
``acceptance``
    The fixed case matrix. Each case walks to a terminal ``AgentRunState`` and
    is checked for status, completion mode, and evidence-reference validity.
``agent "<message>"``
    One ad-hoc message through the same path.

``--self-check``
    The same run/check/report path with deterministic Tool results and a fixed
    Brief. No quota, no network, no credentials: use it to confirm this script
    is wired correctly before spending anything real.

Usage
-----
    set -a; source .env; set +a
    RUN_BAIDU_SMOKE=1 RUN_DEEPSEEK_SMOKE=1 \
      python -m scripts.verify_agent_live_e2e probe
    RUN_BAIDU_SMOKE=1 RUN_DEEPSEEK_SMOKE=1 \
      python -m scripts.verify_agent_live_e2e acceptance --include-diagnosis

Budget notes
------------
`acceptance` without `--include-diagnosis` sends no full Diagnosis, so its
route-target count stays in the low tens. A single Diagnosis is ~1600 route
targets at the free-tier 3/second budget, i.e. roughly ten minutes of pacing
on its own; that is why it is opt-in.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.agent_runtime.runner import run_agent
from app.agent_tools.mvp_gateway import MVPToolGateway
from app.agents.orchestrator import OrchestratorAgent
from app.agents.planning import PlanningAgent
from app.agents.reviewer import ReviewerAgent
from app.providers.baidu.geocoding import BaiduGeocodingError, BaiduGeocodingProvider
from app.providers.baidu.live import build_live_providers
from app.providers.baidu.net import install_ipv4_only
from app.providers.baidu.quota import snapshot
from app.providers.deepseek import DeepSeekLLM, api_key_from_env
from app.schemas.agent import AgentRunState, RunStatus
from app.schemas.location import LocationErrorCode


# A fixed, real Shanghai centre: the same one the 2026-09-22 Tool acceptance
# used, so the two records are comparable.
FIXED_CENTER = {"lng": 121.506123, "lat": 31.282456, "crs": "BD09LL"}
FIXED_ADDRESS = "上海市虹口区四川北路街道"


@dataclass(frozen=True, slots=True)
class Case:
    """One fixed acceptance case."""

    case_id: str
    message: str
    expect_status: RunStatus
    expect_planning: bool
    expect_review: bool
    needs_diagnosis: bool = False
    note: str = ""


CASES: tuple[Case, ...] = (
    Case(
        case_id="poi-address",
        message=f"请查询{FIXED_ADDRESS}附近的菜市场、药店和小学",
        expect_status=RunStatus.COMPLETED,
        expect_planning=False,
        expect_review=False,
        note="地址输入；设施查询；不进入 Planning",
    ),
    Case(
        case_id="walk-time-coordinate",
        message=(
            "我的位置是 BD09LL 坐标 121.506123,31.282456，"
            "步行 15 分钟内能到哪些菜市场？"
        ),
        expect_status=RunStatus.COMPLETED,
        expect_planning=False,
        expect_review=False,
        note="坐标输入；可达性提问；需要 Routing 事实",
    ),
    Case(
        case_id="planning-coordinate",
        message=(
            "我的位置是 BD09LL 坐标 121.506123,31.282456，"
            "菜市场、药店和小学配置是否合理？请给出改善规划建议"
        ),
        expect_status=RunStatus.COMPLETED,
        expect_planning=True,
        expect_review=True,
        needs_diagnosis=True,
        note="坐标输入；规划路径；需要 Diagnosis + Reviewer",
    ),
    Case(
        case_id="missing-location",
        message="帮我看看附近的菜市场和药店",
        expect_status=RunStatus.WAITING_FOR_INPUT,
        expect_planning=False,
        expect_review=False,
        note="缺地点；必须停在 waiting_for_input 且零 Tool 调用",
    ),
)


# --- Self-check: exercise this script without spending anything ------------

SELF_CHECK_CASE = Case(
    case_id="self-check",
    message=f"请查询{FIXED_ADDRESS}附近的菜市场、药店和小学",
    expect_status=RunStatus.COMPLETED,
    expect_planning=False,
    expect_review=False,
    note="离线自检：用确定性结果验证脚本自身的检查与报告逻辑",
)


class SelfCheckGateway:
    """Deterministic ToolGateway so `--self-check` can verify this script.

    It is NOT a test of the Core Tools; plenty of tests do that. Its only job
    is to let the acceptance run logic, the state checkers and the report
    writer execute end to end with no quota, no network and no credentials,
    so a wiring mistake surfaces before a real run rather than during one.
    """

    def __init__(self, center: dict) -> None:
        self.center = dict(center)
        self.calls: list[str] = []

    def resolve_location(self, request):
        from app.schemas.location import LocationResult

        self.calls.append("resolve_location")
        return LocationResult.model_validate({
            "ok": True,
            "data": {
                "center": self.center,
                "label": "四川北路街道",
                "formatted_address": FIXED_ADDRESS,
                "source": "baidu_geocoding",
            },
            "warnings": [],
            "meta": {"schema_version": "1.0", "provider": "baidu"},
        })

    def search_pois(self, request):
        from app.schemas.poi import POISearchResult

        self.calls.append("search_pois")
        center = self.center
        points = [
            {
                "poi_id": f"self:{facility}",
                "name": f"自检{facility}",
                "facility_type": facility,
                "location": {"lng": center["lng"] + 0.001, "lat": center["lat"], "crs": "BD09LL"},
                "address": None,
                "source": "baidu",
            }
            for facility in request.facility_types
        ]
        return POISearchResult.model_validate({
            "ok": True,
            "data": {
                "center": center,
                "pois": points,
                "counts": {facility: 1 for facility in request.facility_types},
            },
            "warnings": [],
            "meta": {"schema_version": "1.0", "provider": "baidu"},
        })

    def calculate_walking_times(self, request):
        from app.schemas.routing import RoutingResult

        self.calls.append("calculate_walking_times")
        routes = [
            {
                "target_id": target.target_id,
                "location": target.location.model_dump(),
                "status": "success",
                "distance_m": 200,
                "duration_s": 150,
            }
            for target in request.targets
        ]
        return RoutingResult.model_validate({
            "ok": True,
            "data": {
                "origin": request.origin.model_dump(),
                "travel_mode": "walking",
                "routes": routes,
                "summary": {
                    "requested": len(routes),
                    "success": len(routes),
                    "no_route": 0,
                    "unavailable": 0,
                },
            },
            "warnings": [],
            "meta": {"schema_version": "1.0", "provider": "baidu"},
        })

    def generate_isochrone(self, request):
        raise AssertionError("self-check 案例不应生成等时圈")

    def detect_blindspots(self, request):
        raise AssertionError("self-check 案例不应分析盲区")

    def diagnose_community(self, request):
        raise AssertionError("self-check 案例不应执行完整体检")


class SelfCheckLLM:
    """A StructuredLLM returning one fixed, schema-valid Brief."""

    def generate_object(self, *, system_prompt, user_message, response_schema):
        return {
            "intent": "facility_query",
            "user_goal": "了解社区设施分布",
            "location": {"type": "address", "query": FIXED_ADDRESS, "city": "上海市"},
            "facility_types": ["market", "pharmacy", "primary_school"],
            "use_standard_facilities": False,
        }


class Recorder:
    """Sanitized per-case observation, safe to write into the report."""

    def __init__(self, case_id: str, message: str) -> None:
        self.case_id = case_id
        self.message = message
        self.status: str | None = None
        self.completion_mode: str | None = None
        self.intent: str | None = None
        self.facility_types: list[str] = []
        self.missing_fields: list[str] = []
        self.planning_round = 0
        self.tool_retry_count = 0
        self.evidence_bundle_id: str | None = None
        self.evidence_ref_count = 0
        self.evidence_kinds: list[str] = []
        self.proposal_id: str | None = None
        self.review_status: str | None = None
        self.review_issue_count = 0
        self.warning_codes: list[str] = []
        self.error_codes: list[str] = []
        self.error_messages: list[str] = []
        self.problems: list[str] = []
        self.seconds = 0.0
        self.route_targets_before = 0
        self.route_targets_after = 0
        self.checks: list[tuple[str, bool, str]] = []

    def add_check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, bool(ok), detail))
        if not ok:
            self.problems.append(f"{name}: {detail}" if detail else name)

    @property
    def passed(self) -> bool:
        return not self.problems

    @property
    def route_targets(self) -> int:
        return self.route_targets_after - self.route_targets_before


def resolve_pointer(document: Any, pointer: str) -> tuple[bool, Any]:
    """Resolve a JSON Pointer (RFC 6901) against a plain JSON value."""
    if pointer == "":
        return True, document
    if not pointer.startswith("/"):
        return False, None
    current = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not token.isdigit():
                return False, None
            index = int(token)
            if index >= len(current):
                return False, None
            current = current[index]
        elif isinstance(current, dict):
            if token not in current:
                return False, None
            current = current[token]
        else:
            return False, None
    return True, current


def check_evidence(state: AgentRunState, recorder: Recorder) -> None:
    """Every EvidenceRef pointer must resolve inside the current bundle."""
    bundle = state.evidence
    if bundle is None:
        return
    recorder.evidence_bundle_id = bundle.bundle_id
    recorder.evidence_ref_count = len(bundle.refs)
    recorder.evidence_kinds = sorted({ref.kind.value for ref in bundle.refs})
    document = bundle.model_dump(mode="json")
    for ref in bundle.refs:
        ok, _ = resolve_pointer(document, ref.json_pointer)
        recorder.add_check(
            f"evidence-pointer:{ref.evidence_id}",
            ok,
            "" if ok else f"JSON Pointer 无法解析: {ref.json_pointer}",
        )


def check_proposal_and_review(state: AgentRunState, recorder: Recorder) -> None:
    bundle = state.evidence
    proposal = state.planning_proposal
    review = state.review_result
    if proposal is not None:
        recorder.proposal_id = proposal.proposal_id
        valid_ids = {ref.evidence_id for ref in bundle.refs} if bundle else set()
        referenced = {
            item
            for entry in (*proposal.issues, *proposal.recommendations)
            for item in entry.evidence_refs
        }
        recorder.add_check(
            "proposal-bundle-binding",
            bundle is not None and proposal.evidence_bundle_id == bundle.bundle_id,
            "Proposal 引用的 bundle_id 与当前 Evidence 不一致",
        )
        recorder.add_check(
            "proposal-evidence-refs",
            referenced <= valid_ids,
            f"引用了不存在的证据: {sorted(referenced - valid_ids)}",
        )
    if review is not None:
        recorder.review_status = review.status.value
        recorder.review_issue_count = len(review.issues)
        recorder.add_check(
            "review-identity",
            proposal is not None
            and review.reviewed_proposal_id == proposal.proposal_id
            and review.reviewed_planning_round == proposal.planning_round,
            "Review 与被审 Proposal 的身份/轮次不一致",
        )
        recorder.add_check(
            "review-bundle-binding",
            bundle is not None and review.reviewed_evidence_bundle_id == bundle.bundle_id,
            "Review 引用的 bundle_id 与当前 Evidence 不一致",
        )


def check_semantics(case: Case, state: AgentRunState, recorder: Recorder) -> None:
    """Terminal-state and intent expectations declared by the case itself."""
    recorder.add_check(
        "terminal-status",
        state.status is case.expect_status,
        f"期望 {case.expect_status.value}，实际 {state.status.value}",
    )
    recorder.add_check(
        "planning-presence",
        (state.planning_proposal is not None) == case.expect_planning,
        f"期望 planning={case.expect_planning}",
    )
    recorder.add_check(
        "review-presence",
        (state.review_result is not None) == case.expect_review,
        f"期望 review={case.expect_review}",
    )
    if case.expect_status is RunStatus.WAITING_FOR_INPUT:
        # A missing location must be reported, not guessed: no evidence may
        # have been fetched and no planning may have started.
        recorder.add_check(
            "waiting-has-missing-fields",
            bool(state.brief and state.brief.missing_information),
            "waiting_for_input 但 Brief 没有列出缺失字段",
        )
        recorder.add_check(
            "waiting-fetched-no-evidence",
            state.evidence is None,
            "waiting_for_input 却已经取证",
        )
    if case.expect_status is RunStatus.COMPLETED:
        recorder.add_check(
            "completed-must-have-evidence",
            state.evidence is not None,
            "completed 却没有 Evidence",
        )
    if case.expect_planning:
        recorder.add_check(
            "planning-consumed-review",
            state.review_result is not None,
            "规划路径完成却没有 Review 结论",
        )
    if state.status is RunStatus.FAILED:
        recorder.add_check("no-error-codes", not state.errors, f"errors={recorder.error_codes}")


def observe(state: AgentRunState, recorder: Recorder) -> None:
    recorder.status = state.status.value
    recorder.completion_mode = (
        state.completion_mode.value if state.completion_mode else None
    )
    recorder.planning_round = state.planning_round
    recorder.tool_retry_count = state.tool_retry_count
    recorder.warning_codes = sorted({item.code for item in state.warnings})
    recorder.error_codes = [item.code for item in state.errors]
    recorder.error_messages = [item.message for item in state.errors]
    if state.brief is not None:
        recorder.intent = state.brief.intent.value
        recorder.facility_types = [item.value for item in state.brief.facility_types]
        recorder.missing_fields = [
            item.field.value for item in state.brief.missing_information
        ]


def run_id_for(case_id: str) -> str:
    """Build a Run ID that satisfies the `run:` pattern the schema enforces.

    A bare `phase10-<case>` fails validation, which the self-check caught; the
    real run must never fail for a reason this trivial.
    """
    return f"run:phase10-{case_id}"


def run_case(
    case: Case,
    gateway: MVPToolGateway,
    model: DeepSeekLLM,
) -> Recorder:
    recorder = Recorder(case.case_id, case.message)
    guard = getattr(gateway, "quota_guard", None)
    recorder.route_targets_before = guard.granted_routes if guard else 0
    started = time.monotonic()
    state = run_agent(
        run_id=run_id_for(case.case_id),
        user_message=case.message,
        orchestrator=OrchestratorAgent(model),
        gateway=gateway,
        planning_agent=PlanningAgent(model) if case.expect_planning else None,
        reviewer_agent=ReviewerAgent(model) if case.expect_review else None,
    )
    recorder.seconds = time.monotonic() - started
    recorder.route_targets_after = guard.granted_routes if guard else 0
    observe(state, recorder)
    check_semantics(case, state, recorder)
    check_evidence(state, recorder)
    check_proposal_and_review(state, recorder)
    return recorder


def run_probe() -> int:
    """One geocoding call: is the AK + IP whitelist actually live?"""
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()
    if not ak:
        print("SKIPPED: BAIDU_MAP_AK 未配置。")
        return 0
    # Pin IPv4 exactly as the provider factory does, so the probe reports the
    # same reachability the real chain will see.
    install_ipv4_only()
    provider = BaiduGeocodingProvider(ak)
    try:
        match = provider.geocode(FIXED_ADDRESS)
    except BaiduGeocodingError as exc:
        print(f"PROBE FAILED: code={exc.code} retryable={exc.retryable} message={exc}")
        if exc.code is LocationErrorCode.INVALID_REQUEST:
            print("含义：服务端 AK 被拒——出口 IP 不在百度白名单，或该 AK 未获授权。")
            print("排查：把本机出口 IP 加入百度控制台白名单（`curl -s https://api.ipify.org`）；")
            print("      出口不是固定单 IP 时优先填写 CIDR 网段。")
        elif exc.code is LocationErrorCode.RATE_LIMITED:
            print("含义：配额或并发已达上限；稍后重试并核对控制台配额。")
        else:
            print("含义：非 AK/白名单问题，可能是网络或服务未开通。")
        return 1
    print("PROBE OK: 百度 Web Service 可达，AK 与 IP 白名单已生效。")
    print(f"  center=({match.center.lng:.6f}, {match.center.lat:.6f}) crs={match.center.crs}")
    print(f"  label={match.label}")
    return 0


def sanitize(text: str, ak: str, model_key: str | None) -> str:
    for secret in (ak, model_key):
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Phase 10 live acceptance")
    parser.add_argument(
        "mode", choices=("probe", "acceptance", "agent"), help="要执行的动作"
    )
    parser.add_argument("message", nargs="?", help="agent 模式下的用户消息")
    parser.add_argument(
        "--include-diagnosis",
        action="store_true",
        help="允许包含完整 Diagnosis 的案例（约 10 分钟、上千次算路）",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="只运行指定 case_id，可重复",
    )
    parser.add_argument(
        "--report",
        default="",
        help="把脱敏的机器可读结果写到这个路径",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="离线自检：确定性 Tool 结果 + 固定 Brief，不消耗配额与模型额度",
    )
    args = parser.parse_args(argv)

    if args.mode == "probe":
        if os.environ.get("RUN_BAIDU_SMOKE") != "1":
            print("SKIPPED: 需要 RUN_BAIDU_SMOKE=1 才允许真实百度调用。")
            return 0
        return run_probe()

    model_key: str | None = None
    ak = os.environ.get("BAIDU_MAP_AK", "").strip()
    if args.self_check:
        # Fully offline: deterministic Tool results and one fixed Brief, so the
        # script's own run/check/report path is exercised without spending
        # quota or model requests.
        gateway = SelfCheckGateway(FIXED_CENTER)
        model = SelfCheckLLM()
        guard = None
        cases = (SELF_CHECK_CASE,)
        print("SELF-CHECK 模式：离线确定性结果，不消耗配额与模型额度")
    else:
        if not ak:
            print("SKIPPED: BAIDU_MAP_AK 未配置。")
            return 0
        model_key = api_key_from_env()
        if os.environ.get("RUN_DEEPSEEK_SMOKE") != "1" or model_key is None:
            print("SKIPPED: 需要 RUN_DEEPSEEK_SMOKE=1 且 DEEPSEEK_API_KEY 已配置。")
            return 0
        if os.environ.get("RUN_BAIDU_SMOKE") != "1":
            print("SKIPPED: 需要 RUN_BAIDU_SMOKE=1 才允许真实百度调用。")
            return 0

        model = DeepSeekLLM(model_key)
        providers = build_live_providers(ak)
        gateway = MVPToolGateway(
            geocode=providers.geocoding.geocode,
            poi_provider=providers.poi,
            routing_provider=providers.routing,
        )
        guard = providers.guard

        if args.mode == "agent":
            if not args.message:
                parser.error("agent 模式需要一条用户消息")
            cases = (Case("ad-hoc", args.message, RunStatus.COMPLETED, False, False, True, "临时消息"),)
        else:
            cases = tuple(
                case
                for case in CASES
                if (not args.only or case.case_id in args.only)
                and (args.include_diagnosis or not case.needs_diagnosis)
            )

    skipped = [case.case_id for case in CASES if case not in cases]
    if not cases:
        print(
            "没有匹配的案例。含完整体检的案例需要 --include-diagnosis；"
            "请检查 --only 的取值。"
        )
        return 2
    print(f"model={getattr(model, 'model', type(model).__name__)}")
    print(f"quota={snapshot(guard).describe() if guard else '未启用（self-check）'}")
    print(f"cases={[case.case_id for case in cases]}")
    if skipped:
        print(f"skipped={skipped}（--include-diagnosis / --only 控制）")
    print()

    recorders: list[Recorder] = []
    for case in cases:
        recorder = run_case(case, gateway, model)
        recorders.append(recorder)
        mark = "PASS" if recorder.passed else "FAIL"
        print(f"[{mark}] {case.case_id} ({recorder.seconds:.1f}s, 算路目标 {recorder.route_targets})")
        print(f"        status={recorder.status} mode={recorder.completion_mode} "
              f"intent={recorder.intent} round={recorder.planning_round}")
        print(f"        evidence={recorder.evidence_bundle_id} refs={recorder.evidence_ref_count} "
              f"review={recorder.review_status}")
        if recorder.warning_codes:
            print(f"        warnings={recorder.warning_codes}")
        if recorder.error_codes:
            print(f"        errors={recorder.error_codes}")
        for name, ok, detail in recorder.checks:
            if not ok:
                print(f"        ✗ {name}: {detail}")
        print()

    print(f"usage(last)={getattr(model, 'last_usage', None)}")
    if guard is not None:
        print(f"quota final={snapshot(guard).describe()}")
    passed = sum(recorder.passed for recorder in recorders)
    print(f"RESULT: {passed}/{len(recorders)} 案例通过")

    if args.report:
        quota_payload = None
        if guard is not None:
            quota_payload = {
                "routes_per_second": guard.routes_per_second,
                "max_batch_routes": guard.max_batch_routes,
                "granted_routes": guard.granted_routes,
            }
        payload = {
            "model": getattr(model, "model", type(model).__name__),
            "quota": quota_payload,
            "cases": [
                {
                    key: value
                    for key, value in recorder.__dict__.items()
                    if key != "checks"
                }
                | {"checks": [
                    {"name": name, "ok": ok, "detail": detail}
                    for name, ok, detail in recorder.checks
                ]}
                for recorder in recorders
            ],
        }
        text = sanitize(json.dumps(payload, ensure_ascii=False, indent=2), ak, model_key)
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"report written: {args.report}")

    return 0 if passed == len(recorders) else 1


if __name__ == "__main__":
    raise SystemExit(main())
