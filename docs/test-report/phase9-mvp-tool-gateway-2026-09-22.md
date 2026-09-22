# Phase 9 MVP Tool Gateway 离线验证记录（2026-09-22）

## 输入与选择性合入

队友包：`15min-life-core-mvp-api-handoff-2026-09-22.zip`，SHA-256 `4a3a9dea06d1399720890e50ab1c25df1dfa3b2967e9530ec807ece2a617caed`。按 UTF-8 恢复中文路径后，包内 `MANIFEST.sha256` 的 103 个文件哈希全部一致。与当前 Agent 仓库比对：92 个同路径文件逐字节相同；另有 5 个新文档、6 个同路径差异文件。

Tool 1–6 的实现、Provider、Schema、Contract、测试与现有薄 API 均已和交付包一致，所以没有覆盖这些代码。新增 `API_交接说明_2026-09-22.md`、2026-09-22 正式验收报告及三份 `docs/reference/` 文档；只更新当前 README 与 POI 说明中的旧验收表述。交付包的 `.env.example`、`.gitignore`、`app/schemas/__init__.py`、`scripts/validate_contracts.py` 和 Core-only README 没有覆盖现有 Agent/DeepSeek 内容。

队友报告记录：2026-09-22 的 Tool 1–6 真实百度正式验收 `formal_acceptance_passed=true`，五阶段均 `complete`，总体 `high`；旧的 2026-09-19 限流失败报告继续留作历史。这是交付包中的受控验收记录，本阶段没有再次消耗百度额度。

交付包的早期 Core 总计划也使用“Phase 9”称呼真实案例验证；本报告的 **Agent Phase 9** 专指总体 Agent 架构中的 MVP Tool Gateway，不把两个阶段混为一谈。

## 实现

`app/agent_tools/mvp_gateway.py` 的 `MVPToolGateway` 实现六方法 `ToolGateway`。它接收 Geocode、POI Provider 和 Routing Provider，复用一次构造的 `POIService`、`RoutingService`、`IsochroneService`，调用原有 Location、Blindspot 与 Diagnosis 函数；不新增 DTO，不重算 GIS，不介入 Agent 路由。`from_baidu_ak` 仅构造真实 Provider，不在构造时联网。

## 验证

```bash
env -u BAIDU_MAP_AK -u RUN_BAIDU_SMOKE -u DEEPSEEK_API_KEY -u RUN_DEEPSEEK_SMOKE PYTHONDONTWRITEBYTECODE=1 \
  '/Users/liuyanhe/Desktop/15分钟生活圈智能体检与规划助手/.venv/bin/python' -m pytest -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 '/Users/liuyanhe/Desktop/15分钟生活圈智能体检与规划助手/.venv/bin/python' scripts/validate_contracts.py
git diff --cached --check -- README.md app/modules/poi/README.md \
  app/agent_tools/mvp_gateway.py tests/test_mvp_tool_gateway.py \
  docs/superpowers docs/test-report/phase9-mvp-tool-gateway-2026-09-22.md
```

在隔离工作树中使用原仓库的 `.venv/bin/python` 执行，结果为 **431 passed、4 skipped、2 warnings**；契约校验输出 `Core MVP Tool 1-6 and Agent v1 contracts validated successfully.`；本阶段自写文件的 staged whitespace 检查通过。队友 Markdown 按原哈希保留，其中 Markdown 强制换行的行尾双空格会让不加路径限制的 `git diff --cached --check` 报告 whitespace。新增 `tests/test_mvp_tool_gateway.py` 的 **11 个离线测试**覆盖六方法 v1 契约、坐标绕过 Geocode、Routing 的可重试限流错误、部分 POI 的 `null` 与 warning、Diagnosis 的部分/失败 Envelope、真实 Runner 只替换 Gateway 的普通设施查询、真实 Provider 工厂构造。测试在 socket 连接时立即失败。

## 未证与后续条件

- 本阶段没有通过真实模型 + 真实 Gateway 执行完整 Agent Run；这是 Phase 10。
- 队友正式验收使用临时的“最多 3 个路线目标/批、间隔 1.05 秒”节奏；当前 `RoutingService` 与 API 没有按 AK 跨请求共享的限速/排队。对外或并发使用真实 Gateway 前必须补齐执行层保护，并核对当时有效的百度配额。这里没有把 3 QPS 硬编码进 Tool Contract。
- 完整 Diagnosis 在队友验收中约需 10 分钟；现有同步 API 只适合开发/单用户受控演示。公网任务化、超时和取消属于 API 后续工作。
- 真实地址路径、真实百度和真实 DeepSeek 全链路，以及 Agent 内容质量尚需 Phase 10 独立验收。
