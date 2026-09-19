# Core MVP 架构与边界

依据《15分钟生活圈 Core MVP 开发交接文档 v1.1》。当前阶段是 Contract-first 的 Core App，不做正式 Web、Agent、MCP 或复杂 Runtime。

```text
Location ── CenterPoint ──► POI
     │                     │
     └────────┬────────────┘
              ▼
           Routing ──► Isochrone ──► Blindspot
              └───────────┬────────────┘
                          ▼
                    Diagnosis（业务编排）

各 Tool ──► Baidu Provider ──► 百度地图 Web Service
```

- 五个基础 Tool：Location、POI、Routing、Isochrone、Blindspot；Diagnosis 负责组合结果，不是新的 GIS 算法。
- `app/schemas/` 是程序使用的结构定义；`contracts/v1/` 是示例、Mock 和联调依据。公共 `CenterPoint` 只在 `app/schemas/common.py` 定义一次，字段为 `lng`、`lat`、`crs="BD09LL"`。
- Tool 的对外结果统一使用 `ok`、`data` 或 `error`、`warnings`、`meta`。`0` 表示确认没有设施；`null` 表示没有查询或数据不完整，不能据此判断盲区。
- 百度原始 JSON 只留在 Provider；Service 输出项目内部的标准结构。POI 的 1500 米只是候选检索范围，不是实际步行 15 分钟可达范围。
- Mock 与测试用于开发；每个 Tool 的验收还要有真实百度数据。GeoJSON 顺序为 `[lng, lat]`。

目标目录中尚无实现的模块只保留目录，不添加会被误认成可运行能力的空 `service.py`。
