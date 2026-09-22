# Phase 9 MVP Tool Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 合入队友的新 Core 验收资料，并以同一 `ToolGateway` 协议连接现有六个真实 Core Service。

**Architecture:** 保留当前 Agent 仓库中逐字节相同的 Tool 代码和后续 Agent 扩展；只合入新文档。`MVPToolGateway` 是薄的依赖装配层，内部复用一个 Routing Service 给 Routing、Isochrone、Blindspot、Diagnosis，返回原 v1 Result。默认离线测试注入假 Provider；真实网络验收留到具备共享限速保护之后。

**Tech Stack:** Python 3.12、Pydantic v2、pytest、现有 Core Service；无新依赖。

**Spec:** `docs/superpowers/specs/2026-09-22-mvp-tool-gateway-design.md`

## Global Constraints

- 使用 Ponytail 原则：小步、复用现有契约/标准库、不要增加无用途抽象。
- Tool 1–6 的实现、Schema、Contract、Agent 代码不因交付包覆盖而退化。
- 不打印/提交 AK，不在普通测试中发起百度或 DeepSeek 请求。
- Gateway 保留 Tool Result 的 `ok`、warnings、errors、`0/null`，不重试、不计算 GIS。
- 2026-09-22 的正式验收报告来自队友交付；3 QPS 节流只在当次验收器中，不是现有正式 Service 的能力。

---

### Task 1: 合入交付包的新资料

**Files:** 新增 `API_交接说明_2026-09-22.md`、`docs/test-report/tool1-6-formal-acceptance-2026-09-22.md`、`docs/reference/` 下三份文档；修改 `README.md` 和 `app/modules/poi/README.md`。

**Interfaces:** 文档指向既有 Core Tool 与 Agent 文件；代码接口无变化。

- [x] 对照 `MANIFEST.sha256` 和工作树哈希，仅复制新增的五个文档，恢复 Unicode 文件名。
- [x] 更新 README 的最新真实验收状态，同时保留 Phase 1–8/DeepSeek/Agent 说明；修正 POI README 中旧的“没有真实 Provider”句子。
- [x] 执行全量离线 pytest 与 `scripts/validate_contracts.py`，确认资料合入未覆盖代码。

### Task 2: 六方法 Gateway 的 RED → GREEN

**Files:** 新增 `tests/test_mvp_tool_gateway.py`、`app/agent_tools/mvp_gateway.py`。

**Interfaces:** `MVPToolGateway(*, geocode, poi_provider, routing_provider)`；`MVPToolGateway.from_baidu_ak(ak: str)`；六方法签名与 `ToolGateway` 完全一致。

- [x] 在 `tests/test_mvp_tool_gateway.py` 写六方法的失败测试：用假的 Geocode/POI/Route Provider 驱动现有 Core Service，并对每个返回调用 `assert_gateway_contract`。额外断言 `isinstance(gateway, ToolGateway)`、坐标输入不触发 Geocode、Diagnosis 的五个阶段、Provider 部分/失败结果与 `null` 语义。再写受控 Orchestrator + 真实 Runner + MVP Gateway 的普通设施查询测试，断言 `completed/normal`、POI 证据引用可解析、无网络。
- [x] 运行聚焦测试并确认因 `app.agent_tools.mvp_gateway` 不存在而 RED。
- [x] 实现最小 Gateway：构造一次 POI/Route/Isochrone Service，方法直接调用现有 Service，Diagnosis 注入相同依赖；工厂仅构造百度 Provider。
- [x] 运行聚焦测试至 GREEN。若某场景本身需要不必要的大量假数据，优先简化测试输入，不增加产品代码抽象。

### Task 3: 文档与最终验证

**Files:** 修改 `README.md`；新增 `docs/test-report/phase9-mvp-tool-gateway-2026-09-22.md`。

**Interfaces:** `run_agent(..., gateway=MVPToolGateway(...)) -> AgentRunState`，三个 Agent、Graph、Runtime 不修改。

- [x] 更新 README 的 Gateway 选择与 Phase 9 边界，记录离线验证命令、未做的跨请求共享限速和 Phase 10 真实链路条件。
- [x] 执行全量离线 pytest、契约脚本，对本阶段自写文件执行 `git diff --cached --check`；按原哈希复制的队友 Markdown 保留其有意的换行空格。核查 `git status` 只含预期文件；完成后提交阶段产物。
