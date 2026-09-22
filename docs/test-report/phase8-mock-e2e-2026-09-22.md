# Phase 8 Mock E2E 测试报告（2026-09-22）

**仓库：** `/Users/liuyanhe/Desktop/15分钟生活圈智能体检与规划助手/`
**分支：** `feat/mock-e2e`（基线 `feat/graph-runner`，`a3dd1c0`）
**设计/计划：** `docs/superpowers/specs/2026-09-22-mock-e2e-design.md`、`docs/superpowers/plans/2026-09-22-mock-e2e.md`

## 1. 本阶段目标与边界

用**受控 `StructuredLLM` + Mock Tool 交换 + 真实 `run_agent`**，从一条用户消息跑到 `AgentRunState` 终态，建立按业务场景组织的离线验收矩阵；同时修复 Phase 7 遗留的具体意图取证缺口。

本阶段**不包含**：真实模型调用、真实百度链路、`MVPToolGateway`、Agent API、持久化、Docker/CI。测试全程离线，场景夹具中的 `no_network` fixture 会在任何 socket 连接尝试时直接失败。

## 2. 运行记录

```bash
env -u BAIDU_MAP_AK -u RUN_BAIDU_SMOKE PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/validate_contracts.py
```

| 项目 | 结果 |
| --- | --- |
| 全量离线 pytest | **398 passed、3 skipped、2 warnings** |
| 其中 Phase 8 新增 | `tests/e2e/` 17 项 + `tests/test_agent_runner.py` 1 项 |
| 契约样例校验 | `Core MVP Tool 1-6 and Agent v1 contracts validated successfully.` |
| 跳过的 3 项 | 真实百度相关测试（`tests/integration/`），本阶段未启用 |
| 2 条警告 | Starlette/httpx 与 AnyIO 既有弃用警告，不是失败 |

Phase 7 基线为 380 passed、3 skipped。

## 3. 修复的 Phase 7 缺陷

Phase 7 的 Runner 只按 `needs_full_diagnosis` 二分取证，导致两种意图拿到不支撑结论的证据却仍以 `completed/normal` 结束：

| 意图 | 修复前 | 修复后 |
| --- | --- | --- |
| `accessibility_query` | 只有 Location + POI，没有任何步行时间事实 | Location → POI → `calculate_walking_times`，证据含 Routing；POI 为空时不发 Routing 请求并写入 `NO_ROUTING_TARGET` warning |
| `blindspot_query` | 只有 POI 计数，被当作盲区结论 | `LifeCircleBrief` 要求该意图走完整体检，证据为 Diagnosis 的等时圈/盲区覆盖/指标 |

修复涉及三处最小改动：`app/schemas/agent.py` 中 `blindspot_query` 的标志期望改为 `(needs_full_diagnosis=True, needs_planning=False, needs_review=False)`（盲区结论必须有 Diagnosis 阶段事实，这是对公共契约的语义修正）；`app/agents/orchestrator.py` 同步标志与标准设施范围校验；`app/agent_runtime/runner.py` 为可达性意图追加 Routing 步骤。

两个缺口都先写成失败测试（`test_accessibility_question_is_backed_by_walking_time_facts`、`test_blindspot_question_is_backed_by_diagnosis_coverage`），确认 RED 后再改实现。回归覆盖同时保留在 `tests/test_agent_runner.py`。

## 4. 场景矩阵

| 场景 | 测试 | 关键断言 |
| --- | --- | --- |
| 可达性提问 | `test_accessibility_question_is_backed_by_walking_time_facts` | 路由时长来自 Tool 结果（1012 s / 560 s）；调用序列 Location→POI→Routing |
| 可达性但无设施 | `test_accessibility_without_pois_does_not_invent_walking_time` | 不调用 Routing，`routing is None`，有 `NO_ROUTING_TARGET` warning |
| 盲区提问 | `test_blindspot_question_is_backed_by_diagnosis_coverage` | Diagnosis 盲区覆盖三类设施；证据不混装低层 POI |
| 缺地点 | `test_missing_location_waits_for_input_without_any_tool_call` | `waiting_for_input`；零 Tool 调用；只调用 Orchestrator |
| 缺设施类别 | `test_missing_facility_types_waits_for_input` | 同上，`missing_information` 指向 `facility_types` |
| 普通设施查询 | `test_facility_query_completes_with_confirmed_counts` | `completed/normal`；计数 (1,1,1)；四条证据引用可解析 |
| 无设施与未知数量 | `test_confirmed_absence_and_unknown_counts_stay_distinct` | 全 `0` 且无 warning；未知为 `null` 且带 `PARTIAL_POI_RESULTS` |
| 完整体检 | `test_full_diagnosis_uses_three_facility_metrics` | 三类指标按请求顺序；`poi`/`routing` 均为 `None` |
| 规划被批准 | `test_planning_analysis_approved_end_to_end` | Proposal/Review/Evidence 三者身份一致；`completed/normal` |
| 审查要求修改 | `test_revision_required_replans_and_approves` | 轮次 `[1,2]`；终态 review 绑定第 2 轮 Proposal |
| 规划轮次上限 | `test_planning_round_cap_completes_with_limitations` | 轮次 `[1,2,3]`；`with_limitations` |
| 审查要求补证据 | `test_insufficient_evidence_refreshes_then_completes_with_limitations` | 三次取证（d1/d2/d3）更换 Evidence；`tool_retry_count=2`；旧 Proposal/Review 不残留；`with_limitations` |
| 可重试失败后成功 | `test_retryable_tool_failure_then_success` | `tool_retry_count=1`；调用序列 resolve→resolve→POI；无 error |
| 重试耗尽 | `test_exhausted_tool_retries_fail_without_partial_evidence` | `failed`；`tool_retry_count=2`；`evidence is None` |
| 不可重试失败 | `test_non_retryable_tool_failure_fails_immediately` | `failed`/`TOOL_FAILED`；不重试；无证据 |
| 未配置 Mock 场景 | `test_unconfigured_mock_exchange_fails_instead_of_faking_success` | `failed`/`GATEWAY_ERROR`，消息含 `No Mock exchange configured` |
| 坐标型入口 | `test_coordinate_entry_takes_the_same_evidence_path` | BD09LL 坐标 Brief 走同一条取证路径，`source=input_coordinate`，中心与请求一致 |

## 5. 场景数据的确定性

Mock 按规范化请求精确匹配，因此场景夹具与 Runner 实际发出的请求逐字段一致：

- 地址夹具 `同济大学四平路校区` 解析到 `contracts/v1` 的同一中心，使地址路径能命中已配置的 Diagnosis 交换。
- 自定义 POI / Routing 交换用同一中心构造；POI 坐标都在请求半径内，设施类别都在请求集合内，计数等于实际返回的 POI 数，未请求类别保持 `null`，未知计数带 `PARTIAL_POI_RESULTS`。
- Routing targets 由 Runner 从 POI 的 `poi_id` 与坐标生成，夹具据此构造同请求同返回的交换。
- 缺少交换时以 `MockScenarioError` 明确失败，没有宽松兜底。

## 6. 仍未证明的部分

1. **真实模型质量**：三个 Agent 仍使用受控模型输出，本阶段不证明自然语言理解、规划内容或事实审查的质量。
2. **真实百度链路**：Tool 1–6 的正式真实验收仍未通过（限流），见 `docs/test-report/tool1-6-formal-acceptance-2026-09-19.md`。
3. **真实 Gateway**：`MVPToolGateway` 属于 Phase 9；本阶段只证明同一 `ToolGateway` 协议下的 Mock 交换。
4. **坐标型的可达性/盲区路径**：坐标型入口已有普通查询覆盖，但仍未覆盖坐标型的可达性与盲区场景。
5. **`agent_runtime/validation.py`**：架构预留文件，交接文档未把它列入 Phase 8 交付物，本阶段未实现。

`completed` 只代表 Runtime 走完流程并有证据；它不代表用户的问题一定被回答，也不代表真实服务质量。
