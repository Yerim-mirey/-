# 15分钟生活圈智能体检与规划助手 MVP 总工作计划 v1.0

## 一、MVP 的最终目标

最终交给评委的 MVP 应该是一款真正可以运行的 Web 应用。

用户能够：

```text
输入地点 / 搜索地址 / 点击地图
            ↓
       确定中心点
            ↓
        开始社区体检
            ↓
系统调用现有 Tool 1–6
            ↓
获得真实百度地图数据
            ↓
生成：
15分钟步行等时圈
真实 POI
服务盲区
设施统计
数据质量信息
            ↓
在 Web 地图和报告面板中展示
```

同时项目必须做到：

```text
可以运行
可以测试
可以部署
可以让评委快速复现
可以解释算法
可以用真实社区证明结果
```

---

## 二、当前已经完成的部分

| 模块 | 状态 | 作用 |
|---|---:|---|
| Tool 1 `Location` | ✅ | 地址/坐标 → 标准 CenterPoint |
| Tool 2 `POI` | ✅ | 搜索菜市场、药店、小学等设施 |
| Tool 3 `Routing` | ✅ | 百度真实步行距离/时间、批量 Route Matrix |
| Tool 4 `Isochrone` | ✅ | 真实步行测时 → 15分钟等时圈 |
| Tool 5 `Blindspot` | ✅ | 识别设施服务盲区 |
| Tool 6 `Diagnosis` | ✅ | 编排前五个 Tool，形成体检结果 |
| Contract / Schema | ✅ | Tool 间标准数据协议 |
| 百度 Provider | ✅ | Core Tool 获取真实百度数据 |
| Tool 单元测试 | ✅ | 各 Tool 独立正确性验证 |

从现在开始，**不要创建 Tool 7**。

---

## 三、剩余 MVP 总路线

```text
阶段 0
冻结 Tool 1–6 基线
        ↓
阶段 1
Tool 1–6 总集成验证
        ↓
阶段 2
FastAPI API 层
        ↓
阶段 3
真实 E2E 测试
        ↓
阶段 4
正式 Web 基础框架
        ↓
阶段 5
地图交互
        ↓
阶段 6
结果地图可视化
        ↓
阶段 7
体检报告 / 图表
        ↓
阶段 8
异常、加载、数据质量 UX
        ↓
阶段 9
真实社区案例验证
        ↓
阶段 10
README / Docker / Scripts / CI
        ↓
阶段 11
最终 QA + 比赛交付
        ↓
========================
Core Competition MVP 完成
========================
```

之后才进入：

```text
AI结果解释
→ Single Agent
→ Reviewer
→ MCP
→ 微信小程序
```

---

## 阶段 0｜冻结 Tool 1–6 基线

### 目标

确认现在六个 Tool 的合并版本就是后续开发的唯一基础。

后续 Web / API 不允许各自重新造一套 POI、等时圈或 Blindspot 逻辑。

### 工作内容

首先将 Tool 1–6 全部合并到 `main`。

然后运行现有项目的：

```bash
python scripts/validate_contracts.py
```

以及：

```bash
python -m pytest -q
```

如果仓库实际脚本名称不同，以当前仓库为准。

同时确认：

```text
app/modules/
├── location/
├── poi/
├── routing/
├── isochrone/
├── blindspot/
└── diagnosis/
```

全部存在并正常工作。

检查：

```text
app/schemas/
```

是否仍然使用统一：

```text
common.py
```

不能因为多人合并产生多个不同版本的：

```text
CenterPoint
Warning
Meta
Error
```

### 本阶段产物

一个确定的：

```text
MVP Core Baseline
```

建议 Git Tag：

```text
core-tools-v1
```

如果你们暂时还不会 Git Tag，可以先不做，至少确保：

```text
main
```

是唯一可信版本。

### 验收标准

必须满足：

```text
✅ Contracts 全部验证通过
✅ pytest 全部通过
✅ Tool 1–6 都在 main
✅ 没有真实 AK 被提交
✅ .env 仍被 ignore
✅ 没有冲突版 common.py
```

---

## 阶段 1｜Tool 1–6 总集成验证

这是下一步非常重要的工作。

### 目标

验证：

> 六个 Tool 各自能运行，不仅如此，它们放在一起也真的能完成一次社区体检。

这是第一次真正的“总装”。

---

### 工作内容

选择一个真实测试中心点。

开发阶段可以继续使用：

```text
同济大学四平路校区附近
```

作为 smoke test。

但是最终比赛案例之后还需要正式选择一个：

```text
真实社区 / 街道
```

完整运行：

```text
Location
↓
CenterPoint

├→ POI
│
├→ Routing
│
├→ Isochrone
│
├→ Blindspot
│
└→ Diagnosis
```

重点检查 Tool 之间：

#### 1. 坐标是否统一

全部应使用当前统一 Contract：

```text
lng
lat
BD09LL
```

---

#### 2. POI 是否正常进入后续 Tool

尤其检查：

```text
poi_id
location
facility_type
```

有没有丢失。

---

#### 3. Routing 数据是否可以被 Isochrone 使用

检查：

```text
target_id
distance_m
duration_s
status
```

---

#### 4. Isochrone GeoJSON 是否合法

至少：

```text
type = Polygon
```

并且：

```text
coordinates = [lng, lat]
```

外环闭合。

---

#### 5. Blindspot 是否真正使用实际 POI

不能：

```text
POI 请求失败
↓
Blindspot 误判成“这里没有设施”
```

数据不完整必须传递 Warning / Confidence。

---

#### 6. Diagnosis 是否完整保存上游 Warning

例如 Routing 部分失败：

```text
PARTIAL_ROUTING_RESULTS
```

Diagnosis 不能把它吃掉。

---

### 测试类型

至少建立：

```text
tests/integration/
```

这里测试：

```text
Tool ↔ Tool
```

与各 Tool 自己的单元测试不同。

---

### 验收标准

必须能够执行一次：

```text
真实 CenterPoint
↓
Diagnosis
↓
完整 DiagnosisResult
```

并包含：

```text
POI
Isochrone
Blindspot
Metrics
Quality / Confidence
Warnings
```

具体字段名称以当前已经完成的 `DiagnosisResult Schema` 为准。

**不要为了前端重新发明字段。**

---

## 阶段 2｜FastAPI API 层

### 目标

把 Python 内部 Tool 变成：

> Web、小程序、未来 Agent 都可以通过 HTTP 调用的服务。

---

### 工作内容

#### 技术方案

后端：

```text
FastAPI
```

API 层只能：

```text
收请求
↓
验证 Schema
↓
调用现有 Tool
↓
返回现有 Result
```

**API 层不重新计算 GIS。**

---

#### 至少建立两个真正需要的接口

##### A. Location

供地址搜索使用。

概念上：

```text
POST /api/v1/location/resolve
```

输入输出必须复用：

```text
LocationRequest
LocationResult
```

不要定义第二套。

---

##### B. Diagnosis

Web 最核心接口。

概念上：

```text
POST /api/v1/diagnosis
```

必须复用已经完成的：

```text
DiagnosisRequest
DiagnosisResult
```

具体字段必须以当前 Tool 6 Schema 为准。

---

#### 建议再有

```text
GET /health
```

返回：

```json
{
  "status": "ok"
}
```

用于：

- Docker；
- 部署；
- CI；
- 判断服务是否启动。

---

#### FastAPI `/docs`

启动后应该能够访问：

```text
/docs
```

然后在浏览器中直接：

```text
输入 DiagnosisRequest
↓
Try it out
↓
Execute
↓
看到完整 DiagnosisResult
```

---

#### 安全要求

服务端百度 AK：

```text
BAIDU_MAP_AK
```

只能：

```text
.env
```

不能：

```text
写进 Python
提交 GitHub
返回给浏览器
```

---

#### CORS

如果：

```text
Web = localhost:5173
Backend = localhost:8000
```

必须正确配置开发环境 CORS。

正式环境不要无脑永久：

```text
*
```

---

### 验收标准

```text
✅ /health 正常
✅ /docs 正常
✅ Location API 正常
✅ Diagnosis API 正常
✅ API 输入使用现有 Schema
✅ API 输出使用现有 Result
✅ 错误状态不导致服务器崩溃
✅ Server AK 不暴露
```

---

## 阶段 3｜完整 E2E 测试

### 目标

验证从用户请求到 `DiagnosisResult` 的完整系统链路。

### 工作内容

Integration 和 E2E 不完全一样。

Integration 问：

> Tool A 和 Tool B 能不能接？

E2E 问：

> 从用户发请求一直到最终结果，整个系统能不能跑完？

---

#### E2E 流程

至少测试：

```text
地址
↓
Location API
↓
CenterPoint
↓
Diagnosis API
↓
真实百度数据
↓
DiagnosisResult
```

以及：

```text
直接 CenterPoint
↓
Diagnosis API
↓
DiagnosisResult
```

如果当前 Diagnosis Contract 只接受其中一种方式，以现有 Schema 为准，不要强行新增。

---

#### 测试分成两类

##### A. 自动 CI 测试

不能依赖真实百度 AK。

使用：

```text
Mock Provider
Fixtures
```

这样 GitHub Actions 能稳定运行。

---

##### B. Live Smoke Test

使用真实：

```text
BAIDU_MAP_AK
```

验证：

```text
真实百度 API
```

但：

```text
不放进普通 CI
```

否则：

- 没 AK；
- 网络波动；
- 配额；
- QPS；

都会导致 CI 随机失败。

---

### 验收标准

存在：

```text
tests/e2e/
```

并且至少有：

```text
Mock E2E
+
Live Smoke Test
```

两个层级。

---

## 阶段 4｜正式 Web 项目初始化

### 目标

建立正式 Web 项目和一个主页面，作为地图交互与体检报告的界面基础。

### 工作内容

到这里才正式开始做用户界面。

#### 推荐 MVP 技术

如果当前仓库还没有确定前端框架，建议：

```text
Vue 3
+
Vite
+
Baidu Maps JavaScript API 4.0
+
ECharts
```

不要为了未来小程序现在改成复杂跨端架构。

未来小程序：

```text
微信小程序
↓
同一个 FastAPI
↓
同一个 Tool 1–6
```

核心后端完全复用。

---

#### 建议目录

```text
web/
├── src/
│   ├── pages/
│   ├── components/
│   ├── map/
│   ├── report/
│   ├── api/
│   ├── stores/
│   └── utils/
├── public/
├── package.json
└── ...
```

---

#### 页面不要做很多

MVP：

> **一个主页面即可。**

结构：

```text
┌───────────────────────────────────────┐
│ 地址搜索 / 中心点 / 开始体检           │
├──────────────────────┬────────────────┤
│                      │                │
│                      │   体检报告      │
│       百度地图        │                │
│                      │   设施统计      │
│                      │   盲区情况      │
│                      │   数据质量      │
│                      │                │
└──────────────────────┴────────────────┘
```

### 验收标准

Web 项目可运行；一个主页面具备地址搜索、中心点、开始体检、地图和报告的布局基础。前端技术选型遵循本阶段建议（前提是仓库尚未确定框架）。

---

## 阶段 5｜Web 地图交互

### 目标

用户能够真正选择一个分析中心点。

---

### 工作内容

#### 1. 地图显示

使用浏览器端百度 AK。

注意：

> 浏览器端 AK ≠ 服务端 AK。

两者不要混。

---

#### 2. 点击地图选点

用户点击：

```text
地图
↓
Marker
↓
CenterPoint
```

内部必须标准化为：

```text
{lng, lat, crs}
```

---

#### 3. 地址搜索

```text
输入地址
↓
Location API
↓
CenterPoint
↓
地图移动到该位置
↓
Marker
```

---

#### 4. 一个明确的按钮

例如：

```text
开始体检
```

点击后：

```text
POST /api/v1/diagnosis
```

---

### 验收标准

用户能够通过：

```text
地图点击
或
地址搜索
```

得到同一种：

```text
CenterPoint
```

然后发起一次 Diagnosis。

---

## 阶段 6｜地图结果可视化

### 目标

在地图上展示现有工具输出的中心点、15 分钟等时圈、三类 POI 和服务盲区。

这是比赛展示最关键的页面之一。

---

### 工作内容

#### A. Center Marker

中心点。

---

#### B. 15分钟 Isochrone

读取现有：

```text
GeoJSON Polygon
```

渲染不规则真实步行范围。

不是画：

```text
圆形 15min buffer
```

---

#### C. POI

至少显示：

```text
market
pharmacy
primary_school
```

建议拥有：

```text
图例
显示/隐藏
```

三类设施视觉要容易区分。

---

#### D. Blindspot

显示：

```text
灰色区域 / 半透明区域
```

需要区分：

```text
market blindspot
pharmacy blindspot
primary_school blindspot
```

具体视觉方式由 UI 决定，但不能让三类完全无法分辨。

---

#### E. 可选 Debug 图层

开发阶段可提供：

```text
Isochrone sample points
Boundary points
```

正式展示可以默认关闭。

这对于：

- 调试；
- 答辩；
- 解释算法；

很有价值。

### 验收标准

地图能显示 Center Marker、现有 GeoJSON 等时圈、`market` / `pharmacy` / `primary_school` 三类 POI，以及可区分类型的 Blindspot；可选 Debug 图层按原计划处理。

---

## 阶段 7｜体检报告与图表

### 目标

将现有 `DiagnosisResult` 直接呈现为用户可读的体检报告和少量图表。

Web 右侧或者下方展示：

```text
DiagnosisResult
```

不要让前端自己重新计算。

---

### 工作内容

#### 设施数量

```text
菜市场
药店
小学
```

---

#### 覆盖 / 盲区指标

直接读取：

```text
Blindspot / Diagnosis
```

现有指标。

不要前端重新算一次。

---

#### Confidence / Quality

如果后端结果有：

```text
high / medium / low
```

需要用户看得见。

---

#### Warnings

例如：

```text
部分 POI 数据不完整
部分 Routing 请求失败
等时圈部分方向置信度较低
```

不能只写进 Console。

---

#### 图表

MVP建议只做：

```text
1 个柱状图
+
1 个补充图表
```

比如柱状图：

```text
菜市场数量
药店数量
小学数量
```

如果 Diagnosis 已经存在可用于雷达图的确定性指标，再做雷达图。

如果目前没有：

> **不要为了漂亮临时让前端创造一个没有算法依据的“综合评分”。**

### 验收标准

报告可见三类设施数量、后端已有覆盖/盲区指标、Confidence / Quality 与 Warnings；图表只使用现有确定性数据，不新增无依据的综合评分。

---

## 阶段 8｜产品状态与异常 UX

### 目标

让用户在选择地点、分析中、获得结果和失败时，都能看到正确的页面状态与数据质量提示。

这一块非常容易被忽视，但能明显提高完整度。

---

### 工作内容

#### Empty

还没选择地点：

```text
请选择分析中心点
```

---

#### Ready

已选地点：

```text
开始体检
```

---

#### Loading

计算中：

```text
正在分析15分钟生活圈……
```

MVP 当前不需要 SSE。

可以普通 Loading。

---

#### Result / Error

成功：

显示地图和报告。

失败：

显示：

```text
数据获取失败
```

而不是：

```text
这里没有设施
```

---

#### Warning 与 Error 的区别必须延续

后端：

```text
ok = true + warnings
```

前端：

> 显示结果，同时告诉用户数据质量问题。

后端：

```text
ok = false
```

前端：

> 显示错误，不展示误导性结论。

### 验收标准

页面覆盖 Empty、Ready、Loading、Result / Error；`ok = true` 且有 warnings 时显示结果与提示，`ok = false` 时显示错误且不呈现误导性结论。

---

## 阶段 9｜真实社区案例验证

### 目标

用一个固定的上海真实社区或街道案例，验证并记录系统结果的合理性。

### 工作内容

这是比赛交付中非常重要的一部分。

---

#### 正式选一个真实社区 / 街道

要求：

```text
上海真实社区/街道
```

最终必须固定一个具体案例。

开发阶段可以继续用同济附近测试，但正式报告需要：

```text
明确案例名称
明确中心点
明确日期
明确 API 数据来源
```

---

#### 保存一次完整结果

建议：

```text
data/samples/final-case/
```

保存：

```text
input.json
diagnosis-result.json
```

如需要：

```text
poi.json
isochrone.geojson
blindspots.geojson
```

但应来自真实工具输出，不要手工伪造。

---

#### 做真实合理性检查

人工检查：

#### POI

抽查一部分设施：

```text
地图上现实存在吗？
分类合理吗？
```

---

#### Isochrone

选择若干边界方向：

```text
后端预测 ≈ 900 秒
```

再用百度单点 Walking Route 独立测一次。

记录：

```text
估计时间
真实路线时间
误差
```

---

#### Blindspot

抽查：

```text
几个被判定为盲区的点
```

确认 1km 设施搜索结果是否与系统一致。

---

#### 最终写：

```text
docs/test-report/
```

包括：

```text
案例
方法
数据
结果
误差
异常
局限性
结论
```

### 验收标准

固定案例名称、中心点、日期和 API 数据来源；保存真实工具输出；完成人工抽查及 `docs/test-report/` 中包含案例、方法、数据、结果、误差、异常、局限性和结论的测试报告。

---

## 阶段 10｜工程交付

### 目标

补齐让评委能够安装、运行、测试和部署项目的工程交付材料。

### 工作内容

赛题不是只看页面。

工程部分必须正式补齐。

---

#### A. `.env.example`

只写：

```text
BAIDU_MAP_AK=
```

以及真实项目需要的其他变量名。

不要写真实值。

---

#### B. README

最终 README 至少包括：

```text
项目介绍
赛题背景
功能截图
架构图
六个 Tool
算法流程
环境要求
安装步骤
百度 AK 配置
后端启动
前端启动
Docker 启动
测试方式
真实案例
项目目录
开源许可证
```

---

#### C. Scripts

建议至少提供：

```text
scripts/
```

里的：

```text
validate_contracts
test
run / verify live
```

现有脚本优先复用。

---

#### D. Docker

目标：

评委：

```text
clone
↓
配置 .env
↓
docker compose up
```

就可以运行。

不要要求评委手动配置十几个步骤。

---

#### E. CI

使用：

```text
.github/workflows/
```

至少自动运行：

```text
Contract validation
pytest
```

如果有正式 Web：

再加入：

```text
npm install
npm build
```

或者实际项目对应命令。

---

#### CI 不使用真实百度 AK

CI：

```text
Mock only
```

真实百度验证通过：

```text
单独 Live Script
```

完成。

### 验收标准

`.env.example` 不含真实密钥；README 覆盖本阶段所列内容；必要脚本、Docker 启动方式和 CI 可用；CI 使用 Mock 运行 Contract、测试与前端构建，真实百度验证由单独 Live Script 完成。

---

## 阶段 11｜最终 QA 与比赛交付检查

### 目标

完成最终功能、工程、安全和新电脑复现检查，只修问题，不再增加功能。

### 工作内容

最后阶段不再加功能。

只修问题。

---

#### 功能检查

必须完整演示：

```text
选地点
↓
开始体检
↓
真实百度数据
↓
15分钟等时圈
↓
三类 POI
↓
服务盲区
↓
体检报告
```

---

#### 工程检查

运行：

```text
contract validation
pytest
frontend build
docker compose
```

全部通过。

---

#### AK 安全检查

在 GitHub 搜索：

```text
ak=
BAIDU
真实 AK 内容
```

确保：

```text
没有真实密钥。
```

---

#### 新电脑测试

非常推荐：

> 找一台没有你们开发环境的电脑。

按照 README：

```text
clone
↓
配置 AK
↓
运行
```

如果能成功：

说明评委大概率也能成功。

### 验收标准

完整演示选点到体检报告的链路；Contract validation、pytest、前端构建与 Docker Compose 通过；GitHub 无真实 AK；按 README 在全新环境完成启动验证。

---

## 四、最终 MVP 仓库大致应该长这样

```text
15min-life-circle-assistant/

├── app/
│   ├── modules/
│   │   ├── location/
│   │   ├── poi/
│   │   ├── routing/
│   │   ├── isochrone/
│   │   ├── blindspot/
│   │   └── diagnosis/
│   │
│   ├── providers/
│   │   └── baidu/
│   │
│   ├── schemas/
│   │
│   ├── api/
│   │   ├── routes/
│   │   └── main.py
│   │
│   └── core/
│
├── web/
│   └── src/
│       ├── map/
│       ├── report/
│       ├── api/
│       ├── components/
│       └── pages/
│
├── contracts/
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
├── docs/
│   ├── architecture/
│   ├── gis/
│   ├── api/
│   └── test-report/
│
├── scripts/
├── .github/workflows/
│
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── README.md
├── LICENSE
└── requirements.txt
```

不要为了让目录“看起来完整”创建完全没用的空模块。

---

## 五、每个阶段都采用同一个 Git 工作流

后续不要直接在 `main` 上写。

例如：

```text
feat/api-layer
```

完成：

```text
开发
↓
自测
↓
Pull Request
↓
另一个人 Review
↓
Merge
↓
两个人 Pull main
```

然后：

```text
test/e2e-diagnosis
```

再：

```text
feat/web-shell
```

再：

```text
feat/map-result-layers
```

再：

```text
feat/report-ui
```

工程部分：

```text
chore/docker-ci-docs
```

真实案例：

```text
docs/final-case-study
```

---

## 六、现在不属于 MVP 的内容

下面这些很重要，但**全部放在 Core Competition MVP 完成以后**：

```text
AI自然语言解释
Single Agent
LifeCircleBrief
Reviewer Agent
Planner Agent
LangGraph
MCP
SSE
Checkpoint
Worker
Redis
多社区比较
智能设施选址
微信小程序
```

原因不是它们没有价值。

而是：

> **现在 Tool 已经做好，先把真实产品交付闭环跑通。**

之后 Agent 才有稳定工具可调用。

---

## 七、MVP 最终验收标准

只有以下全部满足，才宣布 MVP 完成：

| 类别 | 必须达到 |
|---|---|
| Core | Tool 1–6 全部运行正常 |
| Contract | Contract validation 通过 |
| Test | Unit + Integration + E2E 通过 |
| Data | 使用真实百度地图数据 |
| Location | 用户可搜索/选择中心点 |
| POI | 显示菜市场、药店、小学 |
| Routing | 使用真实步行数据 |
| Isochrone | 显示真实路网推导的15分钟等时圈 |
| Blindspot | 显示服务盲区 |
| Diagnosis | 一次完整社区体检 |
| Web | 用户无需碰代码即可操作 |
| UX | Loading / Error / Warning 都有 |
| Quality | Confidence / 数据质量可见 |
| Case | 至少一个真实社区完整测试 |
| Docs | 有真实测试报告 |
| Security | AK 不进入 Git |
| README | 新用户可以按说明运行 |
| Docker | 可快速部署 |
| CI | 自动检查 Contract / Test / Build |
| Open Source | LICENSE 存在 |

---

## 八、交给下一位 GPT 的执行指令

你可以把下面这段连同本计划一起给下一位 GPT：

> **当前项目的 Tool 1–6：Location、POI、Routing、Isochrone、Blindspot、Diagnosis 已经全部完成。不要重新设计或重写这六个 Tool，也不要现在增加 Agent/MCP。请按照《MVP 总工作计划 v1.0》继续。先检查当前合并仓库和全部测试，然后从“阶段 0：冻结 Tool 1–6 基线”开始；确认通过后进入“阶段 1：六 Tool 总集成验证”，再完成 FastAPI API、E2E、正式 Web、地图可视化、体检报告、真实社区案例、Docker、README、CI 和最终 QA。每一个阶段完成并通过验收后再进入下一阶段。所有 API 和前端必须复用现有 Contract / Schema / DiagnosisResult，不允许为了前端重新发明第二套数据结构。真实百度数据必须通过现有 Provider / Tool 获取，Mock 只用于自动化测试。服务端百度 AK 永远不得进入前端或 GitHub。**
