# 15分钟生活圈智能体检与规划助手
## Agent App Architecture v1.0 交接文档

**项目阶段：** 在既有 Core MVP（Tool 1–6）之上搭建 Agent 层
**当前目标：** 只确定并搭建 `app/` 内部的 Agent 总架构，不扩展前端仓库结构
**开发原则：** 简洁、低耦合、可替换、可测试、先 Mock 后接真实 MVP
**本轮不做：** 正式前端、微信小程序、MCP、Redis、Worker、分布式 Runtime、长期 Memory、RAG、复杂 Checkpoint

## 实际仓库校准说明（2026-09-20）

为避免当前仓库事实与下文冻结的目标架构混淆，按本仓库现状校准如下：

- 当前 MVP 的实际入口是 `app/api/main.py`。
- 下文目标架构中的 `app/main.py` 仅表示冻结目标结构；当前不要为匹配示意图重复创建 `app/main.py`。
- `app/core/` 当前仅作预留目录，尚无已实现文件。
- 本说明只校准当前仓库事实，不重写或改变下文冻结的 Agent 目标架构。

---

# 1. 当前已完成基础

现有 Core MVP 已包含 6 个 Tool：

```text
1. Location
2. POI
3. Routing
4. Isochrone
5. Blindspot
6. Diagnosis
```

这些 Tool 是整个系统的**确定性事实计算层**，负责：

- 真实位置解析
- POI 检索
- 步行距离与时间
- 15 分钟等时圈
- 服务盲区
- 综合体检结果

Agent 层不能重新实现这些能力。

> **Tool 负责事实，Agent 负责理解、规划、审查，Runtime 负责流程。**

---

# 2. Agent 层正式组成

新增 3 个 Agent：

```text
1. Orchestrator Agent
2. Planning Agent
3. Reviewer Agent
```

以及 1 个**非 Agent**的运行管理层：

```text
Agent Runtime
```

完整逻辑：

```text
User
 ↓
Agent Runtime
 ↓
Orchestrator Agent
 ↓
LifeCircleBrief
 ↓
Tool Gateway
 ↓
Existing MVP Tool 1–6
 ↓
Structured Evidence
 ↓
Validation
 ↓
Planning Agent
 ↓
Reviewer Agent
 ↓
Runtime 根据 Review 结果决定：
    ├─ approved → Finalize
    ├─ revision_required → 回 Planning
    └─ insufficient_evidence → 回 Tool Gateway 补证据
```

---

# 3. 为什么 Runtime 在最外层

Runtime 不是第四个 Agent，而是整个 Agent 系统的：

```text
Run Manager
+
Workflow Controller
+
State Manager
```

技术执行顺序应为：

```text
User Request
 ↓
Runtime.start_run()
 ↓
创建 AgentRunState
 ↓
执行 Graph
 ↓
第一个智能节点 = Orchestrator
```

因此：

> **Runtime 是技术执行入口；Orchestrator 是业务上的第一个 Agent。**

Runtime 负责：

- 一次 Run 的创建、运行、成功、失败
- 当前执行到哪个节点
- State 的传递
- 条件路由
- Reviewer 打回
- 补充证据
- 最大轮次
- Tool Retry
- 最终终止

Orchestrator 不负责这些流程管理。

---

# 4. 正式冻结的 `app/` 架构

```text
app/
│
├── modules/                         # ✅ 现有 MVP Tool 1–6
│   ├── location/
│   ├── poi/
│   ├── routing/
│   ├── isochrone/
│   ├── blindspot/
│   └── diagnosis/
│
├── agents/                          # 🆕 三个真正的 Agent
│   ├── orchestrator.py
│   ├── planning.py
│   ├── reviewer.py
│   └── prompts.py
│
├── agent_runtime/                   # 🆕 Agent 流程管理
│   ├── runner.py
│   ├── graph.py
│   ├── state.py
│   ├── conditions.py
│   └── validation.py
│
├── agent_tools/                     # 🆕 Agent 与 MVP 的隔离层
│   ├── gateway.py
│   ├── mock_gateway.py
│   └── mvp_gateway.py
│
├── schemas/
│   ├── common.py                    # ✅ 原有
│   ├── location.py                  # ✅ 原有
│   ├── poi.py                       # ✅ 原有
│   ├── routing.py                   # ✅ 原有
│   ├── isochrone.py                 # ✅ 原有
│   ├── blindspot.py                 # ✅ 原有
│   ├── diagnosis.py                 # ✅ 原有
│   └── agent.py                     # 🆕 Agent 公共数据结构
│
├── providers/
│   ├── baidu/                       # ✅ 原有
│   └── llm.py                       # 🆕 LLM Provider
│
├── api/                             # ✅ 原有 API 层
│   └── ...
│
├── core/                            # ✅ 原有配置、异常、日志
│   └── ...
│
└── main.py
```

本交接文档只冻结 `app/`。前端、Docker、CI/CD、docs 等继续沿用现有 MVP 项目结构，本轮不重新设计。

---

# 5. `agents/`：只放真正需要 LLM 推理的角色

## 5.1 `orchestrator.py`

### 输入

```text
UserMessage
```

### 输出

```text
LifeCircleBrief
```

### 负责

- 理解用户想做什么
- 提取地点或中心点需求
- 判断任务 Intent
- 判断是否需要完整体检
- 判断是否需要规划建议
- 判断是否需要 Reviewer
- 判断是否缺少必要信息
- 形成结构化任务说明

第一版建议支持 Intent：

```text
facility_query
accessibility_query
blindspot_query
community_diagnosis
planning_analysis
```

### 不负责

```text
❌ 查真实 POI
❌ 算路径
❌ 算等时圈
❌ 算盲区
❌ 直接生成规划建议
❌ 管 Review Loop
❌ 自己决定 Graph 跳转
```

---

## 5.2 `planning.py`

### 输入

主要读取：

```text
LifeCircleBrief
EvidenceBundle
DiagnosisResult
BlindspotResult
```

### 输出

```text
PlanningProposal
```

### 负责

- 将确定性空间事实转成规划问题
- 总结哪些区域或设施类型值得优先关注
- 形成改善方向
- 形成有依据的规划建议
- 每条建议关联 `evidence_refs`
- 明确结论局限和数据质量限制

### 不负责

```text
❌ 直接调用百度 API
❌ 自己重新计算 GIS
❌ 编造不存在的数字
❌ 假装执行新增设施后的情景模拟
❌ 自己决定流程下一步
```

重要原则：

> **Planning Agent 只能根据已有 Evidence 提建议，不能替代新的确定性分析。**

---

## 5.3 `reviewer.py`

### 输入

```text
EvidenceBundle
+
PlanningProposal
```

### 输出

```text
ReviewResult
```

第一版固定三种状态：

```text
approved
revision_required
insufficient_evidence
```

### 负责检查

- 事实是否有证据支持
- 数字是否与 Tool Result 一致
- 建议是否超出证据能支持的范围
- Warning 是否被忽略
- 是否把“未检索到”说成“绝对不存在”
- 建议是否确实对应已经识别的问题
- 是否存在明显过度推断

### 不负责

```text
❌ 修 GeoJSON
❌ 校验 Pydantic Schema
❌ 重新计算 POI
❌ 自己调用 Planning Agent
❌ 自己选择下一节点
```

Reviewer 只返回结构化 ReviewResult，真正“退回哪里”由 Runtime 决定。

---

# 6. `prompts.py`

第一版集中保存三个 Agent 的 Prompt：

```text
ORCHESTRATOR_SYSTEM_PROMPT
PLANNING_SYSTEM_PROMPT
REVIEWER_SYSTEM_PROMPT
```

暂时不为每个 Agent 单独建立目录。

如果以后 Prompt 明显增多、需要版本化，再拆分。

---

# 7. `agent_runtime/`：整个 Agent 系统的管理层

## 7.1 `runner.py`

Runtime 的统一入口。

未来建议提供类似：

```python
run_agent(...)
```

职责：

- 创建 Run
- 初始化 State
- 启动 Graph
- 捕获顶层异常
- 标记成功 / 失败
- 返回最终结果

流程：

```text
User
↓
runner
↓
AgentRunState
↓
graph
```

---

## 7.2 `graph.py`

只描述：

> Agent Workflow 有哪些节点，节点之间怎样连接。

主流程：

```text
START
 ↓
ORCHESTRATE
 ↓
FETCH_EVIDENCE
 ↓
PLAN
 ↓
REVIEW
 ↓
FINALIZE
```

允许回路：

```text
REVIEW
 ├─ approved → FINALIZE
 ├─ revision_required → PLAN
 └─ insufficient_evidence → FETCH_EVIDENCE
```

`graph.py` 不应该塞进大量业务代码。

---

## 7.3 `state.py`

负责 Runtime 的共享状态。

第一版 `AgentRunState` 至少包含：

```text
run_id
status
user_message
brief
evidence
planning_proposal
review_result
planning_round
tool_retry_count
warnings
errors
```

所有节点遵守：

```text
读取同一个 State
↓
只修改自己负责的字段
↓
交还 Runtime
```

不要让每个 Agent 自己维护独立状态。

---

## 7.4 `conditions.py`

负责确定性流程路由。

例如：

```text
review.status == approved
→ FINALIZE
```

```text
review.status == revision_required
AND planning_round < MAX_PLANNING_ROUNDS
→ PLAN
```

```text
review.status == insufficient_evidence
→ FETCH_EVIDENCE
```

```text
planning_round >= MAX_PLANNING_ROUNDS
→ FINALIZE_WITH_LIMITATIONS
```

原则：

> **只要 Python 能确定判断，就不要让 LLM 决定流程箭头。**

---

## 7.5 `validation.py`

第一版集中处理确定性校验。

负责：

- Schema 是否合法
- `EvidenceRef` 是否真实存在
- Recommendation 是否至少有对应 Evidence
- Tool Result 是否可用于后续分析
- Warning 是否正确保留
- 必要字段是否完整

暂时不要单独建立整个 `validators/` 一级目录。

只有后期明显变复杂时再拆。

---

# 8. `agent_tools/`：Agent 和 MVP 之间的隔离墙

这层必须保留，因为 Agent 与 MVP 正在并行开发。

## 8.1 `gateway.py`

定义 Agent 世界允许调用的统一能力。

第一版对应现有 Tool 1–6：

```text
resolve_location()
search_pois()
calculate_walking_times()
generate_isochrone()
detect_blindspots()
diagnose_community()
```

核心规则：

> **High-level Tool First**

例如完整社区体检：

```text
优先：diagnose_community()
```

不要让 Agent 自己重新编排：

```text
Location → POI → Routing → Isochrone → Blindspot
```

只有用户提出具体子问题时，再调用低层 Tool。

---

## 8.2 `mock_gateway.py`

当前 Agent 开发阶段使用。

```text
Runtime
↓
MockToolGateway
↓
Mock / Fixtures
```

用途：

- 不等真实 MVP API
- 开发 Orchestrator
- 开发 Planning
- 开发 Reviewer
- 测试 Review Loop
- 做 Mock E2E

---

## 8.3 `mvp_gateway.py`

等队友的 MVP 接口稳定以后使用。

```text
Runtime
↓
MVPToolGateway
↓
Existing Tool 1–6
```

它负责必要的参数适配。

目标：

> **从 MockGateway 切换到 MVPGateway 时，三个 Agent 和 Runtime 不需要重写。**

---

# 9. `schemas/agent.py`

Agent 层第一版所有核心 Contract 集中放在：

```text
app/schemas/agent.py
```

预计定义：

```text
LifeCircleBrief

EvidenceRef
EvidenceBundle

PlanningIssue
PlanningRecommendation
PlanningProposal

ReviewIssue
ReviewResult

RunStatus
AgentRunState
```

当前不要拆成：

```text
schemas/agent/
├── brief.py
├── evidence.py
├── planning.py
...
```

等真正出现维护压力再拆。

---

# 10. `providers/llm.py`

三个 Agent 统一通过 LLM Provider 调用模型。

```text
Agents
↓
LLM Provider
↓
LLM API
```

第一版负责：

- 模型配置
- 调用模型
- Structured Output
- Timeout
- Provider Error

不要做：

```text
❌ 多模型 Router
❌ 每个 Agent 自己初始化一个客户端
❌ 复杂模型自动选择
```

未来换模型时，Agent 本身应尽量不改。

---

# 11. 正式依赖方向

用户入口：

```text
API / Client
    ↓
Agent Runtime
    ↓
Agents
    ↓
Agent Schemas
```

需要地图事实时：

```text
Agent Runtime
    ↓
Agent Tool Gateway
    ↓
MVP Core Tool 1–6
    ↓
Baidu Provider
```

需要模型能力时：

```text
Agents
    ↓
LLM Provider
```

---

# 12. 禁止依赖关系

以下依赖禁止：

```text
Planning Agent → providers/baidu/
❌
```

```text
Reviewer Agent → modules/blindspot/
❌
```

```text
Orchestrator → 直接调用 Reviewer / Planning
❌
```

```text
MVP Tool → agents/
❌
```

```text
MVP Tool → agent_runtime/
❌
```

MVP Core 必须完全不知道 Agent 层存在。

---

# 13. 核心数据流

```text
UserMessage
↓
Runtime.start_run()
↓
AgentRunState
↓
Orchestrator
↓
LifeCircleBrief
↓
Runtime 条件路由
↓
Tool Gateway
↓
MVP / Mock
↓
EvidenceBundle
↓
Validation
↓
Planning Agent
↓
PlanningProposal
↓
Reviewer
↓
ReviewResult
↓
Runtime
```

---

# 14. 三种典型 Workflow

## 14.1 简单设施查询

```text
User
↓
Runtime
↓
Orchestrator
↓
Tool Gateway
↓
POI
↓
Finalize
```

不运行 Planning / Reviewer。

---

## 14.2 完整体检

```text
User
↓
Runtime
↓
Orchestrator
↓
diagnose_community()
↓
DiagnosisResult
↓
Finalize
```

如果用户只需要体检结果，也不一定运行 Planning。

---

## 14.3 规划建议

```text
User
↓
Runtime
↓
Orchestrator
↓
Diagnosis
↓
Evidence
↓
Planning
↓
Reviewer
```

Reviewer 路由：

```text
approved
→ Finalize
```

```text
revision_required
→ Planning
```

```text
insufficient_evidence
→ Tool Gateway
→ 补证据
→ Planning
→ Reviewer
```

---

# 15. Runtime v1 必须实现

```text
✅ Run ID
✅ State
✅ Graph
✅ Conditional Routing
✅ Planning ↔ Reviewer Loop
✅ Max Planning Rounds
✅ Tool Retry Count
✅ Error
✅ Warning
✅ Mock E2E
```

---

# 16. Runtime v1 明确不做

```text
❌ Redis
❌ Worker Queue
❌ Distributed Runtime
❌ Long-term Memory
❌ Vector Database
❌ Event Bus
❌ Durable Checkpoint
❌ SSE Streaming
❌ 跨机器暂停 / 恢复
```

这些不是遗漏，而是有意控制 Agent v1 的工程范围。

---

# 17. Agent v1 明确不增加的其他 Agent

```text
❌ Manager Agent
❌ Intake Agent
❌ Diagnostic Agent
❌ Report Agent
❌ Location Agent
❌ POI Agent
❌ Routing Agent
❌ Isochrone Agent
❌ Blindspot Agent
❌ Diagnosis Agent
```

原因：

```text
Manager → Runtime 已承担
Intake → 与 Orchestrator 重叠
Diagnostic → 与 Diagnosis Tool 重叠
Report → 普通 Service / 模板即可
GIS 能力 → 已有确定性 Tool
```

---

# 18. 当前架构的优点

## 18.1 足够简洁

新增核心文件约为：

```text
agents/          4
agent_runtime/   5
agent_tools/     3
schemas/         1
providers/       1
```

没有为未来需求预建大量空目录。

## 18.2 不破坏 MVP

队友继续开发现有：

```text
modules/
providers/baidu/
api/
```

Agent 分支主要开发：

```text
agents/
agent_runtime/
agent_tools/
schemas/agent.py
providers/llm.py
```

## 18.3 MVP 未完成也能做

现在：

```text
MockToolGateway
```

以后：

```text
MVPToolGateway
```

只替换 Tool 接入层。

## 18.4 支持真正的 Agent Workflow

不是简单：

```text
A → B → C
```

而是支持：

```text
Planning ⇄ Reviewer
```

以及：

```text
Reviewer
↓
insufficient_evidence
↓
Tool Gateway
↓
Planning
```

## 18.5 Agent 不替代地图计算

真实地图、路网、POI、等时圈和盲区仍由 MVP Tool 负责。

---

# 19. 当前架构的已知缺点

## 19.1 Gateway 多一层

```text
Agent / Runtime → Gateway → Tool
```

比直接调用多一步，但并行开发和后续替换的收益更大。

## 19.2 Workflow 与 Runtime 在同一目录

这是为了控制文件数量。

必须持续保持：

```text
graph.py      = 流程结构
runner.py     = 流程运行
conditions.py = 流程判断
state.py      = 状态
```

## 19.3 `schemas/agent.py` 未来可能变大

第一版接受。

真正出现维护困难时再拆。

## 19.4 Runtime v1 不支持复杂持久化

这是主动控制范围，不是架构遗漏。

---

# 20. 当前正式冻结版本

```text
app/
│
├── modules/
│   ├── location/
│   ├── poi/
│   ├── routing/
│   ├── isochrone/
│   ├── blindspot/
│   └── diagnosis/
│
├── agents/
│   ├── orchestrator.py
│   ├── planning.py
│   ├── reviewer.py
│   └── prompts.py
│
├── agent_runtime/
│   ├── runner.py
│   ├── graph.py
│   ├── state.py
│   ├── conditions.py
│   └── validation.py
│
├── agent_tools/
│   ├── gateway.py
│   ├── mock_gateway.py
│   └── mvp_gateway.py
│
├── schemas/
│   ├── common.py
│   ├── location.py
│   ├── poi.py
│   ├── routing.py
│   ├── isochrone.py
│   ├── blindspot.py
│   ├── diagnosis.py
│   └── agent.py
│
├── providers/
│   ├── baidu/
│   └── llm.py
│
├── api/
│   └── ...
│
├── core/
│   └── ...
│
└── main.py
```

除非真实 Contract 或真实 MVP 接口证明此结构不合理，否则不要随意增加新的一级目录。

---

# 21. 后续正式开发顺序

```text
Phase 1
Agent Contracts
↓
LifeCircleBrief
EvidenceBundle
PlanningProposal
ReviewResult
AgentRunState

Phase 2
Mock Tool Gateway

Phase 3
Orchestrator Agent

Phase 4
Planning Agent

Phase 5
Reviewer Agent

Phase 6
Runtime State + Conditions

Phase 7
Graph + Runner

Phase 8
Mock E2E

────────────────

等待真实 MVP 接口稳定

↓

Phase 9
MVP Tool Gateway

Phase 10
Real E2E
```

---

# 22. 给接手 ChatGPT Work 的执行要求

接手后必须：

1. 先检查当前仓库真实 `app/` 结构。
2. 不覆盖或重构现有 Tool 1–6。
3. 对照本文检查命名冲突。
4. 第一阶段只建立必要目录与最小文件，不一次性实现全部功能。
5. 不增加本文未要求的 Manager Agent、MCP、Redis、Memory、RAG 等组件。
6. 不让 Agent 直接调用 Baidu Provider。
7. 不让 MVP Tool 依赖 Agent。
8. 优先完成 `schemas/agent.py` 的 Contract 设计，再写 Agent。
9. MVP 尚未完全接入时使用 `MockToolGateway`。
10. 所有 Review Loop 和节点跳转由 Runtime / Conditions 控制，不由 Agent 相互直接调用。
11. 如果当前仓库已有同名模块或现有实现与本文命名冲突，先汇报差异，不要直接覆盖。
12. 每一步继续遵守项目既有的 Contract-first、Mock、Tests、README 风格。

---

# 23. 给接手 Work 的第一条指令

> 请以当前仓库现有 Tool 1–6 为不可破坏的 MVP Core，严格按照《Agent App Architecture v1.0 交接文档》扩展 `app/`。不要重新设计总体架构，也不要一次性创建过度复杂的 Runtime。先检查当前 `app/`，确认无命名冲突后建立最小目录骨架；随后进入 Agent Contracts 设计阶段。当前不要接真实 MVP Tool，先使用 Mock Tool Gateway。

---

# 24. 本文档的架构结论

当前 Agent v1 的核心只有：

```text
3 Agents
+
1 Runtime
+
1 Tool Gateway Layer
+
1 Agent Schema File
+
1 LLM Provider
```

即：

```text
Orchestrator Agent
Planning Agent
Reviewer Agent

Agent Runtime

Mock / MVP Tool Gateway

Agent Contracts

LLM Provider
```

这套结构的目标不是“做一个复杂多 Agent 框架”，而是：

> **在不破坏现有 MVP Tool 1–6 的情况下，用最少的新架构实现自然语言理解、规划推理、证据审查、Review Loop、补证据和未来真实 MVP 接入。**
