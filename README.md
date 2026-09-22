# 15 分钟生活圈助手 — Core Tool 基线

本仓库以 Tool 1–6 的现有 Contract/Schema 和实现作为后续 API、Web 的唯一核心基线。六个 Tool 已有离线测试；完整真实百度端到端验收因步行路由限流尚未通过，不能据此宣称整个 MVP 已完成。

## 当前内容

| 部分 | 状态 |
| --- | --- |
| `contracts/v1/`、`app/schemas/` | Tool 1–6 的 v1 接口与示例；Tool 5/6 语义见 `docs/architecture/` |
| `app/modules/location/`、`app/providers/baidu/geocoding.py` | 已放入队友合并交付的 Location Service、百度 Provider 和测试；其真实联网验证由队友完成 |
| `app/modules/poi/`、`app/providers/baidu/poi.py` | 已换成双方合并交付的 POI Tool；真实百度检索结果见 `docs/test-report/poi-2026-09-18.md` |
| `app/modules/routing/`、`app/providers/baidu/routing.py` | 已从队友 Tool 1–4 交付包逐文件合入 Routing Service、百度 Provider、Contract 和测试 |
| `app/modules/isochrone/` | 已从同一交付包合入 Isochrone Service、Contract、测试和去敏样例；它复用 Routing，不直接调用百度 |
| `app/modules/blindspot/` | 已实现网格裁剪、步行路由判定、盲区/未知区及离线测试；含保存的真实等时圈样本处理测试 |
| `app/modules/diagnosis/` | 已实现 Tool 1–5 编排、设施指标与部分失败语义；有完整 Mock 集成测试 |
| `app/api/main.py` | FastAPI 薄接口：健康检查、Location、Diagnosis；直接复用现有 Tool Contract |
| `data/`、`docs/`、`demo/` | 保存去敏样本、架构与验收记录、现有地图 Demo；正式 Web 尚待开发 |

### Agent v1 Phase 1

Agent 层已进入 Contract 阶段：`app/schemas/agent.py` 定义 Brief、Evidence、Planning、Review 与 Run State 的共享结构，示例位于 `contracts/v1/`。这一阶段只建立数据契约，不实现 Agent 行为、流程路由、真实 LLM 调用或 MVP Gateway；Tool 1–6 仍是唯一的确定性事实层。

### Agent v1 Phase 2：Mock Tool Gateway

`app/agent_tools/gateway.py` 定义六个 Tool 方法的稳定 `ToolGateway` 接口；后续 Agent 和 Runtime 接收由调用方注入的 Gateway。`MockToolGateway` 使用 `contracts/v1/` 的确定性样例，按方法和规范化请求精确匹配，返回现有 v1 Result；未配置的请求会抛出 `MockScenarioError`。它不调用真实 Service、Provider 或网络，也不负责重试或流程决策。

```python
from app.agent_tools.mock_gateway import MockToolGateway
from app.agent_tools.mock_scenarios import load_contract_exchanges

normal_gateway = MockToolGateway()
partial_gateway = MockToolGateway(load_contract_exchanges("partial"))
failure_gateway = MockToolGateway(load_contract_exchanges("failure"))
```

接入更新版 Tool 1–6 时，让真实适配器实现同一接口，并运行 `tests/test_mock_tool_gateway.py` 中可复用的 Gateway 契约检查；无法准确映射到 v1 的语义变化需显式升级契约。Phase 2 只验证模拟交换，不代表真实百度链路验收。

### Agent v1 Phase 3：Orchestrator Agent

`app/agents/orchestrator.py` 将用户消息转成经过 `LifeCircleBrief` 校验的任务说明。模型只提取意图、目标、地点和设施类别；诊断/规划/审查标志和缺失信息由 Python 确定；仅当模型明确识别为标准完整体检或规划分析时，才使用三类默认设施。空消息或无效模型输出会明确失败；模型服务错误交给后续 Runtime 处理。Orchestrator 不调用 Tool、不决定流程下一节点。

模型通过 `app/providers/llm.py` 的 `StructuredLLM` 接口注入。当前只提供通用接口和离线测试，不绑定模型厂商，也不发起真实 LLM 请求。选定模型服务后，具体适配器负责模型配置、结构化输出、超时及网络错误处理；`OrchestratorAgent` 无需因此改写。

### Agent v1 Phase 4：Planning Agent

`app/agents/planning.py` 使用同一个 `StructuredLLM` 接口，把需要规划的 `LifeCircleBrief` 和成功的 Diagnosis 证据包转成 `PlanningProposal`。输入必须完整，诊断指标必须覆盖所请求的设施类别；证据路径必须存在，坐标输入须与诊断中心一致。输出中的轮次、证据包 ID、设施范围和每条证据引用会经过校验。无效模型输出会失败，模型服务错误留给 Runtime。当前使用受控模型做离线测试，不调用地图 Tool，也不做新增设施的情景模拟。

### Agent v1 Phase 5：Reviewer Agent

`app/agents/reviewer.py` 使用同一模型接口审查证据包与规划提案，返回 `approved`、`revision_required` 或 `insufficient_evidence` 的 `ReviewResult`。输入提案和输出审查结果的身份、轮次及引用均会核对；事实、数字和过度推断由模型按提示词审查。当前只有受控输出的离线测试，真实审查质量需接入模型后评测。Reviewer 不调用 Tool 或决定 Runtime 下一节点。

### Agent v1 Phase 6：Runtime State + Conditions

`app/agent_runtime/state.py` 复用公共 `AgentRunState` 创建运行并原子更新状态；替换已有 Brief、Evidence 或 Proposal 时，即使 ID 不变，也必须清除下游旧结果。`conditions.py` 根据 Brief、Evidence 和 ReviewResult 确定下一步。规划最多 3 轮，Tool 最多重试 2 次；错误是否可重试由后续 Runner 判断。本阶段只提供状态与纯条件函数，不执行 Graph。

### Agent v1 Phase 7：Graph + Runner

`app/agent_runtime/graph.py` 只声明节点与确定性连线：`ORCHESTRATE → FETCH_EVIDENCE → (PLAN → REVIEW →)* FINALIZE`，Review 回路按 `conditions.py` 的结果回到 PLAN、FETCH_EVIDENCE 或 FINALIZE；`PLAN` 只有一条无条件边，Brief 缺信息时停在 `waiting_for_input`。`app/agent_runtime/runner.py` 的 `run_agent(...)` 是唯一执行者：创建 Run、按图执行节点、装配证据、判断 Tool 重试与回路、写入终态。

Runner 先解析地点，再按任务类型取证：完整体检只调用 `diagnose_community()`（`EvidenceBundle` 不允许 Diagnosis 与低层结果并存），普通查询调用 `resolve_location` 与 `search_pois`。证据引用由 Runner 依据实际 Tool 结果生成。Tool 失败时 `retryable=false` 立即失败，`retryable=true` 且预算未耗尽时消耗一次重试并重取；预算耗尽或出现未配置的 Mock 场景则以 `failed` 终止，不留半成品证据。`revision_required` 推进规划轮次并清除被消费的 Proposal/Review；`insufficient_evidence` 在预算允许时重新取证（更换 Evidence 会清除旧 Proposal/Review），预算不足或达到 3 轮上限时以 `with_limitations` 完成并写入 warning。

本阶段不接真实模型、真实百度链路、API 入口或持久化。测试使用受控模型输出与既有 Mock Gateway，覆盖节点与转移、等待输入、重试成功与耗尽、非重试失败、规划轮次上限及补证据回路（`tests/test_agent_graph.py`、`tests/test_agent_runner.py`）。

### Agent v1 Phase 8：Mock E2E

`tests/e2e/` 用受控 `StructuredLLM` + Mock Tool 交换 + 真实 `run_agent` 跑完整场景矩阵，从一条用户消息一直到 `AgentRunState` 终态，并逐场景核对状态链：Evidence 的 `bundle_id`、每条 `EvidenceRef.json_pointer` 都能在证据包中解析、Proposal/Review 的身份与轮次一致、warnings/errors 保留、补证据或返工后被消费的旧结果不再有效。安装依赖后运行：

```bash
env -u BAIDU_MAP_AK -u RUN_BAIDU_SMOKE python -m pytest tests/e2e -q
```

矩阵覆盖：缺地点或设施类别（零 Tool 调用）、普通设施查询、`0`（确认没有）与 `null`（未知，必须带 `PARTIAL_POI_RESULTS`）分开断言、完整体检、规划被批准、审查要求修改（第二轮新轮次且旧审查被清除）、审查要求补证据（换 Evidence、清旧 Proposal/Review、预算耗尽带限制完成）、Tool 可重试失败后成功、不可重试/重试耗尽/未配置 Mock 一律 `failed` 且不留半成品证据。

本阶段同时修复了 Phase 7 的取证缺口：**可达性提问**需要 Routing 事实，Runner 现在以解析中心为起点调用 `calculate_walking_times`（没有可路由设施时不发请求，改为写入 `NO_ROUTING_TARGET` warning）；**盲区提问**不能由 POI 计数支撑，`LifeCircleBrief` 现在要求它走完整体检，由 Diagnosis 的等时圈与盲区覆盖回答。`completed` 只表示流程走完，不等于用户的问题已被证据回答。

项目长期分层是：用户界面 / Agent → 业务 Tool → GIS Tool → Provider → 百度地图 API。当前已完成确定性的 Core Tool、Agent 契约、Mock Gateway、三个 Agent 的离线行为、Runtime 状态判断、可运行的 Graph 与 Runner，以及离线端到端场景矩阵；真实 MVP Gateway（Phase 9）与真实模型/百度链路（Phase 10）仍待后续阶段。详见 `docs/architecture/core-mvp.md`。

## 基线边界

1. `app/schemas/common.py` 是公共 `CenterPoint`、`WarningItem`、`ErrorDetail`、`Meta` 的唯一来源。API 和 Web 复用现有 Tool/Schema，不另造数据结构。
2. 原有 `demo/map-demo.template.html` 与 `LICENSE` 保留；正式 Web 尚未开发。
3. 真实百度 AK 只放在本机 `.env`，不提交 Git；仓库只包含 `.env.example`。步行路由目前出现限流，请勿反复运行真实 API 脚本。

本地测试（先安装 `requirements.txt`）：

```text
python scripts/validate_contracts.py
python -m pytest -q
```

## 本地 API

安装依赖后，将 `.env.example` 复制为不入 Git 的 `.env` 并填写服务端 `BAIDU_MAP_AK`。在项目根目录启动：

```bash
set -a
source .env
set +a
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000/docs`。接口为 `GET /health`、`POST /api/v1/location/resolve`、`POST /api/v1/diagnosis`。两个 POST 分别使用现有 `LocationRequest/Result`、`DiagnosisRequest/Result`；请求格式错误返回 HTTP 422 与对应失败 Envelope，工具执行失败则在 Result 的 `ok=false` 中表达。没有 AK 时，坐标输入的 Location 仍可用，地址解析和 Diagnosis 会返回失败 Envelope，不会发起百度请求。

开发环境 CORS 默认仅允许 `localhost:5173` 和 `127.0.0.1:5173`。正式部署时通过 `CORS_ORIGINS` 指定逗号分隔的明确来源，不允许 `*`；服务端 AK 不进入前端、API 响应或 Git。

Tool 3/4 来源为 `core-mvp-tools-v1.zip`（SHA-256：`c9028fe8ca6e2ac1782327a4864212e377ae4923b3af9014a2c12d7ba47572f7`）。其交付时的离线测试为 `120 passed, 3 skipped`。

Tool 5/6 的 Contract/Schema 和 Service 完成后，接口校验通过；离线测试 `184 passed, 3 skipped`。Tool 5 已用保存的真实等时圈样本做离线空间处理测试；受控真实百度链路结果见 `docs/test-report/tool5-6-2026-09-19.md`。`scripts/verify_diagnosis_live.py` 默认不会发起百度请求，只有显式启用并配置 AK 才联网。

三类设施、默认业务参数的正式端到端验收因百度步行路由限流而未通过；没有继续消耗配额，结果与重验条件见 `docs/test-report/tool1-6-formal-acceptance-2026-09-19.md`。

尚未补 Docker、CI 和正式 Web；这些内容按交接文档随开发逐步加入。
