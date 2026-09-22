# Tool 1–6 正式端到端验收记录（2026-09-22）

## 结论

**通过。** 本次使用真实百度服务、全部三类设施以及 Tool 1–6 默认业务参数，最终 `formal_acceptance_passed=true`。所有阶段完整，整体质量为 `high`。

## 运行条件

- 输入中心：BD09LL 坐标 `121.506123, 31.282456`。
- 设施类别：`market`、`pharmacy`、`primary_school`。
- 15 分钟阈值：900 秒。
- Blindspot 服务距离：1000 米；网格：200 米。
- Tool 4：24 个方向、192 个路由样本。
- 百度控制台批量算路上限：3 QPS。
- 验收节奏：每批最多 3 个目标，批次间隔 1.05 秒。
- AK 仅从本机环境变量读取；未保存或输出 AK、原始百度响应或带密钥 URL。

## 结果

| 检查项 | 结果 |
| --- | --- |
| Diagnosis Envelope | `ok=true` |
| 阶段状态 | Location、POI、Routing、Isochrone、Blindspot 全部 `complete` |
| 总体质量 | `high` |
| POI | 市场 34、药店 43、小学 22；18 次 POI HTTP 请求 |
| Isochrone | 24/24 方向有效；192/192 路由样本成功；`high` |
| POI 搜索半径 | 2126m；计算最低值 2126m；通过 |
| Blindspot | 55 个网格；1389 个目标对；1388 条成功路线、1 条明确无路线、0 条 unavailable |
| 未知区 | 三类 `unknown_ratio=0`；0 个 unknown Feature |
| 盲区 | 4 个有效 Feature，全部位于分析域内 |
| 几何 | 等时圈、盲区及未知区拓扑检查全部通过 |
| 网络调用 | 578 次批量算路 HTTP、18 次 POI HTTP；未触发 Provider 限流或安全熔断 |
| 最终判定 | `formal_acceptance_passed=true` |

分类指标：

| 类别 | POI | 15 分钟确认可达 | 最近步行距离/时间 | covered | blind | unknown |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| 市场 | 34 | 4 | 381m / 325s | 0.9955475905 | 0.0044524095 | 0 |
| 药店 | 43 | 7 | 117m / 99s | 1.0 | 0 | 0 |
| 小学 | 22 | 2 | 933m / 797s | 0.9640852416 | 0.0359147584 | 0 |

唯一警告为 `NO_ROUTE_FOR_TARGETS`，对应一个百度明确返回无可用步行路线的目标，不是数据未知或 Provider 失败，因此不影响完整性判定。

## 与 2026-09-19 记录的关系

`tool1-6-formal-acceptance-2026-09-19.md` 记录了当时因未按账户真实 3 QPS 上限节流而产生的部分结果。该文件作为历史排障记录保留；本文件是当前正式验收结论。

## API 侧注意事项

本次验收的节流位于临时验收器，不属于 Tool Contract。现有正式 `RoutingService` 仍以 50 个目标为最大批次，API 必须增加跨请求共享的 3 QPS 限速/排队机制。完整 Diagnosis 约需 10 分钟，公网 API 应考虑异步任务模型。

