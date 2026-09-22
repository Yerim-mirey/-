# Phase 3 Orchestrator Agent 设计

日期：2026-09-22
基线：`feat/mock-tool-gateway` 的 Tool 1–6 v1、Agent v1 Contracts 和 Mock Tool Gateway。

## 目标

将一条非空用户消息转换为 `LifeCircleBrief`。Orchestrator 只理解需求并整理结构化任务说明；不调用地图 Tool、不生成规划建议、不选择 Graph 下一节点，也不承担 Run/Retry/Review Loop 管理。

## 模型边界

`app/providers/llm.py` 定义通用 `StructuredLLM` Protocol：接收系统提示、用户消息和 JSON Schema，返回 JSON 对象映射。Phase 3 通过依赖注入使用它，离线测试提供受控实现。不绑定模型厂商、不读取模型密钥、不发起网络请求。具体模型适配器、模型配置、超时与网络错误映射在选定服务后实现；Agent 不因更换模型服务而改变。

`app/agents/prompts.py` 保存 `ORCHESTRATOR_SYSTEM_PROMPT`。提示词解释五种 `AgentIntent`、三类受支持设施、地点提取边界、不可编造位置、输出字段，以及不进行任何地理计算。输入坐标必须明确为 BD09LL；其他坐标系不在本阶段偷偷转换。

模型只返回四个语义字段：`intent`、`user_goal`、`location`、`facility_types`。内部 Pydantic 模型严格校验类型和额外字段。Orchestrator 从 `intent` 确定 `needs_full_diagnosis`、`needs_planning`、`needs_review`，从缺失的地点或设施类别生成 `missing_information`，固定 `schema_version="1.0"`，最后使用公共 `LifeCircleBrief` 再校验一次。

## 业务规则

- 五种意图沿用 `AgentIntent`：设施查询、可达性查询、盲区查询、社区体检、规划分析。
- 社区体检和规划分析未指定设施类别时，沿用 Core Diagnosis 的三类默认设施：`market`、`pharmacy`、`primary_school`。聚焦查询未指定类别时，Brief 标记缺少 `facility_types`。
- 所有意图都需要地点；未提供地点时标记缺少 `location`。Orchestrator 不自行地理编码。
- 重复设施类别按首次出现顺序去重。Intent 对应的三个工作标志由 Python 派生，不能由模型控制。
- 空白用户消息在调用模型前拒绝。模型输出缺字段、类型错误、未知类别或其他契约错误时抛出不含原始输出的 `OrchestratorOutputError`；模型服务异常原样上抛，由后续 Runtime 处理。Orchestrator 不重试。

## 接口与交付

- `app/providers/llm.py`：`StructuredLLM` 协议。
- `app/agents/prompts.py`：Orchestrator 系统提示。
- `app/agents/orchestrator.py`：`OrchestratorAgent(llm).create_brief(user_message) -> LifeCircleBrief` 与输出错误。
- `tests/test_orchestrator.py`：受控模型响应，覆盖五类意图、默认与缺失设施、缺失地点、去重、坏输出、服务异常、无 Tool/网络路径。
- README 增加 Phase 3 的调用边界及离线运行说明。

## 验收

运行新增测试、完整离线测试和现有契约验证。Phase 3 的验收只证明 Orchestrator 代码与受控模型输出的契约正确；自然语言提取质量和真实模型可用性需要后续接入具体模型后验证。
