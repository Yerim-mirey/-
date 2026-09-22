# Phase 4 Planning Agent 设计

基线：`feat/orchestrator-agent`；依据：`docs/architecture/15分钟生活圈_Agent_App_Architecture_v1.0_交接文档.md` 第 5.2、21 节及现有 Agent v1 契约。

## 边界

`PlanningAgent(llm).create_proposal(brief, evidence, planning_round=1)` 接受已验证的 `LifeCircleBrief` 和 `EvidenceBundle`，通过现有 `StructuredLLM` 生成 `PlanningProposal`。本阶段只有通用模型接口及离线受控测试，不接模型服务、地图 Tool、Runtime 或 Reviewer。

输入必须需要规划、没有待补信息、轮次为正整数，且证据包包含成功的 Diagnosis 结果及其证据引用；诊断指标必须覆盖 Brief 请求的设施类别。否则调用模型前拒绝。

模型接收 Brief、EvidenceBundle、轮次的 JSON 和 `PlanningProposal` JSON Schema。提示词要求每条问题和建议仅引用已有 evidence ID，忠实处理空值、警告和质量限制，不编造数字、点位或新增设施后的模拟结果。模型返回完整公共契约对象；Python 校验契约、轮次、证据包 ID、所有 evidence_refs 是否存在，以及问题设施类别是否在 Brief 范围内。不修改模型的规划判断。

无效模型输出抛出不回显原始内容的 `PlanningOutputError`；模型服务异常原样交给未来 Runtime。Planning Agent 不重试、不决定后续流程。

## 验收

受控模型测试覆盖有效建议、输入拒绝、身份和引用错误、设施范围、坏输出及服务异常。运行完整离线测试和既有契约验证。测试仅验证结构与边界；真实模型的事实忠实度需接入后由 Reviewer 和端到端测试验证。
