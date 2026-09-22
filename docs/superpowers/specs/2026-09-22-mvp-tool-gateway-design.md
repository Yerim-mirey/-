# Phase 9 MVP Tool Gateway 设计

基线：`feat/mock-e2e`，`910b978`。输入：队友交付包 `15min-life-core-mvp-api-handoff-2026-09-22.zip`、项目总体架构第 8 节及上一版交接文档的 Phase 9 范围。

## 1. 交付包合入原则

已验证交付包 103 个文件与 `MANIFEST.sha256` 全部匹配。Tool 1–6 的代码、Schema、Contract、测试和现有 API 与本仓库逐字节相同，因此不覆盖这些文件。只合入新 API 交接说明、2026-09-22 正式验收报告、三份参考文档；更新现有 README/POI 说明中的旧验收状态。保留 Agent 阶段内容、DeepSeek 环境样例与 Agent 契约校验脚本。

新报告记录 Tool 1–6 在 2026-09-22 的一次真实百度正式验收通过；2026-09-19 的失败记录继续保留为历史。队友报告中的 3 QPS 是当时控制台上限及临时验收节奏，当前正式 Routing Service 尚无跨请求共享限速。Phase 9 不发起真实百度调用，也不把一次验收节奏硬编码为通用限速策略。

## 2. Gateway 接口

新增 `app/agent_tools/mvp_gateway.py`，实现现有六方法 `ToolGateway`，返回原有 v1 Result，不引入新 DTO。构造器接收一个可选 `geocode` 函数、一个 POI Provider 和一个 Routing Provider；内部各建一次 `POIService`、`RoutingService`、`IsochroneService`。`from_baidu_ak(ak)` 只负责构造三个百度 Provider，无网络请求；AK 由调用方从服务端环境传入，不由 Gateway 读 `.env`。

| Gateway 方法 | 现有 Service |
| --- | --- |
| `resolve_location` | `resolve_location(request, geocode=...)` |
| `search_pois` | `POIService.search(request)` |
| `calculate_walking_times` | `RoutingService.route(request)` |
| `generate_isochrone` | `IsochroneService.generate(request)` |
| `detect_blindspots` | `analyze_blindspots(request, route)` |
| `diagnose_community` | `diagnose(request, resolve_location=..., search_pois=..., route=..., generate_isochrone=..., analyze_blindspots=...)` |

Gateway 不重算 GIS、不改写 `ok`/warning/error/`null`，也不决定 Agent 重试或审查回路。完整体检仍只调用高层 `diagnose_community()`。没有服务端 AK 时，不构造真实 Provider；坐标解析可通过注入的离线 Gateway 测试。

## 3. 验证与边界

测试使用受控假 Provider 和现有 Core Service，禁止网络。六方法复用 `tests/test_mock_tool_gateway.py` 的 `assert_gateway_contract`，再验证真实 Service 装配的 Diagnosis、部分/失败语义，以及一次 `run_agent` 只替换 Gateway 的离线流程。全量 pytest 和契约脚本通过后更新 README。

Phase 9 完成只证明真实 Core Service 的适配可替换 Mock。公开 API 的共享限速、排队、长任务模型和真实 Agent 全链路验收属于后续 API 工作与 Phase 10；在这些保护落实前不做反复联网诊断。
