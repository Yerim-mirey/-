# Phase 7 Graph + Runner 设计

基线：`feat/runtime-state-conditions`（HEAD `843df5c`）；依据：`docs/architecture/15分钟生活圈_Agent_App_Architecture_v1.0_交接文档.md` 第 7.1、7.2、13、14、15、16、21 节与现有 `AgentRunState`、`conditions.py`、`state.py` 契约。

本阶段只交付 `app/agent_runtime/graph.py` 与 `app/agent_runtime/runner.py`，不新增公共契约、不引入真实百度调用、模型厂商适配、持久化、队列或新 Agent。

## 1. Graph：只描述节点与连线

`graph.py` 不执行业务代码，只声明节点名、主流程、Review 回路和确定性转移表，把 `conditions.py` 的判断结果映射成下一节点。缺失输入的 Run 停在 `WAITING_FOR_INPUT`，不再进入图。

```text
START → ORCHESTRATE → FETCH_EVIDENCE → (PLAN → REVIEW →)* FINALIZE
```

转移由 `after_brief`、`after_evidence`、`after_review` 唯一决定，图不再自行判断业务含义：

| 当前节点 | 条件分支 | 下一节点 |
| --- | --- | --- |
| ORCHESTRATE | `req_fetch_evidence` / `fetch_evidence` | FETCH_EVIDENCE |
| ORCHESTRATE | `req_wait_for_input` | WAIT_FOR_INPUT（终止） |
| FETCH_EVIDENCE | `req_plan` / `plan` | PLAN |
| FETCH_EVIDENCE | `req_finalize` / `finalize` | FINALIZE |
| PLAN | （唯一分支 `review`） | REVIEW |
| REVIEW | `req_finalize` / `finalize` | FINALIZE |
| REVIEW | `req_plan` / `plan` | PLAN |
| REVIEW | `req_fetch_evidence` / `fetch_evidence` | FETCH_EVIDENCE |
| REVIEW | `req_finalize_with_limitations` / `finalize_with_limitations` | FINALIZE |

## 2. Runner：一次 Run 的唯一执行者

`run_agent(*, run_id, user_message, orchestrator, gateway, planning_agent=None, reviewer_agent=None) -> AgentRunState` 创建 Run、初始化状态、按图执行节点、捕获顶层异常、标记完成或失败。Runner 是唯一修改状态、唯一决定重试与回路、唯一终结 Run 的角色；节点只返回新状态。

- **Brief**：`OrchestratorAgent.create_brief`。Brief 缺少地点或设施类别时状态为 `waiting_for_input` 并立即返回，不调用任何 Tool。
- **证据装配**：地址型 Brief 先 `resolve_location` 取得中心坐标，再用解析后的中心调用 `search_pois`；需要完整体检时改用 `diagnose_community`（坐标型诊断请求），并按 Phase 4 规则让指标类别与请求顺序一致。由于 `EvidenceBundle` 禁止 Diagnosis 与低层结果并存，体检证据只含 Diagnosis，其余任务只含 Location/POI。
- **证据引用**：`refs` 由 Runner 依据实际调用的 Tool 结果生成，JSON Pointer 指向 `location/data`、`poi/data/pois/<i>` 与 `diagnosis/data/metrics/<i>/blind_ratio`，不使用 RootModel.root；POI 计数为 `null` 的设施类别写入 warning，避免把未知当成盲区。
- **失败**：`ok=false` 的结果不留半成品证据，转成结构化错误。`ErrorDetail.retryable` 为假时立即以 `failed` 终止；为真且未达 `MAX_TOOL_RETRIES` 时只累计 `tool_retry_count` 并重试当前取证节点；重试预算耗尽后以 `failed` 终止，错误写入 `errors`。未配置的 Mock 场景按可重试的 `GATEWAY_ERROR` 处理。
- **规划与审查**：`planning_round` 从 1 开始。`revision_required` 时 REVIEW 仍保留本轮 Proposal 与 ReviewResult 供条件函数判断；进入下一轮 PLAN 时同一次原子更新中提升 `planning_round` 并清除被消费的 Proposal 与 ReviewResult。`approved` 完成。`insufficient_evidence` 时，只有当剩余 Tool 重试预算还够一次补取证（`tool_retry_count + 1 <= MAX_TOOL_RETRIES`）才重新取证：重新取证会更换 Evidence 并清除旧的 Proposal、ReviewResult 与轮次；预算不足时不再循环，写入 `INSUFFICIENT_EVIDENCE` warning——这是确定性的带限制完成标志，使 REVIEW 直接路由到 `finalize_with_limitations`。达到 `MAX_PLANNING_ROUNDS` 的非通过审查同样以 `with_limitations` 完成。
- **失效**：所有状态变更都经 `update_run`。Runner 在更换 Evidence 时于同一次更新中清除旧的 Proposal、ReviewResult 并把规划轮次重置为 1；在推进规划轮次时于同一次更新中清除被消费的 Proposal 与 ReviewResult。Brief 由 Orchestrator 只写一次，其后的失效规则由 Phase 6 状态入口继续保证。
- **完成语义**：非规划任务只允许 `normal`；规划任务只有在 `approved` 时才 `normal`，其余（包括预算耗尽和轮次上限）为 `with_limitations`；`failed` 与 `waiting_for_input` 不声明 `completion_mode`。

## 3. 本阶段不做

不实现 Phase 8 的 Mock E2E 场景矩阵，不接真实模型或真实百度链路，不新增 API、CLI、持久化、重试策略配置或图框架依赖。测试使用受控模型输出与既有 Mock Gateway，只覆盖节点、转移、重试、回路与终态。
