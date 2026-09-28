# 15分钟生活圈智能体检与规划助手
## Core MVP 开发交接文档 v1.1

> **历史产品计划快照**：本文记录 2026-09-18 的 Core MVP 规划，不代表当前代码状态。当前实施进度与 API 以[最新接手交接](../DeepSeek接手交接_截至AgentPhase9_2026-09-22.md)和[Phase 10 补测记录](../test-report/phase10-semantic-sampling-supplement-2026-09-24.md)为准。

**项目阶段：** Core App MVP / Contract-first Tool Architecture  
**日期：** 2026-09-18  
**赛题：** 【开源 AI 工具赛道】基于地图开放能力的“15分钟生活圈”智能体检与规划助手

---

# 0. 给接手 ChatGPT / ChatGPT Work 的执行要求

你现在是这个项目的开发导师和执行助手。项目成员共两人，二人都接近互联网开发零基础。

请遵守以下规则：

1. 不要默认用户懂 Python、API、JSON、Git、Schema、FastAPI、Pydantic、GeoJSON。
2. 实际操作时，每一步先用 1～2 句简单中文说明“这一步在做什么、为什么要做”。
3. 简单步骤可以连续安排；容易出错、影响架构或需要判断的步骤必须停下来确认。
4. 不要擅自推翻本文已经确定的架构。
5. 当前优先完成 **App Core MVP**，暂不做正式 Web UI、完整 Agent、多 Agent、MCP、复杂 Runtime。
6. 所有代码要考虑未来 Web、微信小程序、Agent、Reviewer、MCP、Docker/CI 的扩展，但不要过度设计。
7. 不允许把真实百度 AK / 密钥提交到 GitHub。
8. 每个 Tool 编码前，必须先确定输入、输出、Error、Warning、Mock、Test、README。
9. 不允许让不同 Tool 直接依赖百度原始 JSON 格式。
10. 所有 Core Tool 最终必须接入真实百度地图数据验证，不能只用 Mock 数据宣布完成。
11. Mock 只用于并行开发、Schema 验证、单元测试和降低 API 依赖；真实百度 API 才是最终事实来源。

---

# 1. 赛题目标与开发原则

项目最终要实现：

> 用户选定一个社区/街道中心点后，系统基于真实道路步行数据生成“15分钟步行等时圈”，统计民生设施，识别菜市场、药店、小学等设施的服务盲区，并生成社区生活圈体检结果。

赛题核心要求包括：

- 地理编码 / 逆地理编码
- POI 兴趣点检索
- 路径规划与距离计算
- 真实路网 15 分钟步行等时圈
- 菜市场、药店、小学等设施覆盖
- 服务盲区识别
- 可视化体检报告
- 批量距离矩阵或并发优化
- API 异常、QPS 限流等容错与降级
- 扇形采样 / 空间插值
- Git 开源、README、Docker/环境配置、自动化测试、CI/CD
- 针对真实社区的测试报告

因此总原则：

> **先确保地图事实和 GIS 计算可靠，再做 Agent。**

长期架构思想：

```text
用户
 ↓
Web / 微信小程序
 ↓
Agent
 ↓
Business Tools
 ↓
Deterministic GIS Tools
 ↓
Provider
 ↓
百度地图 API
```

一句话：

> **百度负责事实，Provider 负责取数据，GIS 负责计算，业务 Tool 负责组织，Agent 负责理解、调度和解释，前端负责展示。**

当前阶段不做完整 Agent，而是先做未来 Agent 的“手和脚”。

---

# 2. Core MVP：5 个基础 Tool + 1 个业务 Tool

已经确定：

```text
1. Location
2. POI
3. Routing
4. Isochrone
5. Blindspot
6. Diagnosis
```

## 2.1 Location
回答：“这个位置到底在哪里？”

职责：
- 地址 → 坐标
- 坐标 → 地址
- 坐标合法性检查
- 统一输出标准 `CenterPoint`

## 2.2 POI
回答：“这个地方周围有什么生活设施？”

MVP 首先支持：
- `market`
- `pharmacy`
- `primary_school`

## 2.3 Routing
回答：“从 A 实际步行到 B 需要多久？”

职责：
- 步行路径
- Route Matrix
- 批量请求
- 请求拆批
- QPS
- 缓存
- 异常处理

## 2.4 Isochrone
回答：“从中心点真实步行15分钟到底能到哪里？”

职责：
- 扇形 / 多方向采样
- 不同半径采样
- Route Matrix 测时
- 900 秒边界定位
- 边界加密
- 空间插值
- Polygon / GeoJSON 输出

## 2.5 Blindspot
回答：“哪些地方缺少基本生活服务？”

MVP 必须识别：
- 周边 1km 没有菜市场
- 周边 1km 没有药店
- 周边 1km 没有小学

## 2.6 Diagnosis
回答：“这个社区总体体检结果是什么？”

它不是新的 GIS 算法，而是业务编排 Tool：

```text
Location
+
POI
+
Routing
+
Isochrone
+
Blindspot
        ↓
DiagnosisResult
```

未来 Agent 最常调用的业务能力之一将是：

```text
diagnose_community()
```

---

# 3. 当前不要单独变成 Tool 的能力

以下能力保留，但不独立成 Core Tool：

```text
sampling       → isochrone/
interpolation  → isochrone/
coverage       → blindspot/
confidence     → 各模块结果 + diagnosis
report         → 未来 Web / Agent
```

当前也暂不开发：

```text
Agent
Reviewer
LangGraph
MCP
Runtime
Scheduler
Worker
Checkpoint
Redis
复杂多社区比较
设施智能选址
微信小程序正式版
```

---

# 4. MVP 目标仓库结构

```text
15min-life-circle-assistant/
│
├── app/
│   ├── modules/
│   │   ├── location/
│   │   │   ├── service.py
│   │   │   ├── README.md
│   │   │   └── tests/
│   │   ├── poi/
│   │   │   ├── service.py
│   │   │   ├── category_mapping.py
│   │   │   ├── README.md
│   │   │   └── tests/
│   │   ├── routing/
│   │   │   ├── service.py
│   │   │   ├── batching.py
│   │   │   ├── README.md
│   │   │   └── tests/
│   │   ├── isochrone/
│   │   │   ├── service.py
│   │   │   ├── sampling.py
│   │   │   ├── refinement.py
│   │   │   ├── interpolation.py
│   │   │   ├── README.md
│   │   │   └── tests/
│   │   ├── blindspot/
│   │   │   ├── service.py
│   │   │   ├── grid.py
│   │   │   ├── coverage.py
│   │   │   ├── README.md
│   │   │   └── tests/
│   │   └── diagnosis/
│   │       ├── service.py
│   │       ├── metrics.py
│   │       ├── README.md
│   │       └── tests/
│   │
│   ├── providers/
│   │   └── baidu/
│   │       ├── client.py
│   │       ├── geocoding.py
│   │       ├── poi.py
│   │       ├── routing.py
│   │       └── errors.py
│   │
│   ├── schemas/
│   │   ├── common.py
│   │   ├── location.py
│   │   ├── poi.py
│   │   ├── routing.py
│   │   ├── isochrone.py
│   │   ├── blindspot.py
│   │   └── diagnosis.py
│   │
│   ├── api/
│   │   ├── routes/
│   │   │   ├── location.py
│   │   │   ├── poi.py
│   │   │   └── diagnosis.py
│   │   └── main.py
│   │
│   ├── core/
│   │   ├── config.py
│   │   ├── exceptions.py
│   │   └── logging.py
│   │
│   └── main.py
│
├── contracts/
│   └── v1/
│       ├── center-point.example.json
│       ├── location-request.example.json
│       ├── location-result.example.json
│       ├── poi-search-request.example.json
│       ├── poi-search-result.example.json
│       ├── travel-time.example.json
│       ├── isochrone.example.json
│       ├── blind-spot.example.json
│       └── diagnosis-result.example.json
│
├── data/
│   ├── samples/
│   ├── fixtures/
│   └── cache/
│
├── tests/
│   ├── integration/
│   └── e2e/
│
├── demo/
├── docs/
│   ├── architecture/
│   ├── api/
│   ├── gis/
│   └── test-report/
├── scripts/
├── .github/
│   └── workflows/
├── .gitignore
├── .env.example
├── README.md
├── LICENSE
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

注意：这是目标结构，不要求第一天一次性创建所有空文件，应随着 Tool 开发逐步建立。

---

# 5. Schema 与 Contract

## 5.1 `app/schemas/`
给程序使用，作为正式结构定义。

同一种结构只定义一次。

例如 `CenterPoint` 统一放：

```text
app/schemas/common.py
```

不要在多个 Tool 中重复定义。

原则：

> **Single Source of Truth**

## 5.2 `contracts/`
给开发者阅读，同时用于：
- Mock
- 联调参考
- 文档
- 测试 fixture

例如：

```text
contracts/v1/center-point.example.json
```

用来告诉所有模块 `CenterPoint` 到底长什么样。

---

# 6. 所有 Tool 的统一 Envelope

成功：

```json
{
  "ok": true,
  "data": {},
  "warnings": [],
  "meta": {}
}
```

失败：

```json
{
  "ok": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "给人看的错误解释",
    "retryable": false
  },
  "warnings": [],
  "meta": {}
}
```

为什么需要 `warnings`：

> 数据成功返回，不代表数据完全可靠。

例如：
- 某页 POI 请求失败
- 某些 Route Matrix 目标点失败
- 数据来自缓存
- 部分采样缺失

这些可以 `ok=true`，但必须通过 `warnings` 告知下游。

---

# 7. Mock 与真实百度数据的正式开发规则

## 7.1 最终必须接真实百度数据

本项目不是纯 Mock 项目。

Core MVP 最终必须真实调用百度地图开放能力，包括：
- Geocoding
- Reverse Geocoding
- POI
- Route Matrix
- Walking Route

只用 Mock 不能宣布 Tool 完成。

## 7.2 Mock 的作用

Mock 用于：
- Provider 尚未完成时并行开发
- 验证 Schema
- 验证 Tool 输入输出
- 单元测试
- 让下游不等待上游
- 降低开发阶段 API 调用量

正式流程：

```text
Contract
↓
Schema
↓
Mock 验证
↓
Baidu Provider
↓
真实百度 API
↓
真实数据测试
↓
Tool 验收
```

## 7.3 不允许的错误方式

不要：

```text
先把6个 Tool 全部用 Mock 写完
↓
最后一天统一接百度
```

正确做法：

> **每完成一个 Tool，就尽快接真实百度数据验证。**

## 7.4 Provider 是真实百度数据的唯一正式入口

正确：

```text
Location Tool
↓
Baidu Provider
↓
真实百度 Geocoding API
```

```text
POI Tool
↓
Baidu Provider
↓
真实百度 POI API
```

## 7.5 AK 安全规则

已有浏览器端 AK，用于地图 Demo。

进入 Core App 后，还需要服务端 AK，用于 Geocoding、POI、Routing 等 Web Service。

真实 AK：
- 放 `.env`
- `.env` 必须被 `.gitignore`
- `.env.example` 只保留变量名，不写真实值

---

# 8. 公共数据结构：CenterPoint

Location → POI 的第一个正式连接协议：

```json
{
  "lng": 121.506123,
  "lat": 31.282456,
  "crs": "BD09LL"
}
```

字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `lng` | number | 经度 |
| `lat` | number | 纬度 |
| `crs` | string | 坐标参考系 |

MVP 统一：

```text
crs = BD09LL
```

命名铁律：

```text
lng
lat
```

不要混用：

```text
longitude
latitude
lon
x
y
```

GeoJSON 坐标数组顺序：

```text
[lng, lat]
```

即经度在前、纬度在后。

---

# 9. Tool 1：Location

## 9.1 核心职责

> 把不同形式的位置输入统一转换为 `CenterPoint`。

不是地图 UI，而是位置标准化 Tool。

## 9.2 支持两种输入

### 地址

```text
同济大学四平路校区
```

流程：

```text
地址文字
↓
Baidu Geocoding Provider
↓
真实百度结果
↓
标准化
↓
CenterPoint
```

### 坐标

```text
121.506123, 31.282456
```

流程：

```text
坐标
↓
合法性检查
↓
坐标系检查
↓
CenterPoint
```

## 9.3 Address Request

```json
{
  "schema_version": "1.0",
  "input": {
    "type": "address",
    "query": "同济大学四平路校区",
    "city": "上海市"
  }
}
```

## 9.4 Coordinate Request

```json
{
  "schema_version": "1.0",
  "input": {
    "type": "coordinate",
    "lng": 121.506123,
    "lat": 31.282456,
    "crs": "BD09LL"
  }
}
```

## 9.5 成功返回

```json
{
  "ok": true,
  "data": {
    "center": {
      "lng": 121.506123,
      "lat": 31.282456,
      "crs": "BD09LL"
    },
    "label": "同济大学四平路校区",
    "formatted_address": "上海市杨浦区四平路1239号",
    "source": "baidu_geocoding"
  },
  "warnings": [],
  "meta": {
    "schema_version": "1.0",
    "provider": "baidu"
  }
}
```

下游真正依赖：

```text
data.center
```

## 9.6 错误返回

```json
{
  "ok": false,
  "error": {
    "code": "LOCATION_NOT_FOUND",
    "message": "未找到匹配的位置。",
    "retryable": false
  },
  "warnings": [],
  "meta": {
    "schema_version": "1.0",
    "provider": "baidu"
  }
}
```

第一版 Error Code：

```text
LOCATION_NOT_FOUND
INVALID_COORDINATE
PROVIDER_ERROR
RATE_LIMITED
TIMEOUT
INVALID_REQUEST
```

## 9.7 目录

```text
app/modules/location/
├── service.py
├── README.md
└── tests/

app/providers/baidu/geocoding.py
app/schemas/common.py
app/schemas/location.py
```

## 9.8 Provider 与 Service 的边界

Provider：

```text
我们的 query
↓
百度参数
↓
真实百度 API
↓
解析百度原始响应
```

Service：

```text
Provider结果
↓
Location Service
↓
标准 LocationResult
```

## 9.9 Location 不负责

- POI
- Routing
- Isochrone
- Blindspot
- UI
- Report

## 9.10 验收标准

```text
✅ 地址 → CenterPoint
✅ 坐标 → CenterPoint
✅ 至少一个地址通过真实百度 API 验证
✅ CenterPoint 格式统一
✅ 百度原始格式不泄露给下游
✅ Error
✅ Mock
✅ README
✅ Tests
```

---

# 10. Tool 2：POI

## 10.1 核心职责

> 给定标准 `CenterPoint`，检索指定类型生活设施，并转换成项目统一 POI 格式。

POI 不负责“地点在哪里”，只接受已经确定的 CenterPoint。

## 10.2 Location → POI 的唯一连接点

```text
LocationResult.data.center
```

不要把整个 LocationResult 传给 POI。

正确：

```text
POI(location_result.data.center)
```

## 10.3 POI Search Request

```json
{
  "schema_version": "1.0",
  "center": {
    "lng": 121.506123,
    "lat": 31.282456,
    "crs": "BD09LL"
  },
  "facility_types": [
    "market",
    "pharmacy",
    "primary_school"
  ],
  "search_radius_m": 1500
}
```

## 10.4 1500m 的含义

1500m 只是候选 POI 搜索范围，不等于 15 分钟真实可达范围。

真正可达性后续由：

```text
Routing + Isochrone
```

判断。

## 10.5 内部设施类型标准

MVP：

```text
market
pharmacy
primary_school
```

不要在内部混用：

```text
菜市场
农贸市场
生鲜市场
药店
药房
小学
学校
```

内部统一使用英文 Enum。

## 10.6 `category_mapping.py`

负责把内部标准类型映射为适合百度检索的关键词 / 分类组合。

概念：

```text
market
↓
菜市场 / 农贸市场 / 生鲜市场

pharmacy
↓
药店 / 药房

primary_school
↓
小学
```

实际策略必须用真实百度数据验证。

## 10.7 POI 数据清洗

流程：

```text
真实百度原始 POI
↓
Provider解析
↓
多关键词合并
↓
去重
↓
标准分类
↓
标准 POI[]
```

## 10.8 Standard POI

```json
{
  "poi_id": "baidu:123456",
  "name": "示例大药房",
  "facility_type": "pharmacy",
  "location": {
    "lng": 121.508100,
    "lat": 31.280900,
    "crs": "BD09LL"
  },
  "address": "上海市杨浦区XX路XX号",
  "source": "baidu"
}
```

## 10.9 POI Result

```json
{
  "ok": true,
  "data": {
    "center": {
      "lng": 121.506123,
      "lat": 31.282456,
      "crs": "BD09LL"
    },
    "pois": [
      {
        "poi_id": "baidu:123456",
        "name": "示例大药房",
        "facility_type": "pharmacy",
        "location": {
          "lng": 121.508100,
          "lat": 31.280900,
          "crs": "BD09LL"
        },
        "address": "上海市杨浦区XX路XX号",
        "source": "baidu"
      }
    ],
    "counts": {
      "market": 3,
      "pharmacy": 12,
      "primary_school": 2
    }
  },
  "warnings": [],
  "meta": {
    "schema_version": "1.0",
    "provider": "baidu",
    "returned_count": 17
  }
}
```

## 10.10 未检索到与 API 失败严格分开

成功但无结果：

```json
{
  "ok": true,
  "data": {
    "pois": [],
    "counts": {
      "market": 0,
      "pharmacy": 0,
      "primary_school": 0
    }
  }
}
```

API 失败：

```json
{
  "ok": false,
  "error": {
    "code": "PROVIDER_ERROR",
    "message": "百度地图 POI 服务请求失败。",
    "retryable": true
  }
}
```

不能把 API 失败伪装成“0 个设施”。

## 10.11 部分失败 Warning

```json
{
  "ok": true,
  "warnings": [
    {
      "code": "PARTIAL_POI_RESULTS",
      "message": "小学设施数据可能不完整。"
    }
  ]
}
```

## 10.12 目录

```text
app/modules/poi/
├── service.py
├── category_mapping.py
├── README.md
└── tests/

app/providers/baidu/poi.py
app/schemas/poi.py
```

## 10.13 Provider 与 Service

Provider 负责：
- URL
- 参数
- 服务端 AK
- HTTP
- 分页
- 百度错误码
- 超时
- 限流

Service 负责：
- facility_type
- 多关键词
- 合并
- 去重
- 标准格式
- counts
- warnings

## 10.14 POI 不负责

- 步行时间
- 15分钟可达性
- Isochrone
- Blindspot
- 最终评分

## 10.15 验收标准

```text
✅ 接受统一 CenterPoint
✅ 支持 market / pharmacy / primary_school
✅ 接入百度真实 POI 数据
✅ 数据标准化
✅ 去重
✅ 百度原始字段不泄露给下游
✅ 区分无结果与 API 失败
✅ warnings
✅ Mock
✅ README
✅ Tests
```

---

# 11. Location → POI 正式对接规范

```text
resolve_location()
        ↓
LocationResult
        ↓
if ok == false:
    停止
        ↓
提取 data.center
        ↓
构造 POISearchRequest
        ↓
search_pois()
```

实例：

Location 输入：

```json
{
  "schema_version": "1.0",
  "input": {
    "type": "address",
    "query": "同济大学四平路校区",
    "city": "上海市"
  }
}
```

Location 返回：

```json
{
  "ok": true,
  "data": {
    "center": {
      "lng": 121.506123,
      "lat": 31.282456,
      "crs": "BD09LL"
    }
  }
}
```

只抽取：

```json
{
  "lng": 121.506123,
  "lat": 31.282456,
  "crs": "BD09LL"
}
```

然后组装：

```json
{
  "schema_version": "1.0",
  "center": {
    "lng": 121.506123,
    "lat": 31.282456,
    "crs": "BD09LL"
  },
  "facility_types": [
    "market",
    "pharmacy",
    "primary_school"
  ],
  "search_radius_m": 1500
}
```

传给：

```text
search_pois()
```

---

# 12. 两人并行开发规则

如果 A 负责 Location、B 负责 POI：

B 不需要等待 A。

B 直接使用：

```text
contracts/v1/center-point.example.json
```

作为 Mock 输入。

因此：

```text
Location 未完成
≠
POI 不能开发
```

只要 Contract 不变，最后把 Mock CenterPoint 换成真实：

```text
LocationResult.data.center
```

即可。

---

# 13. 第一批必须建立的 Contract 文件

先创建：

```text
contracts/v1/
├── center-point.example.json
├── location-request.example.json
├── location-result.example.json
├── poi-search-request.example.json
└── poi-search-result.example.json
```

原则：

> **先 Contract → 再 Schema → 再 Mock → 再真实 Provider → 再 Service。**

---

# 14. 第一批 Python Schema

创建：

```text
app/schemas/common.py
app/schemas/location.py
app/schemas/poi.py
```

其中：

`common.py`

```text
CenterPoint
Warning
Error
Meta
```

`location.py`

```text
LocationRequest
LocationResult
```

`poi.py`

```text
POISearchRequest
POI
POISearchResult
FacilityType
```

---

# 15. Git 分支建议

共同 Contract：

```text
chore/contracts-v1
```

优先 merge 到 `main`。

然后：

```text
feat/location-tool
feat/poi-tool
```

两个 Tool Branch 都从最新 main 创建。

Contract 一旦 merge，在当前开发轮次不随意修改。

必须修改时：

1. 通知另一位成员
2. 说明原因
3. 检查下游影响
4. 改 Contract
5. 改 Schema
6. 改 Mock
7. 改 Test
8. 再继续开发

---

# 16. Tool 完成标准：5+1

每个 Core Tool 必须具备：

1. Contract / Schema
2. Implementation
3. Mock
4. Tests
5. README
6. **Real Provider Verification**

第 6 条非常重要：

> 至少一次通过真实百度 API 验证。

---

# 17. 整个 Core MVP 开发顺序

```text
Step 0
Contracts v1
↓
Schemas common/location/poi

Step 1
Location Tool
Mock + 真实百度 Geocoding

Step 2
POI Tool
Mock + 真实百度 POI

Step 3
Routing Contract + Tool
真实百度 Route Matrix

Step 4
Isochrone Contract + Tool

Step 5
Blindspot Contract + Tool

Step 6
Diagnosis Contract + Tool

Step 7
API

Step 8
Integration Tests

Step 9
Core App MVP 验收

────────────

然后做正式 Web

────────────

最后逐步做：
AI解释
Single Agent
Reviewer
MCP
微信小程序
```

---

# 18. Core App MVP 最终毕业标准

在没有正式 Web 的情况下，输入真实社区中心点，系统应得到：

```text
✅ 标准位置
✅ 真实百度 POI
✅ 菜市场 / 药店 / 小学
✅ 真实步行时间
✅ 批量 Route Matrix
✅ 15分钟真实路网近似等时圈
✅ GeoJSON
✅ 1km 服务盲区
✅ 基础指标
✅ Confidence / 数据质量信息
✅ warnings
✅ API 错误处理
✅ 可重复运行
```

随后补齐：

```text
README
Docker
自动化测试
CI/CD
真实社区测试报告
```

---

# 19. 最重要的项目原则

1. **LLM 不负责事实计算。**
2. **百度原始数据不能成为内部公共 Contract。**
3. **Tool 之间只共享最小必要数据。**
4. **没有查到 ≠ API 失败。**
5. **先 Contract，后代码。**
6. **Mock 是开发手段，不是真实事实源。**
7. **每完成一个 Tool，都尽早用真实百度数据验证。**
8. **不要为了未来 Agent 提前把系统复杂化。**
9. **所有 Tool 最终必须独立可测试。**

---

# 20. 接手后的下一项实际工作

## Task A：建立 Contract v1

创建：

```text
contracts/v1/
```

并建立：

```text
center-point.example.json
location-request.example.json
location-result.example.json
poi-search-request.example.json
poi-search-result.example.json
```

## Task B：建立第一批 Schema

创建：

```text
app/schemas/common.py
app/schemas/location.py
app/schemas/poi.py
```

使用 Pydantic 与 Contract 对齐。

## Task C：验证 Contract ↔ Schema

确保 5 个 example JSON 都可以被对应 Schema 正确读取。

## Task D：配置真实百度服务端 Provider

Location 真正开发前：

- 创建 / 确认百度服务端 AK
- 放入 `.env`
- `.env` 加入 `.gitignore`
- `.env.example` 只写变量名

## Task E：Location Tool

```text
Mock
↓
Schema
↓
Baidu Geocoding Provider
↓
真实地址测试
↓
Location Service
↓
Tests
↓
README
```

## Task F：POI Tool

```text
Mock CenterPoint
↓
Schema
↓
Baidu POI Provider
↓
真实设施测试
↓
分类 / 去重 / 清洗
↓
POI Service
↓
Tests
↓
README
```

---

# 21. 当前项目已有状态

已经完成：

```text
✅ 百度地图开发者认证
✅ 浏览器端 AK
✅ GitHub 仓库
✅ GitHub Desktop
✅ VS Code
✅ Clone 仓库
✅ 百度地图基础 Demo 成功显示
✅ .gitignore
✅ 本地 AK 文件不上传
✅ map-demo.template.html
✅ Git Branch
✅ Commit
✅ Push / Publish
✅ Pull Request
✅ Merge 到 main
```

无需重复。

下一阶段正式进入：

> **Contract-first + Real-Baidu-validated Core Tool Development**

---

# 22. 给 ChatGPT Work 的第一条执行指令

请严格按照本交接文档继续执行，不重新设计总体架构。

先检查当前 GitHub 仓库，然后从：

```text
Task A：contracts/v1
```

开始。

当前只做到：

```text
Contract v1
+
第一批 Schema
```

完成并验证后，再进入：

```text
真实百度 Provider
+
Location Tool
```

每一步先用简单中文解释：

> “这一步在做什么”  
> “为什么要做”

然后再执行。
