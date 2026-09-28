# 15 分钟生活圈助手 — Core Tool 基线

本仓库以 Tool 1–6 的现有 Contract/Schema 和实现作为后续 API、Web 的唯一核心基线。六个 Tool 有离线测试；队友在 2026-09-22 按当时百度控制台的 3 QPS 上限完成一次真实端到端验收，记录见 `docs/test-report/tool1-6-formal-acceptance-2026-09-22.md`，旧的限流失败记录保留作历史。正式 Service 已有按 AK 共享的进程内限速；单进程演示版 Agent 长任务入口和 Phase 10 语义补测结果见下方与 `docs/test-report/phase10-semantic-sampling-supplement-2026-09-24.md`。

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
| `app/api/main.py`、`app/api/agent_jobs.py` | FastAPI 薄接口：健康检查、Location、Diagnosis、同步 Agent Run 与有限容量的 Agent Job 轮询；直接复用现有 Tool Contract |
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

Phase 9 的 `MVPToolGateway` 实现同一接口，复用 `tests/test_mock_tool_gateway.py` 中的 Gateway 契约检查；无法准确映射到 v1 的语义变化需显式升级契约。Phase 2 的模拟交换本身不代表真实百度链路验收。

### Agent v1 Phase 3：Orchestrator Agent

`app/agents/orchestrator.py` 将用户消息转成经过 `LifeCircleBrief` 校验的任务说明。模型只提取意图、目标、地点和设施类别；诊断/规划/审查标志和缺失信息由 Python 确定；仅当模型明确识别为标准完整体检或规划分析时，才使用三类默认设施。空消息或无效模型输出会明确失败；模型服务错误交给后续 Runtime 处理。Orchestrator 不调用 Tool、不决定流程下一节点。

模型通过 `app/providers/llm.py` 的 `StructuredLLM` 接口注入。当前只提供通用接口和离线测试，不绑定模型厂商，也不发起真实 LLM 请求。选定模型服务后，具体适配器负责模型配置、结构化输出、超时及网络错误处理；`OrchestratorAgent` 无需因此改写。

### Agent v1 Phase 4：Planning Agent

`app/agents/planning.py` 使用同一个 `StructuredLLM` 接口，把需要规划的 `LifeCircleBrief` 和成功的 Diagnosis 证据包转成 `PlanningProposal`。输入必须完整，诊断指标必须覆盖所请求的设施类别；证据路径必须存在，坐标输入须与诊断中心一致。输出中的轮次、证据包 ID、设施范围和每条证据引用会经过校验。无效模型输出会失败，模型服务错误留给 Runtime。当前使用受控模型做离线测试，不调用地图 Tool，也不做新增设施的情景模拟。

### Agent v1 Phase 5：Reviewer Agent

`app/agents/reviewer.py` 使用同一模型接口审查证据包与规划提案，返回 `approved`、`revision_required` 或 `insufficient_evidence` 的 `ReviewResult`。输入提案和输出审查结果的身份、轮次及引用均会核对；结构或绑定校验失败时只追加一次带脱敏字段错误摘要的修复请求，仍失败则明确失败，不会伪造通过。事实、数字和过度推断由模型按提示词审查；真实语义质量仍需持续评测。Reviewer 不调用 Tool 或决定 Runtime 下一节点。

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

### Agent v1 Phase 9：MVP Tool Gateway

`app/agent_tools/mvp_gateway.py` 把现有六个 `ToolGateway` 方法直接映射到 Core Service：Location、POI、Routing、Isochrone、Blindspot 和 Diagnosis。构造时复用一组 Provider/Service，完整体检仍由 Tool 6 `diagnose` 编排；Agent、Graph 与 Runner 不改写。`MVPToolGateway.from_baidu_ak(ak)` 只构造 Provider，真正调用方法才可能联网。Phase 9 测试注入假 Provider、封锁 socket，并用同一个 `run_agent` 验证只替换 Gateway 的流程。

该阶段的验证完全离线。队友的 2026-09-22 真实 Tool 验收使用临时节流器；正式 API/Service 后续已加按 AK 共享的进程内批量算路限速，并新增单进程 Agent 长任务轮询入口。多进程配额共享仍未实现。Phase 9 记录见 `docs/test-report/phase9-mvp-tool-gateway-2026-09-22.md`。

`docs/项目进度交接_截至Phase8及Phase10准备_2026-09-22.md` 等较早文档是当时的进度快照；其中“Phase 9 未开始”已由本节和 Phase 9 报告更新。

### Agent v1 Phase 10 准备：DeepSeek 模型适配器

`app/providers/deepseek.py` 的 `DeepSeekLLM` 实现 `StructuredLLM` 协议，三个 Agent 可直接注入，无需改写。DeepSeek 的 JSON Output 模式不接收 JSON Schema，因此适配器把 `response_schema` 渲染进系统提示词，返回对象仍由各 Agent 的 Pydantic 模型校验。`max_tokens` 默认为 8192；空内容、非法 JSON、HTTP 错误、网络失败会产生明确异常，由 Runner 转成 `failed` Run，不会伪装成成功。

模型默认 `deepseek-flash`，可用 `DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_API_KEY` 覆盖。密钥只放在本机未入 Git 的 `.env`：

```bash
set -a; source .env; set +a
RUN_DEEPSEEK_SMOKE=1 python -m scripts.verify_agent_llm_live
```

该脚本每轮恰好发出 3 次请求（每个角色一次），只打印公开摘要，不含密钥、原始提示词或完整模型输出。可用 `RUN_DEEPSEEK_SMOKE_ROUNDS`（上限 5）跑多轮以观察稳定性。它验证的是**输出符合公共 Schema 与确定性引用校验**，不是回答质量；真实语义质量评测属于 Phase 10。

思考模式默认关闭：DeepSeek 的 reasoning tokens 计入 `max_tokens`，结构化抽取的输出很小，开启思考会把预算耗尽并导致 JSON 截断（首次真实调用即遇到）。需要时可用 `thinking=True` 或 `DEEPSEEK_THINKING=1` 开启；命中 `max_tokens` 会抛 `LLMOutputTruncated`，与格式错误明确区分。

**瞬时失败在适配器内重试。** Runner 只对 **Tool** 失败消耗重试预算（`MAX_TOOL_RETRIES`），模型异常会直接让整个 Run 判 `failed`——而一次完整体检的 Run 可能已经跑了好几分钟。因此适配器对**可恢复**的失败做有界重试：网络/超时、HTTP 429 与 5xx、以及 JSON 模式**偶发**的空内容（官方文档说明会偶发返回空）。**不重试**的是确定性失败：密钥或请求被拒（4xx）、`max_tokens` 截断、以及内容不是合法 JSON——同样的请求只会得到同样的结果。默认 `max_retries=2`、指数退避（`retry_base_seconds=0.5`，即 0.5s、1s），可用构造参数或 `sleep` 注入覆盖；`last_attempts` 记录最近一次实际尝试次数，便于诊断，耗尽后异常消息会注明重试了几次。

真实调用观测见 `docs/test-report/phase10-prep-deepseek-adapter-2026-09-22.md`。默认测试完全离线：`tests/test_deepseek_provider.py` 用注入的 `httpx.MockTransport` 断言请求载荷、重试策略与全部失败分支，真实调用被 `RUN_DEEPSEEK_SMOKE=1` 与 `DEEPSEEK_API_KEY` 双重门控。

### Agent v1 Phase 10：真实链路验收

真实模型 + 真实百度 + 真实 Gateway 的完整链路验收入口是 `scripts/verify_agent_live_e2e.py`，它按用户实际路径运行：`run_agent(...)` → `MVPToolGateway` → Core Tool 1–6 → 百度，并由 `DeepSeekLLM` 驱动三个 Agent。它**不是**默认测试的一部分，只有显式开关才会联网：

```bash
set -a; source .env; set +a
RUN_BAIDU_SMOKE=1 RUN_DEEPSEEK_SMOKE=1 python -m scripts.verify_agent_live_e2e probe
RUN_BAIDU_SMOKE=1 RUN_DEEPSEEK_SMOKE=1 python -m scripts.verify_agent_live_e2e acceptance
```

`probe` 只发一次地理编码请求，用来把“AK 未生效或 IP 不在白名单”与真正的链路故障区分开，几乎不消耗配额——上表提到的部署出口 IP 白名单要求对本机同样适用。`acceptance` 跑固定案例矩阵（地址输入、坐标输入、规划路径、以及必须停在 `waiting_for_input` 的缺地点案例），逐条核对终态、`completion_mode`、Evidence 的 JSON Pointer、Proposal 与 Review 的绑定关系。`--self-check` 用确定性结果离线跑通同一套检查与报告逻辑，不消耗任何配额，适合在真实运行前确认脚本本身没接错。

含完整体检的案例需要显式加 `--include-diagnosis`：默认三类设施的一次 Diagnosis 约 1600 个算路目标，按 3 QPS 节流本身就要约十分钟。`--report <path>` 可写出机器可读的脱敏结果，AK 与模型密钥都会被替换掉。

### 百度配额与限速

Core Tool 与 Agent 的真相层共用同一份按 AK 的配额预算。`app/providers/baidu/quota.py` 的 `BaiduQuotaGuard` 是进程内按 AK 共享的滑动窗口：一秒窗口内最多放行 `routes_per_second` 个**配额单位**：每个步行路线目标占 1 单位，每次地理编码或 POI 请求占 1 单位。监控会分别报告路线目标数、其他地图请求数和总单位数。这条窗口约束本身就能压住速率——每个被放行的请求都会被记录，所以累计放行量不可能超过「配额 × 已流逝秒数」；单批上限（`max_batch_routes`）又保证一个请求吃不下整个窗口。**不要把「配置的速率」误当成「请求之间必须隔多久」**：曾有一版额外强制最小间隔，结果在窗口还有余量时也把请求拒之门外一整个窗口，并发下 8 个目标实测从预算要求的 1.00 秒劣化到 5.03 秒，已删除（`77165bd`）。请求按 FIFO 排队，只有等待超过 `max_wait_seconds` 才会拒绝，并表现为既有契约里可重试的 `RATE_LIMITED`。`RoutingService` 的拆批大小由 Provider 提供的 `max_batch_routes` 决定，因此配额窗口会直接决定拆批粒度，而不是写死的 50。

默认值对应免费版（3 目标/秒）；付费配额通过环境变量调整，密钥与配额参数都只从本机环境读取：

```text
BAIDU_ROUTES_PER_SECOND=3
BAIDU_QUOTA_MAX_WAIT_SECONDS=180
```

`MVPToolGateway.from_baidu_ak(...)` 与 `app/api/main.py` 都经 `app/providers/baidu/live.py` 构造 Provider，所以 Agent 侧与 Core HTTP API 共用同一份预算，并发请求不会各自开一份配额。限速层不改变任何 Tool Contract、Schema 或百度单批 50 目标的限制。

### 百度流量强制 IPv4

`app/providers/baidu/live.py` 在构造 Provider 时调用 `install_ipv4_only()`，把百度流量固定到 IPv4。原因是这个故障**极难自行诊断**：

- `api.map.baidu.com` 同时解析出 A 记录与 AAAA 记录，而双栈主机会**优先走 IPv6**，于是请求从 IPv6 地址发出；
- 百度控制台的 IP 白名单只登记 **IPv4** 地址，因此即使你把 `curl https://ipinfo.io/ip` 查到的 IPv4 一字不差地加进白名单，请求仍会被拒；
- 拒绝方式是 **HTTP 200 + `status=210`（`APP IP校验失败`）**，看起来就像"白名单没配对"，于是会一直往控制台方向排查。

本机实测（同一台机器、同一 AK、只改地址族）：

```text
强制 IPv4 → status 0
强制 IPv6 → status 210
```

`install_ipv4_only()` 通过临时替换 `socket.getaddrinfo` 过滤掉 IPv6 结果来实现，带引用计数，可嵌套、可并发，进程退出即恢复；对只有一个 AAAA 记录的主机仍会回退到原结果，不会把可达主机变成不可达。若某天白名单改为登记 IPv6，可用 `BAIDU_FORCE_IPV4=0` 关闭。

### Agent Run HTTP 入口

`POST /api/v1/agent/runs` 把 Agent Runtime 暴露成 HTTP：请求体是 `AgentRunRequest`（`app/schemas/api.py`，只有 `schema_version`、`user_message`、可选 `run_id`），**响应体直接就是 `AgentRunState`**，所以 API 不会与 Runtime 各自演化出两套状态结构。

```json
{"schema_version": "1.0", "user_message": "请体检上海市虹口区四川北路街道并给出规划建议"}
```

设计要点：

- **形式正确的请求一律返回 200。** `completed`、`waiting_for_input`、`failed` 都是合法终态，靠 `status`、`completion_mode`、`errors` 区分，而不是靠 HTTP 状态码；只有请求体格式错误才返回 422。前端必须按 `status` 分支，不能把 200 当作成功。
- `run_id` 可省略，服务端会自动生成合法的 `run:*`；客户端传入的值同样按共享 `RunId` 规则校验，格式不对就是 422 而不是 500。
- 缺少服务端密钥时返回 `status=failed` 且 `errors[].code` 明确为 `LLM_KEY_MISSING` 或 `BAIDU_AK_MISSING`——这是部署没配好，不是调用方写错了，所以不用 5xx 表达。密钥值本身绝不进入响应。
- 构造 runner **不发任何网络请求**；真实调用只在执行时发生，并复用上面的按 AK 配额预算。
- 这个兼容入口仍是**同步**执行：一次完整体检可能需要数分钟，HTTP 连接会一直挂着。前端新页面应使用下面的任务接口。

`POST /api/v1/agent/jobs` 接收与同步入口相同的请求体，立即返回 HTTP 202。例如：

```json
{"schema_version":"1.0","job_id":"job:0123456789abcdef0123456789abcdef","run_id":"run:api-0123456789abcdef","status":"queued","status_url":"/api/v1/agent/jobs/job:0123456789abcdef0123456789abcdef"}
```

前端按 `status_url` 调用 `GET /api/v1/agent/jobs/{job_id}`。查询结果包含 `job_id`、`run_id`、`status`、`created_at`、`updated_at` 和 `result`；运行前 `result=null`，结束后 `result` 是完整的 `AgentRunState`。任务状态为 `queued`、`running`、`completed`、`waiting_for_input` 或 `failed`。`completed`、`waiting_for_input`、`failed` 是终态；前端仍须读取 `result.status`、`completion_mode`、`warnings` 和 `errors`。查询响应禁用缓存，未知或过期的任务返回 404。

演示服务器只支持**单进程、一个执行中任务加一个排队任务**；第三个同时提交的任务返回 429。终态结果保存 15 分钟，最多保留最近 32 条，进程重启后全部丢失。当前没有取消任务接口；如果前端遇到 429，应提示稍后重试，不要自动连续重发，以免重复消耗地图和模型额度。

项目长期分层是：用户界面 / Agent → 业务 Tool → GIS Tool → Provider → 百度地图 API。当前已完成确定性的 Core Tool、Agent 契约、Mock Gateway、三个 Agent 的离线行为、Runtime 状态判断、Graph 与 Runner、Mock 端到端场景、Phase 9 的真实 Core Service 适配器离线验证、按 AK 共享的进程内配额保护、Agent 的同步和轮询 HTTP 入口，以及三组真实语义样本按预设量表的人工复核。三组样本通过不代表有稳定通过率，也不代替独立专家或现场验证；详见 `docs/test-report/phase10-semantic-sampling-supplement-2026-09-24.md` 和 `docs/architecture/core-mvp.md`。

前端读取 Run/Job 结果时应把 `completion_mode`、阶段状态、整体置信度、逐设施 `unknown_ratio` 和非空 `warnings` 一起展示。`completion_mode=normal` 表示规划／审查流程正常结束，不代表证据完整；`unknown_ratio=0` 表示没有网格被分类为 unknown，不代表全部路线成功；`confidence=high` 也不会取消响应中具体列出的 warning。若产品需要单独的“证据完整度”总指标，应通过新版本化 DTO 定义，不重新解释已有字段。

## 基线边界

1. `app/schemas/common.py` 是公共 `CenterPoint`、`WarningItem`、`ErrorDetail`、`Meta` 的唯一来源。API 和 Web 复用现有 Tool/Schema，不另造数据结构；`app/schemas/api.py` 只描述 HTTP 入参。
2. 原有 `demo/map-demo.template.html` 与 `LICENSE` 保留；正式 Web 尚未开发。
3. 真实百度 AK 只放在本机 `.env`，不提交 Git；仓库只包含 `.env.example`。跨请求共享限速按单个 API 进程生效（默认按免费版 3 配额单位/秒：每个步行路线目标 1 单位，每次地理编码或 POI 请求 1 单位）；工具先按直线距离找近邻，网格某设施类别确认有一条 ≤1km 步行路线后会跳过该类别在该网格的其他候选，未确认覆盖时仍继续取证，未测完不能判盲。用进程内任务和配额管理器时，API 必须以单 worker 运行。多 worker/多副本会各自创建独立配额预算和任务表，现阶段不支持。Agent Job 没有取消接口，服务重启会丢失排队任务与已保存结果。

本地测试（先安装 `requirements.txt`）：

```text
python scripts/validate_contracts.py
python -m pytest -q
```

CI 与容器构建使用精确版本的 `requirements.lock`，默认测试不携带真实 Provider 密钥，且不会调用百度或 DeepSeek。

## 本地 API

安装依赖后，将 `.env.example` 复制为不入 Git 的 `.env` 并填写服务端 `BAIDU_MAP_AK`。在项目根目录启动：

```bash
set -a
source .env
set +a
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000/docs`。V1 接口保留原有请求与响应契约。V2 提供 `GET /api/v2/facilities`、`POST /api/v2/agent/brief`、`POST /api/v2/agent/jobs`、`GET /api/v2/agent/jobs/{job_id}`、`POST /api/v2/pois/search`、`POST /api/v2/pois/explore` 和 `POST /api/v2/routing/walking`。自然语言完整问答使用异步 Job：提交后前端轮询 `status_url`，任务完成后从 `result` 读取位置、证据、诊断/规划状态、限制和中文回答。

V2 目录为每个设施声明当前能力与证据限制。45 类设施均可通过统一目录与 POI 流程检索；步行时间问题和标准诊断会对最多 20 个代表点算路，超过 20 个实际检索到点位的设施类别时均匀抽样，并在限制说明中列出未取得路线样本的有点位类别。只有 `capabilities` 包含 `diagnosis` 且有受支持 `metric_profile` 的类别才可进入标准诊断；当前仍只有菜市场、药店和小学，V2 可报告已检索点位的步行可达性，但没有等时圈/人口覆盖证据采集器，因此目录不开放 `coverage` 能力，也不提供人口覆盖率或供需结论。其余设施可回答地图点位与可用路线问题，但不能据此断言容量、开放状态、服务质量或覆盖达标。目录外探索仅允许明确的设施/服务点名称，结果标记为 exploratory 且不进入标准计数、诊断或规划。POI 查询一次最多 100 类（当前目录有 45 类）、搜索半径最多 5 公里，每请求最多尝试 60 个关键词、每关键词最多翻 2 页；全目录先查询每类主词，再用剩余预算优先补查用户明确重点关注、主词无结果或点位较少的类别。未查的词继续在 `failed_queries` 标明原因。完整自然语言 Agent 运行有 8 分钟时间预算，单次 POI 搜索最多 3 分钟；时间预算耗尽时未完成类别保留为未知。若返回 `partial` 或 `unavailable`，前端应展示失败词与状态，不能把未知数量显示为 0。

V2 延续 Agent、Runtime、Tool Service、Provider、Schema/API 的分层，并沿用最初架构定义的统一 Tool Gateway 边界：`app/agent_tools/gateway_v2.py` 是 `ToolGateway` 的版本化延伸，`runner_v2.py` 只通过注入的 `ToolGatewayV2` 调用位置解析、POI 检索、目录外探索、步行路线和诊断，不直接构造 Service，也不直接调用百度 Provider；真实 Service 与共享百度 Provider 统一在 `MVPToolGatewayV2`（`app/agent_tools/mvp_gateway_v2.py`）中装配。V1 继续使用 `ToolGateway` / `MVPToolGateway`，两者互不影响。V2 尚未收集等时圈和人口覆盖证据，因此相关能力未对外承诺。

异步任务使用单进程内存队列：最多一个运行任务、一个等待任务，完成结果最多保留 15 分钟且不超过 32 条。它适用于单进程演示部署；服务重启会丢失任务状态，多 worker/多副本部署需要共享队列、持久化存储和分布式限速后再启用。真实百度/DeepSeek 验收通过有预算的单次测试手动触发；离线测试不会调用外部服务。自适应补查的首次真实综合测试与限额情况见 `docs/test-report/v2-adaptive-search-2026-09-28.md`。

## Docker Compose 演示

先将 `.env.example` 复制为 `.env` 并填入服务端密钥，然后在仓库根目录启动单 worker 容器：

```bash
docker compose up --build
```

API 默认仅发布到本机 `127.0.0.1:8000`。访问 `http://127.0.0.1:8000/docs` 验证服务，停止时运行 `docker compose down`。不要把 `.env` 打进镜像或提交仓库；镜像以非 root 用户运行。容器重启会清空内存中的任务状态。

开发环境 CORS 默认仅允许 `localhost:5173` 和 `127.0.0.1:5173`。正式部署时将 `CORS_ORIGINS` 设置为前端实际使用的完整 origin（含协议、域名和端口；多个 origin 用逗号分隔），不允许 `*`。服务端 AK 不进入前端、API 响应或 Git。当前受控演示应保持一个 Uvicorn worker；多个 worker 或副本会分别持有自己的百度配额守卫和任务记录，可能超出 AK 配额且无法跨 worker 查询任务。共享限速与持久化队列留待确有多进程部署需求时再设计。

API 的意外异常日志只记录 endpoint、Run/Job ID（如适用）和异常类型；不记录用户原文、密钥、请求 URL 或 Provider 响应正文。

发布或交接前，请确认历史上是否曾把百度 AK 暴露在聊天或文件中；若曾暴露且尚未轮换，应先在百度控制台轮换，再更新部署环境变量。不要把密钥写入本仓库或前端配置。

Tool 3/4 来源为 `core-mvp-tools-v1.zip`（SHA-256：`c9028fe8ca6e2ac1782327a4864212e377ae4923b3af9014a2c12d7ba47572f7`）。其交付时的离线测试为 `120 passed, 3 skipped`。

Tool 5/6 的 Contract/Schema 和 Service 完成后，接口校验通过；离线测试 `184 passed, 3 skipped`。Tool 5 已用保存的真实等时圈样本做离线空间处理测试；受控真实百度链路结果见 `docs/test-report/tool5-6-2026-09-19.md`。`scripts/verify_diagnosis_live.py` 默认不会发起百度请求，只有显式启用并配置 AK 才联网。

三类设施、默认业务参数的正式端到端验收于 2026-09-22 通过，详见 `docs/test-report/tool1-6-formal-acceptance-2026-09-22.md`；2026-09-19 的限流失败记录继续保留。

正式 Web 尚未开发。Docker 与 CI 的当前状态以仓库根目录的部署文件和 GitHub Actions 工作流为准。
