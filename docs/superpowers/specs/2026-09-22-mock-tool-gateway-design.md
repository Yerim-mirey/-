# Phase 2 Mock Tool Gateway 设计

日期：2026-09-22
目标仓库：`/Users/liuyanhe/Desktop/15分钟生活圈智能体检与规划助手`
状态：Phase 2 已确认设计；本文件随实现提交。

## 目标与边界

在 Phase 1 的 Agent v1 契约和既有 Tool 1–6 v1 契约之间建立稳定的 Gateway 边界。Phase 2 只实现抽象接口、确定性 Mock 和离线契约测试，使 Phase 3–8 的 Agent/Runtime 可以在没有真实百度调用的条件下开发。后续拿到修改过的 Tool 1–6 封装时，可以在 Phase 9 前提前实现真实适配器；正常情况下只改变适配器，不改 Agent/Runtime。

本阶段不实现 `MVPToolGateway`、真实 Tool 接入、Agent、Runtime、LLM Provider、API 或前端，也不修改 Core Tool 1–6。Mock E2E 属于 Phase 8，不在本阶段宣称完成。

## 稳定接口

在 `app/agent_tools/gateway.py` 定义 `ToolGateway` 协议，方法与交接文档一致：

| 方法 | 输入 | 输出 |
| --- | --- | --- |
| `resolve_location` | `LocationRequest` | `LocationResult` |
| `search_pois` | `POISearchRequest` | `POISearchResult` |
| `calculate_walking_times` | `RoutingRequest` | `RoutingResult` |
| `generate_isochrone` | `IsochroneRequest` | `IsochroneResult` |
| `detect_blindspots` | `BlindspotRequest` | `BlindspotResult` |
| `diagnose_community` | `DiagnosisRequest` | `DiagnosisResult` |

Phase 3–8 的代码仅依赖 `ToolGateway`，由调用方注入具体实现；不得导入 `MockToolGateway` 的内部场景结构。完整社区体检优先调用 `diagnose_community`，不在 Agent 层重新编排低层 Tool。

Agent 侧保持 v1 接口。新版 Tool 若更改参数或输出字段，真实适配器负责转换并通过同一套契约测试。若新版结果的语义无法准确、无损地映射到 v1，必须显式升级契约版本；不允许静默伪造兼容性。

## Mock 实现与数据流

`app/agent_tools/mock_gateway.py` 提供 `MockToolGateway`。它持有按“方法名 + 请求的规范化 JSON”索引的请求/结果样例，调用时精确匹配并返回对应的现有 Tool Result 模型。没有匹配项时抛出明确的 Mock 场景配置错误，不猜测地理结果，也不回退到真实 Service、Provider 或网络。

默认样例覆盖六个方法的成功交换；Diagnosis 至少另有部分成功和失败交换。其他失败结果可通过注入现有 v1 失败样例覆盖，无需在生产代码中建立庞大场景框架。每次返回独立模型副本，避免调用方修改样例后影响后续测试。Mock 可记录方法与请求，供后续流程测试确认 High-level Tool First；调用记录不属于 `ToolGateway` 协议。

所有样例按对应 Pydantic Request/Result 模型加载。Blindspot 与 Diagnosis 的请求/结果组合必须通过现有 `validate_blindspot_exchange`、`validate_diagnosis_exchange`；其他 Tool 至少校验请求身份字段和输出类型，不能将一个地点或设施类型的结果冒充另一个请求。警告、部分成功和失败继续使用现有 Envelope，不让 Mock 自行重试或决定工作流跳转。

## 错误与安全

- 未配置的交换、请求不匹配或无效样例是 Mock 配置错误，应显式失败；不得悄悄返回空结果。
- Tool 业务失败以对应的 `ok=false` Result 表达。Mock 不把业务失败改成异常，也不吞掉契约错误。
- 运行 Mock 测试时强制关闭百度 smoke 并清空 `BAIDU_MAP_AK`；测试应证明 Gateway 没有真实网络访问。
- 不在 Mock 结果中放真实 API Key、用户地址或未脱敏的真实地图响应。

## 测试与验收

1. 对六个方法逐一验证：合法请求获得对应类型的 v1 Result；结果可 JSON 序列化并重新校验。
2. 验证规范化请求精确匹配、错误请求显式失败、重复调用稳定且返回副本、调用记录准确。
3. 验证 Diagnosis 正常、部分成功和失败场景；Blindspot/Diagnosis 使用现有 exchange validator 检查跨字段一致性。
4. 建立可复用的 Gateway 契约测试用例。Phase 9 或更早接入更新版 Tool 封装时，同一套用例必须在 `MVPToolGateway` 上通过；如转换失败，明确失败而非改写 Agent。
5. 运行现有完整离线测试与 `scripts/validate_contracts.py`，确认 Core 和 Phase 1 无回归；不运行真实百度验收。

## 交付范围

- `app/agent_tools/gateway.py`
- `app/agent_tools/mock_gateway.py`
- 与 Mock 交换配套的少量确定性样例和测试文件
- Phase 2 的 README/文档说明（仅在需要说明接口使用和真实接入边界时增加）

未来真实适配器文件 `app/agent_tools/mvp_gateway.py` 在拿到新版 Tool 封装并进入接入任务时创建，可早于原计划的 Phase 9；本阶段不预建空壳。
