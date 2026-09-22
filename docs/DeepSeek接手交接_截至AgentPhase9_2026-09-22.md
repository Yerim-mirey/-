# 15 分钟生活圈智能体检与规划助手：DeepSeek 接手交接（截至 Agent Phase 9）

> 交接基准日：2026-09-22。本文以**本机实际仓库和本机验证结果**为准；旧交接文档是历史快照。本文中的“已完成”只对应明确写出的层次和证据，不代表完整产品已经交付。

## 0. 请先读这段结论

项目要把地点或坐标转成 15 分钟步行生活圈的设施、可达性、盲区和体检事实，再由 Agent 基于可追溯证据给出规划建议并复核。**确定性的 Core Tool 1–6 已建成；Agent 架构 Phase 1–9 已做完离线工程与测试；真实模型 + 真实地图 Tool 的完整 Agent Run（Agent Phase 10）尚未验收。**项目目前是“知识／业务后端引擎 + Core 薄 API”，不是一个已经完成的网页产品。正式前端、Agent HTTP 入口、并发保护、长任务交互与部署交付仍待完成。

DeepSeek 接手时请从当前本机 `feat/mvp-tool-gateway` 分支开始，先核对真实文件和测试，再完成 Phase 10 与后续产品化工作。**不要从 `main`、队友 ZIP 或旧 Phase 8 文档重新开始。**本次交接不要求上传远端；当前 Agent Phase 9 分支在本地，继续本机协作即可。

## 1. 仓库、版本与资料优先级

| 项目 | 当前事实 |
| --- | --- |
| 工作仓库 | `/Users/liuyanhe/Desktop/15分钟生活圈智能体检与规划助手/` |
| 接手分支 | `feat/mvp-tool-gateway` |
| 本文写入前 HEAD | `0dc879a`，`feat: connect agent gateway to core MVP tools` |
| 本文写入前状态 | 工作树干净；该分支未设置远端跟踪 |
| `main` | 停在 `e19ec48`，**不是**当前完整进度 |
| Python 环境 | 仓库内 `.venv/`；依赖清单为 `requirements.txt` |
| 本地密钥 | 未入 Git 的 `.env`；仓库只有 `.env.example` |

读资料按以下顺序：

1. `README.md`：最新仓库入口、当前阶段和本地启动方式。
2. `docs/architecture/15分钟生活圈_Agent_App_Architecture_v1.0_交接文档.md`：**Agent v1 的正式架构与 Phase 1–10 定义**，尤其第 4、11–16、20–22 节。
3. `docs/architecture/core-mvp.md`、`docs/architecture/blindspot-contract-v1.md`、`docs/architecture/diagnosis-contract-v1.md`：Core 数据语义和 Tool 5/6 的冻结规则。
4. `docs/test-report/phase9-mvp-tool-gateway-2026-09-22.md`、`docs/test-report/phase8-mock-e2e-2026-09-22.md`、`docs/test-report/phase10-prep-deepseek-adapter-2026-09-22.md`：最近三个 Agent 工作节点的真实范围与未证事项。
5. `docs/test-report/tool1-6-formal-acceptance-2026-09-22.md`、`API_交接说明_2026-09-22.md`：**队友提供**的 Core 真实百度验收及 API 后续要求。
6. `contracts/v1/`、`app/schemas/` 和代码／测试：具体字段与实际行为以这里为准。若文档文字与代码冲突，先记录差异并按冻结 Contract 判断，不能悄悄覆盖。

还有一份**产品最终交付基线**：`docs/reference/MVP-总工作计划-v1.0.md` 的第 1、3、7 节明确要求可运行的 Web 应用、真实社区案例、README、Docker、CI 与最终 QA。它的“阶段 0–11”是 **Core 比赛 MVP 产品路线**，并非本文“Agent Phase 1–10”。该早期计划曾建议先交付 Web 再做 Agent；团队后来先实施了 Agent 架构。**工作顺序已经变化，Web／部署验收目标没有因此自动完成或取消。**其中“不要现在增加 Agent”等句子是当时的时序说明，已被后续正式 Agent 架构替代。

`docs/项目进度交接_截至Phase6_2026-09-22.md`、`docs/项目进度交接_截至Phase7_及Phase8-10执行说明_2026-09-22.md`、`docs/项目进度交接_截至Phase8及Phase10准备_2026-09-22.md` 是各自时间点的快照，其中“Phase 9 未开始”等表述已经过期。队友的 `docs/reference/MVP-总工作计划-v1.0.md` 使用过自己的“Phase 9”名称；**本交接的 Phase 9 始终指 Agent 架构的 MVP Tool Gateway**，两套编号不能混用。

## 2. 业务目标与不可混淆的事实语义

- 输入可以是地址或 BD09LL 坐标。当前标准设施范围只有 `market`（市场）、`pharmacy`（药店）、`primary_school`（小学）；不要擅自把其他类别当作已支持。
- 地点、POI、步行路线、等时圈、盲区和体检指标由确定性的 Core Tool 产生。Agent 负责理解意图、生成规划建议、复核证据；**LLM 不发明地图事实**。
- 对外结果采用项目 v1 Envelope：`ok`、`data` 或 `error`、`warnings`、`meta`。`counts=0` 是完整查询后确认没有，`counts=null` 是未知／不完整；`blind` 是证据充分后确认无覆盖，缺数时应为 `unknown`。
- 内部坐标统一 BD09LL；GeoJSON 坐标顺序 `[lng, lat]`。POI 检索圆不等于真实 15 分钟步行可达区。
- 证据引用必须指向当前 `EvidenceBundle` 中存在的 JSON Pointer；旧 Evidence 被替换后，旧 Proposal/Review 不能继续有效。
- 简单设施查询、可达性提问、完整体检、规划建议是不同工作流。可达性必须有 Routing 事实，盲区必须由完整 Diagnosis 的等时圈／盲区证据支撑。流程 `completed` 只代表程序走到终态，**不自动证明回答充分或建议正确**。

## 3. 原始架构与仓库文件的逐层对应

原始冻结架构位于 `docs/architecture/15分钟生活圈_Agent_App_Architecture_v1.0_交接文档.md` 第 4、20 节。实际代码应按下表理解；不要为了“目录看起来一致”重新拆散已可工作的模块。

| 原始架构层 | 当前真实文件 | 已有能力／边界 |
| --- | --- | --- |
| MVP Core Tool 1–6 | `app/modules/location/service.py`、`poi/service.py`、`routing/service.py`、`isochrone/service.py`、`blindspot/service.py`、`diagnosis/service.py` | 确定性事实层；Diagnosis 编排前五个 Tool；Isochrone 另有 `sampling.py`、`interpolation.py`、`refinement.py`；对应 v1 Schema、Contract、测试均在仓库 |
| Baidu Provider | `app/providers/baidu/geocoding.py`、`poi.py`、`routing.py` | 隔离百度原始响应；密钥只在服务端 |
| 三个 Agent | `app/agents/orchestrator.py`、`planning.py`、`reviewer.py`、`prompts.py` | 分别形成 Brief、Proposal、Review；不直接操作百度或互相直接调用 |
| Agent Runtime | `app/agent_runtime/state.py`、`conditions.py`、`graph.py`、`runner.py` | 一次 Run 的状态、条件、图与执行；`run_agent(...)` 是 Python 入口 |
| Agent Tool 隔离层 | `app/agent_tools/gateway.py`、`mock_gateway.py`、`mock_scenarios.py`、`mvp_gateway.py` | 六方法协议、离线 Mock、Phase 9 真实 Core Service 适配器 |
| Agent 公共结构 | `app/schemas/agent.py`；`contracts/v1/agent-*.example.json` | Brief、Evidence、Proposal、Review、Run State v1 |
| Core 公共结构 | `app/schemas/common.py` 和其余 Tool Schema；`contracts/v1/` | `CenterPoint`、警告、错误、Envelope 与 Tool v1 示例，不另造重复 DTO |
| LLM Provider | `app/providers/llm.py`、`app/providers/deepseek.py` | 通用 `StructuredLLM` 与 DeepSeek 实现；真实模型单独冒烟过，完整 Agent 真实链路未验 |
| API | `app/api/main.py` | **Core 薄 API**：`/health`、Location、Diagnosis；目前没有 Agent Run HTTP 路由 |
| 前端／演示 | `demo/map-demo.template.html` | 仅既有地图演示模板，**不是正式 Web 前端** |

### 3.1 冻结结构与实际文件的差异，接手时要明说

- 架构图列有 `app/agent_runtime/validation.py`，实际**没有**这个文件。当前确定性校验分布在 Pydantic Schema、Agent 和 Runtime 实现中。Phase 10 先检查是否有真实校验缺口；不要仅因图上列出文件就建空模块或迁移稳定逻辑。
- 架构示意出现 `app/core/`、`app/main.py`。实际 `app/core/` 是**空的预留目录，没有已实现／已跟踪模块**；`app/main.py` 不存在。Core 业务代码落在 `app/modules/`，FastAPI 入口是 `app/api/main.py`。不要误写“均已实现”或为了匹配示意图复制入口。
- `app/providers/deepseek.py` 是通用 LLM Provider 的具体实现，未改变 Agent 与 `StructuredLLM` 的依赖关系。
- `app/agent_tools/mvp_gateway.py` 增加于 Phase 9，与架构预留位置一致。它把六个 Gateway 方法映射到已有 Core Service，没有另建一套业务算法或 DTO。
- 原始架构只**冻结 `app/` 的 Agent v1 分层**，并未宣称正式 Web、Docker、CI/CD 已建成。产品开发必须在这个边界外增量设计 API／前端，并保留当前 Tool Contract。
- 原始 Core 产品计划建议在前端框架尚未定时采用 Vue 3 + Vite、百度地图 JavaScript API 与 ECharts；这是待决技术方案，仓库中目前没有该前端实现。DeepSeek 应以可维护、可复现的 Web 交付为目标，不要把建议技术栈误写成现有文件。

### 3.2 必须维持的依赖方向

```text
用户界面 / Client → API → Agent Runtime → 三个 Agent → Agent Schema
                         ↓                 ↓
                  Tool Gateway          LLM Provider
                         ↓
                 MVP Core Tool 1–6 → Baidu Provider → 百度 Web Service
```

Runtime 负责图、状态、重试、轮次和 Reviewer 回路；Orchestrator 是第一个业务 Agent，不是 Runtime。禁止 Agent 直接依赖 `providers/baidu/` 或某个 Core Tool；禁止 Core Tool 反向依赖 Agent／Runtime；禁止 Agent 互相直接驱动节点跳转。Agent 证据来源只经 Gateway；Tool 6 Diagnosis 负责完整体检的内部编排，不在 Agent 层重做 GIS。

## 4. 已完成的工程进度与证据

| 工作 | 当前状态 | 可核对的位置与准确含义 |
| --- | --- | --- |
| Core Tool 1–6 | 已实现，离线测试覆盖；队友记录一次真实百度正式验收通过 | `app/modules/`、`app/providers/baidu/`、`app/schemas/`、`contracts/v1/`、`docs/test-report/tool1-6-formal-acceptance-2026-09-22.md`。真实结果是队友的受控验收，本次交接没有重跑百度 |
| Core 薄 API | 已有开发入口 | `app/api/main.py`。`GET /health`、`POST /api/v1/location/resolve`、`POST /api/v1/diagnosis`；FastAPI 另生成 `/docs`、`/openapi.json` |
| Agent Phase 1：Contracts | 已完成 | `app/schemas/agent.py`、`contracts/v1/agent-*.example.json` |
| Phase 2：Mock Gateway | 已完成 | `app/agent_tools/gateway.py`、`mock_gateway.py`、`mock_scenarios.py`；六方法协议、确定性样例与失败场景 |
| Phase 3：Orchestrator | 已完成离线行为 | `app/agents/orchestrator.py`、`prompts.py`、`tests/test_orchestrator.py`；从用户消息形成校验后的 Brief |
| Phase 4：Planning | 已完成离线行为 | `app/agents/planning.py`、`tests/test_planning.py`；建议范围／引用由确定性逻辑约束 |
| Phase 5：Reviewer | 已完成离线行为 | `app/agents/reviewer.py`、`tests/test_reviewer.py`；三种审查结论，内容质量仍需真实评测 |
| Phase 6：State + Conditions | 已完成离线行为 | `app/agent_runtime/state.py`、`conditions.py`；最多 3 轮规划、最多 2 次 Tool 重试，替换证据时清掉旧下游结果 |
| Phase 7：Graph + Runner | 已完成离线执行 | `app/agent_runtime/graph.py`、`runner.py`；`run_agent(...)` 跑单次内存 Run，不代表 HTTP 或持久化 |
| Phase 8：Mock E2E | 已完成离线场景 | `tests/e2e/test_agent_mock_e2e.py`、对应报告；受控 LLM + Mock Gateway + 真实 Runner，共 17 个场景 |
| Phase 9：MVP Tool Gateway | 已完成**离线集成** | `app/agent_tools/mvp_gateway.py`、`tests/test_mvp_tool_gateway.py`；六个方法映射到真实 Core Service，测试用假 Provider 并阻止网络，11 个新增测试；**不等于真实百度 + 真实模型链路通过** |
| Phase 10 前置：DeepSeek 适配器 | 已有实现和单独真实模型冒烟 | `app/providers/deepseek.py`、`scripts/verify_agent_llm_live.py`、`docs/test-report/phase10-prep-deepseek-adapter-2026-09-22.md`；不是 Phase 10 完成证明 |
| Agent Phase 10：Real E2E | **未完成** | 需在安全调用条件下运行真实 DeepSeek + 真实百度 + 真实 Gateway + Runner，形成可复现报告 |

队友的 Core 正式验收报告写明 `formal_acceptance_passed=true`，Location、POI、Routing、Isochrone、Blindspot 五阶段均为 `complete`，整体置信度 `high`；该次样本市场／药店／小学计数为 34／43／22，等时圈 24/24 方向和 192/192 路由样本完成，盲区分析 55 个网格、`unknown_ratio=0`。这些数值只描述**那个地点、那个配额与那次运行**，不能作为所有地点的固定结果，也不能替代 Phase 10 的 Agent 验收。

DeepSeek 适配器曾做 12 次有界真实请求：Orchestrator 4/4 和 Reviewer 4/4 通过合同校验，Planning 3/4，其中一次被确定性校验拒绝且原因没有定位；最近 9 次矩阵请求均通过合同校验。这里只说明输出格式与引用校验的样本情况，**没有评估规划语义质量**。原报告称“Phase 9 未开始”是写报告当天的历史状态，现已由 Phase 9 报告更新。

### 4.1 本次交接前的本机复核

在 `feat/mvp-tool-gateway`、HEAD `0dc879a`，清除真实服务凭据与联网测试开关后运行：

```bash
env -u BAIDU_MAP_AK -u RUN_BAIDU_SMOKE -u DEEPSEEK_API_KEY -u RUN_DEEPSEEK_SMOKE \
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/validate_contracts.py
```

2026-09-22 的本机输出为 **431 passed、4 skipped、2 warnings**；Contract 校验输出 `Core MVP Tool 1-6 and Agent v1 contracts validated successfully.`。4 个跳过项是显式启用的真实集成测试；2 个警告来自测试依赖弃用提示。该结果验证当前离线基线，**不验证百度配额、真实模型质量、浏览器体验或部署环境**。

## 5. 当前没有完成的产品层，务必别误报

| 层次 | 现状 | DeepSeek 接手后的工作 |
| --- | --- | --- |
| 正式 Web 前端 | 没有 `frontend/`、`web/`、`package.json`、正式页面与构建链。仅有 `demo/map-demo.template.html`。 | 基于真实用户任务设计输入、地图、指标、结果解释、证据／警告、规划建议及审查状态，并实现浏览器页面 |
| Agent 对外 API | `run_agent(...)` 仅 Python 调用；FastAPI 只暴露 Core Location/Diagnosis。 | 设计 Agent Run 的 HTTP 契约和输入／输出，复用现有 Agent Schema；处理等待补充信息、失败和受限完成 |
| 长任务与并发 | 现有 Diagnosis 同步，队友正式验收约需 10 分钟；无跨请求共享的按 AK 限速／排队。 | 在 Provider／执行层实现按实际配额的共享节流与并发上限；为前端提供状态查询／超时／取消，或明确受控单用户演示边界 |
| 联调与部署 | 无正式浏览器到 Agent 的完整链路；未补 Docker、CI 与部署脚本。 | 完成浏览器→API→Runtime→Agent/Gateway→Core→Provider 的验收；按原始 Core MVP 计划补 Docker、CI、运行／部署／复现说明 |

已有 Core API 的 CORS 开发默认来源为 `localhost:5173` 与 `127.0.0.1:5173`；正式环境需显式配置 `CORS_ORIGINS`。API 当前工具调用失败通常在 Envelope 内 `ok=false`，请求格式错误由 HTTP 422 表达。新增 Agent API 不应把百度原始 JSON、完整请求 URL、服务端 AK 或模型密钥传给浏览器。

## 6. DeepSeek 现在应怎样接手：顺序与具体产物

### A. 锁定基线，避免覆盖正确文件

1. 在上面的仓库检查 `git branch --show-current`、`git rev-parse --short HEAD`、`git status --short`；应确认是 `feat/mvp-tool-gateway` 与 `0dc879a`（若后来有新的交接文档提交，则以该提交及其父提交核对）。不要以 `main` 为起点推断 Phase 9 尚未存在。
2. 阅读原始 Agent 架构、本文、Phase 9 报告、Core v1 Contract 和 Core 正式验收报告，列出“代码存在／仅报告存在／尚未验证”的差异。
3. 先跑离线测试与 `scripts/validate_contracts.py`，确认基线。不要把真实百度或真实模型调用混入默认测试／CI。
4. 用户要求写代码时使用 **Ponytail** 来减少 token 消耗；保持改动集中、实现简单、测试覆盖真实风险。

### B. 完成 Agent Phase 10：真实链路验收

1. **先做调用安全。** 核对当时百度控制台配额；队友当次验收采用临时“每批最多 3 个目标、批间约 1.05 秒”的节奏，现有 `RoutingService`／API 没有跨请求共享限流。将限速、队列、并发控制放在合适的 Provider／执行层；不要把临时配额写死到 Tool Contract。受控、单用户、有限次数的真实调用仍需设置预算、超时与安全日志。
2. 用现有 `DeepSeekLLM`、`MVPToolGateway.from_baidu_ak(...)` 和 `run_agent(...)` 组成**同一条实际运行路径**；地点至少覆盖地址解析和直接坐标两种输入；任务至少覆盖设施查询、步行可达性、盲区／完整体检、需要规划与 Reviewer 的路径。
3. 分别核对 `completed`、`waiting_for_input`、`with_limitations`、`failed` 的适用场景；部分结果、`0`／`null`、`blind`／`unknown`、Provider 限流或无数据都不得被文字回答改写成确定成功。
4. 检查实际 Evidence、Proposal、Review：引用的 `bundle_id` 与 JSON Pointer 都有效，建议没有越过设施范围和证据，Reviewer 能指出不受证据支持的结论；对真实模型失败与此前一次 Planning 校验拒绝保留可定位的**脱敏**诊断信息。
5. 用固定案例与人工抽样核查意图识别、设施归类、规划建议与审查判断；合同通过率和内容质量分别报告，不用几次成功冒烟替代语义验收。发生问题时保留可重现输入的脱敏版本，修复后回跑对应案例。
6. 提交 `docs/test-report/` 下的新 Phase 10 报告：代码版本、运行时间、输入类别、配置摘要、实际调用量／时长、每条案例终态、证据追踪、失败与局限、人工检查结论、复现步骤；不得提交 `.env`、AK、完整带密钥 URL 或未脱敏的外部响应。

**Agent Phase 10 的退出条件：**真实 DeepSeek 与真实百度经实际 Gateway、Runner 跑通预定案例；限流／超时控制在该运行条件下有效；结果、错误与证据引用逐项检查；真实语义抽样结论和局限写入报告；离线测试与 Contract 校验仍通过。若某项失败，报告需明确“未通过”和可复现原因，不能将 Phase 10 标为完成。

### C. 再补 API 与正式前端

1. **API 合同先行。** 在保留现有 Core v1 Schema 的前提下增加 Agent Run 入口／查询接口；明确地址与坐标请求、任务状态、结果、警告、错误和补充信息的 HTTP 表达。不要复制或改写 `CenterPoint`、Diagnosis、Evidence 的已有定义。若要做任务状态 DTO，应明确它是 API 层结构。
2. **长任务方案。** 完整体检已有约 10 分钟的真实观测，应让浏览器看到排队／运行／完成／失败，避免默认 HTTP 长时间悬挂。任务模型需写明超时、取消、重复提交与部署重启后的行为；若比赛只允许受控单用户同步演示，也须明确限制、超时和操作步骤。Agent v1 原架构不要求 Redis、分布式 Runtime、长记忆或 SSE，不因产品化顺手增加。
3. **正式 Web。** 实现地点输入、设施选择／任务选择、进度反馈、地图与分析范围、三类设施指标、步行可达／盲区展示、规划建议与 Reviewer 状态、`0` 与未知的区分、部分失败和错误恢复。前端只消费公共 API；服务端 AK 和模型密钥绝不进入浏览器包。
4. **联调和交付。** 实际浏览器走 HTTP 到 Agent 或 Core；检查典型成功、无数据、部分结果、限流、模型失败、需要补信息、长任务超时。提供一键或清晰的本机启动方法、脱敏演示数据／受控真实演示步骤，按原始产品计划完成 Docker／CI 与新环境部署验证。以上目前均不能写成已有。

原始 Core MVP 总工作计划把 README、Docker、CI 和至少一个真实社区案例列为最终验收项；DeepSeek 需将它们放进交付计划。Agent Runtime v1 自身不需要 Redis、分布式 Worker 或 SSE，但这不等于最终 Web 产品可以省略启动、部署和自动检查。

## 7. “交接完成”与“项目最终完成”是两道不同的门槛

### 7.1 本次移交给 DeepSeek 何时算可接手

- DeepSeek 在**本机当前分支**看到本交接文档和第 1 节的关键资料，确认旧快照与现状差异。
- 复现第 4.1 节离线基线；若版本有变化，记录新的 commit、测试数量与差异，而不是照抄 `431 passed`。
- 明确工作边界：Core Tool 1–6 和 Contract 不重写；Agent Phase 9 是离线完成；Phase 10、Agent API 和正式前端仍要做。
- 就 Phase 10 的真实测试预算、现行百度配额及密钥位置做本机核对；密钥始终只从本机环境读取。

达到这些条件后即可继续开发。**上传远端不是本次移交的前置条件**；不要未经用户明确授权上传仓库、密钥或交接材料。

### 7.2 Agent 后端何时可宣布完成

- Phase 10 的实际模型 + 实际百度 + 实际 Gateway + Runtime 案例与证据质量验收通过，留下脱敏报告；失败路径与语义抽样也有结果。
- Core 与 Agent 的 v1 Contract 保持可校验，全部离线测试继续通过；新发现的接口差异走明示的 Contract 变更，不做隐式兼容。
- 真正用于演示或 API 的执行路径有符合当时配额的共享限流、并发与超时保护；密钥、日志与错误返回安全。
- 说明单次内存 Run 的局限；若要对外供前端使用，Agent API 和长任务行为已经明确并实际验证。

### 7.3 整个项目何时可宣布最终交付

以下均应有实际可运行结果、测试或演示证据；**仅交付后端、仅通过 Phase 10、仅有 `demo/map-demo.template.html` 均不足以宣布整个产品完成**：

1. 用户能在正式前端输入地址或坐标，选择受支持的任务，看到执行状态及最终结果。
2. 前端通过正式 HTTP API 调用当前后端；结果的地图、数字、建议、证据与警告一致，`0`／`null`、盲区／未知区、部分失败不会被误展示。
3. 至少一条完整的“浏览器 → API → Agent Runtime → DeepSeek + Gateway → Core Tool → 百度 → 前端展示”受控真实链路通过；另有离线／脱敏演示路径可复现。
4. 长任务、限流、并发、超时、失败和重试行为与界面反馈一致；服务端密钥不在前端、仓库或日志中泄漏。
5. 至少一个真实社区案例有完整测试记录，使用真实百度步行数据展示 POI、等时圈、盲区、Diagnosis、数据质量与限制；用户不碰代码也能操作 Web。
6. README 说明运行环境、安装、配置、启动、验收方法、已知限制及当时的代码版本；Docker 启动与部署验证可用；CI 自动执行 Contract 校验、离线测试与前端构建，真实百度／模型调用只在显式受控验收中执行；`LICENSE` 保留。

本节把原始 Core 比赛 MVP 的最终验收与后来新增的 Agent 能力合并为**当前建议的最终验收清单**，用于防止把阶段性后端成果误称为全项目交付。若比赛或指导老师另有明确验收指标，以其正式要求补充具体阈值和提交物。

## 8. 当前主要风险与处理原则

| 风险／未证事项 | 后果 | 处理原则 |
| --- | --- | --- |
| Routing 跨请求没有按 AK 共享限流；队友曾靠临时节奏完成真实验收 | 并发诊断可能限流，Phase 10 或演示不稳定 | 查当前配额，先加执行层限流／队列／预算，再受控跑真实链路；不把 3 QPS 当永久规则 |
| 一次完整 Diagnosis 约 10 分钟（队友案例） | 浏览器／代理超时，用户看不到进度 | 做任务状态与超时／取消，或严格受控单用户同步演示 |
| DeepSeek Planning 有过一次未定位的校验拒绝 | 合同成功率与语义质量不确定 | 保留脱敏可定位证据，建固定案例与人工抽样，分别报告格式和内容 |
| 只有 Core API，没有 Agent API 和正式 Web | 用户无法以产品方式使用完整 Agent 能力 | API 合同、前端、联调都纳入最终交付，不写“前端已完成” |
| `feat/mvp-tool-gateway` 本地分支，`main` 落后 | 从错误分支接手会丢 Phase 2–9 工作 | 接手时核对分支与 HEAD；远端上传按用户授权另行决定 |
| 旧交接／队友计划的阶段编号不同 | 将旧“Phase 9”误判为 Agent Phase 9 | 以 Agent App Architecture v1.0 第 21 节定义为准，报告注明两套编号 |
| 文档架构图列出未落地路径 | 为对齐图纸而引入重复实现 | 保留本节映射；只有真实需求和测试证明有缺口时才改结构 |

本机还可能存在被 Git 忽略的 `.env`、`demo/map-demo.local.html` 与缓存备份。它们是**本机私有文件，不是交付基线**；尤其地图本地页可能含浏览器端 AK。接手时只检查其是否存在和所需环境变量，不要在交接、日志、截图、提交或公开演示中复制其内容。

## 9. 接手后的首个工作记录应包含

请 DeepSeek 在开始修改代码前，留下一个简短的本机核对记录：当前分支与 HEAD、实际文件树、离线测试与 Contract 结果、当时百度配额及预估调用预算、Phase 10 的具体真实案例、限流／长任务决策、API 和前端的交付顺序。后续每一阶段都标注“代码完成／离线测试通过／真实链路通过／产品联调通过”中的实际位置。这样团队能清楚区分工程进度、验证进度和最终产品进度。
