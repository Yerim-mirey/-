# Phase 10 准备：真实模型适配器记录（2026-09-22）

**仓库：** `/Users/liuyanhe/Desktop/15分钟生活圈智能体检与规划助手/`
**分支：** `feat/mock-e2e`
**范围说明：** 这是 Phase 10 的前置准备（真实模型适配器），**不是 Phase 10 本身**。Phase 9 的 MVP Tool Gateway 仍未开始，完整真实链路验收仍未执行。

## 1. 交付物

| 文件 | 作用 |
| --- | --- |
| `app/providers/deepseek.py` | `DeepSeekLLM` 实现 `StructuredLLM` 协议，三个 Agent 可直接注入 |
| `tests/test_deepseek_provider.py` | 22 项离线契约测试，用注入的 `httpx.MockTransport`，零网络 |
| `scripts/verify_agent_llm_live.py` | 有界、可选的真实模型冒烟入口，只打印公开摘要 |
| `tests/integration/test_deepseek_live.py` | 门控的真实冒烟 pytest 包装（默认跳过） |
| `.env.example` / `README.md` | `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`、`RUN_DEEPSEEK_SMOKE` 的配置与运行说明 |

## 2. 接口约束与实现决定

依据 [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)、[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)、[Error Codes](https://api-docs.deepseek.com/quick_start/error_codes)：

1. **JSON Output 只接受 `response_format={'type':'json_object'}`，不接受 JSON Schema。** 适配器把 `response_schema` 渲染进系统提示词；返回对象仍由各 Agent 的 Pydantic 模型校验，与受控模型测试的路径一致。
2. **官方要求提示词含 "json" 字样并给出格式示例。** `render_system_prompt` 在角色提示词之后追加中文输出指令与 schema 示例，原提示词保持不变。
3. **`max_tokens` 必须留足。** 默认 8192（可用构造参数覆盖）。
4. **思考模式默认开启、默认 effort 为 high，且 reasoning tokens 计入 `max_tokens`。** 结构化抽取的输出很小，思考会占满预算；适配器默认关闭思考（可 `thinking=True` 或 `DEEPSEEK_THINKING=1` 开启）。
5. **官方说明 JSON 模式偶尔返回空内容。** 空内容、非 JSON、非对象分别抛出 `LLMResponseError`；命中 `max_tokens` 时抛出子类 `LLMOutputTruncated`（明确区分截断与格式错误）。
6. **HTTP 与网络失败** 抛 `LLMTransportError`，带状态码与服务端 message。

所有失败都会传播到 Runner，被收敛成 `failed` Run，**不会伪装成成功**。

## 3. 首次真实调用发现并修复的问题

首次冒烟（`max_tokens=4096`、思考默认开启）结果：

```text
orchestrator: intent=planning_analysis facilities=['market', 'pharmacy', 'primary_school'] missing=[]
planning: FAILED LLMResponseError: DeepSeek content is not a JSON object
reviewer: FAILED LLMResponseError: DeepSeek returned empty content
usage: completion_tokens=4096 (= max_tokens), reasoning_tokens=2499, finish_reason 未记录
```

诊断：`completion_tokens` 正好等于 `max_tokens`，其中 2499 是 reasoning tokens，JSON 输出被截断。修复为「默认关闭思考 + 提高预算 + 检测 `finish_reason=length`」后：

```text
orchestrator: intent=planning_analysis facilities=['market', 'pharmacy', 'primary_school'] missing=[]
planning: issues=2 recommendations=2 round=1
reviewer: status=approved issues=0 missing_evidence=0
usage: completion_tokens=69, finish_reason=stop
```

`completion_tokens` 由 4096 降到 69，reasoning tokens 消失。**这个问题只有真实调用才会暴露，受控模型测试不可能发现。**

## 4. 真实调用观测（有界，共 12 次请求）

运行命令：

```bash
set -a; source .env; set +a
RUN_DEEPSEEK_SMOKE=1 RUN_DEEPSEEK_SMOKE_ROUNDS=3 python -m scripts.verify_agent_llm_live
```

| 观测项 | 结果 |
| --- | --- |
| 请求数 | 12（3 次早期诊断 + 9 次矩阵） |
| Orchestrator 输出合同有效 | 4/4；每次都正确识别 `planning_analysis` 与三类设施 |
| Planning 输出合同有效 | 3/4；**1 次**被 Phase 4 确定性校验拒绝（"Model output conflicts with the planning input or evidence"） |
| Reviewer 输出合同有效 | 4/4 |
| 9 次矩阵轮 | **9/9 合同有效**，拒绝原因为空 |
| `prompt_cache_hit_tokens` | 3840（第二次起命中缓存，prompt 成本显著下降） |

**必须诚实记录两点：**

1. **那次 Planning 拒绝没有被复现，也没有被解释。** 当时脚本只打印"拒绝原因"、不保留模型原始输出，因此无法判断它越界在哪（引用不存在的证据 ID、设施类别超出 Brief、还是其它）。当前 9 连测全部通过，但样本太小，**不能据此认定真实模型的输出稳定性已经达标**。
2. **reviewer 三次返回 `revision_required`（3–4 个问题）是正确行为，不是失败。** 冒烟用的提案故意让市场盲区的证据去支撑药店/小学建议，Reviewer 识别出了不匹配。这说明"输出符合 Schema"与"内容正确"是两件事——本节所有数字只说明前者。

## 5. 仍未完成 / 明确边界

- **真实语义质量未评测。** 本记录只覆盖"输出能否通过公共 Schema 与确定性引用校验"。意图提取准确率、规划建议是否超出证据、审查是否漏判，都需要 Phase 10 的评测集与人工抽样。
- **未跑完整 Agent 链路。** 真实模型 + 真实 Tool 的 `run_agent` 端到端验收依赖 Phase 9 的 `MVPToolGateway`。
- **未做重试与限流策略。** 适配器不自动重试；429/503 的退避策略应由 Runtime 决定，目前 Runner 只按 `retryable` 与预算处理 Tool 失败。模型失败不消耗 Tool 重试预算。
- **思考模式未做质量对比。** 关闭思考是基于结构化抽取的成本/可靠性取舍，尚未对比开启思考后的输出质量差异。
- **报告不含密钥、原始提示词与完整模型输出。** 密钥只存本机未入 Git 的 `.env`。
