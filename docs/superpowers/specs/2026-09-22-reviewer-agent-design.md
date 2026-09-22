# Phase 5 Reviewer Agent 设计

基线：`feat/planning-agent`。依据：`docs/architecture/15分钟生活圈_Agent_App_Architecture_v1.0_交接文档.md` 第 5.3、21 节及现有 Agent v1 契约。

`ReviewerAgent(llm).review_proposal(evidence, proposal)` 读取已验证的 `EvidenceBundle` 和 `PlanningProposal`，通过现有 `StructuredLLM` 返回 `ReviewResult`。只负责审查，不调用 Tool、Planning Agent 或 Runtime，也不决定下一节点。保持通用模型接口，不接具体服务。

调用模型前，要求 Proposal 指向输入证据包，其所有问题与建议引用均在证据包中。模型收到两份完整 JSON 和 `ReviewResult` JSON Schema。提示词检查事实、数字、过度推断、警告、未检索到与绝对不存在的区别、建议与问题的对应关系；根据公共契约选择 `approved`、`revision_required` 或 `insufficient_evidence`。需要新证据时只提出结构化 Diagnosis `EvidenceRequest`，由未来 Runtime 决定是否执行。

模型输出通过公共 `ReviewResult` 契约校验；Python 再核对被审提案 ID、轮次、证据包 ID，以及审查问题引用的建议和证据是否属于本次输入。无效模型输出抛出不回显私有内容的 `ReviewerOutputError`；模型服务异常原样交给未来 Runtime。

离线测试覆盖三种状态、输入关联、输出身份和引用、坏输出与服务异常；完整测试和既有契约验证通过。受控输出测试不证明真实模型的事实判断质量，留待接入模型后用评测验证。
