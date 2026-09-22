# Phase 8 Mock E2E 设计

基线：`feat/graph-runner`（HEAD `a3dd1c0`）；依据：`项目进度交接_截至Phase7_及Phase8-10执行说明_2026-09-22.md` 第 3、5 节与总体架构第 14、15、21 节。

本阶段不改公共契约、不接真实模型或真实百度链路、不加 API 入口、不加持久化。交付两部分：**修复 Phase 7 遗留的取证缺口**，以及**按业务场景组织的离线端到端验收矩阵**。

## 1. Phase 7 缺口：具体意图没有被相应事实支撑

`LifeCircleBrief` 支持五种意图，但 Phase 7 的 Runner 只按 `needs_full_diagnosis` 二分取证：

- `accessibility_query`（"到最近的药店要走多久"）走 `needs_full_diagnosis=false` 分支，只装配 Location + POI，**没有任何步行时间事实**，却仍以 `completed/normal` 结束。
- `blindspot_query`（"哪片区域是菜市场盲区"）同样只得到 POI 数量。把 POI 计数当成覆盖或盲区结论，正是契约反复禁止的推理。

`completed` 表示流程走完，不表示用户的提问被回答。修复方式按意图分流：

| 意图 | 取证路径 | 证据内容 |
| --- | --- | --- |
| `facility_query` | Location → POI | Location + POI |
| `accessibility_query` | Location → POI → Routing | Location + POI + Routing |
| `blindspot_query` | Location → Diagnosis | Diagnosis（内含等时圈、盲区覆盖与指标） |
| `community_diagnosis` | Location → Diagnosis | Diagnosis |
| `planning_analysis` | Location → Diagnosis | Diagnosis |

- **可达性**只需补充 Routing 事实：以解析中心为 origin、POI 的 `poi_id` 与坐标为 targets 调用 `calculate_walking_times`。POI 为空时没有可路由目标，此时不发 Routing 请求，改为写入 warning，证据只含 Location + POI，运行照样完成但不假装回答了可达性。
- **盲区**不能由 Gateway 单独取得：`BlindspotRequest` 要求上游的等时圈结果，而等时圈需要路由函数。因此盲区提问改为走 `diagnose_community()`，复用 Diagnosis 内部的等时圈与盲区阶段，而不是让 Runner 重新编排低层 GIS Tool。这需要 Orchestrator 对 `blindspot_query` 设置 `needs_full_diagnosis=true`，与 `community_diagnosis` 一致。
- 两个意图都不引入新 Agent、新 Tool 方法或新契约字段。

## 2. Mock E2E 场景矩阵

`tests/e2e/` 使用**受控 `StructuredLLM` + Mock Tool 交换 + 真实 `run_agent`**，从一条用户消息跑到 `AgentRunState` 终态：

| 场景 | 关键断言 |
| --- | --- |
| 缺地点或设施类别 | `waiting_for_input`；零 Tool、零 Planning、零 Reviewer 调用 |
| 普通设施查询 | Location → POI；`completed/normal`；计数与引用正确 |
| 无设施与未知数量 | `0` 与 `null` 分开断言；未知计数带 `PARTIAL_POI_RESULTS`；不从未知推出"没有设施" |
| 完整体检 | 三类设施指标来自 Diagnosis；Bundle 不混装低层结果 |
| 规划建议被批准 | Proposal/Review 身份与证据 ID 对齐；`completed/normal` |
| 审查要求修改 | 第二轮使用新轮次；被消费的旧审查结果不再出现在终态；不超过 3 轮 |
| 审查要求补证据 | 预算内重新调用 Tool、更换 Evidence、清除旧 Proposal/Review；预算耗尽 `completed/with_limitations` 且有说明 |
| Tool 可重试失败后成功 | 调用次数与 `tool_retry_count` 一致；最终无半成品证据 |
| 不可重试 / 重试耗尽 / 未配置 Mock | `failed` 且有可定位 error；不返回伪成功证据 |
| 可达性提问 | 有 Routing 事实，步行时长来自 Tool 结果 |
| 盲区提问 | 有 Diagnosis 盲区覆盖事实，不把 POI 计数当盲区 |

## 3. 场景数据的确定性约束

Mock 按规范化请求精确匹配，所以场景数据必须与 Runner 实际发出的请求逐字段一致：

- 地址解析结果必须与 `contracts/v1/diagnosis-request.example.json` 的中心一致，否则 Diagnosis 交换无法命中。
- 自定义 POI / Routing 交换用同一中心构造，POI 坐标必须在请求半径内、设施类别必须在请求集合内、计数必须等于实际返回的 POI 数；未知计数（`null`）必须带 `PARTIAL_POI_RESULTS`。
- Routing targets 由 Runner 从 POI 结果的 `poi_id` 与坐标生成，夹具据此构造同请求同返回的交换。
- 场景缺交换时以 `MockScenarioError` 明确失败，不用宽松兜底掩盖请求差异。

## 4. 状态链断言

除终态外，每个场景统一核对：Evidence 的 `bundle_id`；每条 `EvidenceRef.json_pointer` 能在 `EvidenceBundle` 序列化结果中解析；Planning 项的引用集合恰为 Brief 请求的设施类别对应证据；Proposal/Review 的身份、轮次、证据包 ID 互相一致；warnings/errors 保留；补证据或返工后旧 Proposal/Review 不得继续有效。

## 5. 本阶段不做

不实现真实 `MVPToolGateway`（Phase 9）、不接真实模型（Phase 10）、不新增 API/CLI/持久化、不补 `agent_runtime/validation.py`（架构预留文件，交接文档未把它列入 Phase 8 交付物）。真实模型与百度链路质量仍未被本阶段证明。
