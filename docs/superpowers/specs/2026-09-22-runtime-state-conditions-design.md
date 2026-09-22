# Phase 6 Runtime State + Conditions 设计

基线：`feat/reviewer-agent`；依据：`docs/architecture/15分钟生活圈_Agent_App_Architecture_v1.0_交接文档.md` 第 7.3、7.4、21 节及现有 `AgentRunState` 契约。

本阶段只交付 `app/agent_runtime/state.py` 与 `conditions.py`，不启动 Graph、Runner、Tool 或 Agent。继续使用公共 `AgentRunState` 作为唯一状态模型。`new_run(run_id, user_message)` 创建待运行状态；`update_run(state, **changes)` 原子校验整份新状态，返回新实例，不改原实例，不允许改写 run ID、原始消息或 schema 版本。循环中更换 Evidence 时调用方须在同一次更新中清除旧 Proposal 和 Review；公共契约负责验证这种依赖关系。

条件函数只读取状态：`after_brief` 根据缺失信息返回 `wait_for_input` 或 `fetch_evidence`；`after_evidence` 根据 `needs_planning` 返回 `plan` 或 `finalize`；`after_review` 在 approved 时返回 `finalize`，其余审查结果在达到 3 轮上限时返回 `finalize_with_limitations`，未到上限时分别返回 `plan` 或 `fetch_evidence`。`can_retry_tool` 在已重试次数小于 2 时返回真；它不判断错误是否可重试，由下一阶段 Runner 决定。缺少相应阶段输入时条件函数拒绝调用。

不增加配置层、持久化、Graph 路由执行或新的状态契约。离线测试验证状态原子更新、旧证据失效、全部条件分支及上限边界；完整测试和契约验证仍须通过。
