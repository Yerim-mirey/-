# Tool 5 Blindspot + Tool 6 Diagnosis 开发交接文档 v1.0

**项目：**15 分钟生活圈智能体检与规划助手  
**交接对象：**另一位 ChatGPT Work 与项目队友  
**基线：**用户附件 `core-mvp-tools-v1.zip`，SHA-256 `c9028fe8ca6e2ac1782327a4864212e377ae4923b3af9014a2c12d7ba47572f7`  
**阶段：**仅设计和交接；本文件不代表 Tool 5/6 已编码或已通过真实百度验证。  
**日期：**2026-09-19

## 0. 接手者先做什么

1. 以这份 ZIP 的**内容**为 Tool 1–4 唯一基线；正式仓库可能已有同名文件，先逐文件比较，绝不整包覆盖。ZIP 自身没有 `.git`，分支应从正式仓库的共同基线提交创建。
2. 先共同 Review 下文的 Blindspot Contract，确定后提交 Contract 示例与 Schema，再写 Mock、计算、测试和真实数据验证。Tool 5 合并后，用同样顺序做 Tool 6。不要先写 Diagnosis 后倒推接口。
3. 项目内 `AGENTS.md` 要求审阅或改动项目代码前使用 `ponytail` 技能。保留一个 `app/schemas/common.py`，复用其中公共类型；不得提交真实百度 AK、`.env`、含 AK 的 URL 或原始响应。
4. 给初学者执行时，每一步说明目的和预期输出。已有接口若确需变化，先提交兼容性说明和测试，不要悄悄修改 Tool 1–4。

## 1. ZIP 实际审阅记录

### 1.1 真实目录与接口

```text
core-mvp-tools-v1/
├── app/
│   ├── schemas/{common,location,poi,routing,isochrone}.py
│   ├── modules/
│   │   ├── location/{service.py,README.md,tests/}
│   │   ├── poi/{service.py,category_mapping.py,README.md,tests/}
│   │   ├── routing/{service.py,batching.py,README.md,tests/}
│   │   └── isochrone/{service.py,sampling.py,refinement.py,interpolation.py,README.md,tests/}
│   └── providers/baidu/{geocoding.py,poi.py,routing.py}
├── contracts/v1/{center-point,location-*,poi-search-*,routing-*,isochrone-*}.example.json
├── scripts/{validate_contracts.py,verify_poi_live.py,verify_routing_live.py,verify_isochrone_live.py}
├── tests/{test_*contracts.py,test_*integration.py,test_baidu_*provider.py,integration/test_*_baidu_live.py}
├── data/samples/isochrone-live.json
├── requirements.txt
└── README.md
```

ZIP **没有** `app/api/`、`app/modules/blindspot/`、`app/modules/diagnosis/` 或 GeoJSON 空间运算依赖。不要把旧对话里的规划目录误当成现有文件。

| Tool | 已存在的入口和输入 | 已存在的输出与真实边界 |
|---|---|---|
| Location | `resolve_location(LocationRequest, geocode=None)`；地址或 BD09LL 坐标 | `LocationResult`；坐标直通，地址需注入 `BaiduGeocodingProvider.geocode`。当前**没有逆地理编码实现**。 |
| POI | `search_pois(POISearchRequest, provider=None)`；中心、设施类型、检索半径 | `POISearchResult`；`POI{poi_id,name,facility_type,location,address,source}`，`counts` 的每类值为整数或 `null`。 |
| Routing | `RoutingService(provider).route(RoutingRequest)`；一个起点和 `RouteTarget{target_id,location}[]` | `RoutingResult`；每条路线保留 `target_id`、`status`、`distance_m`、`duration_s`，并给出汇总。 |
| Isochrone | `IsochroneService(route_callback).generate(IsochroneRequest)` | `IsochroneResult`；`data.geometry` 是近似 GeoJSON Polygon，另有边界点与质量指标。只调用 Routing 公共接口，不直接调用百度。 |

**共同 Contract：**`schema_version="1.0"`；`CenterPoint={lng: float, lat: float, crs:"BD09LL"}`；成功 `{ok:true,data,warnings,meta}` 与失败 `{ok:false,error,warnings,meta}` 互斥；`WarningItem={code,message}`、`ErrorDetail={code,message,retryable}`、`Meta={schema_version,provider}`；Pydantic v2 严格校验并禁止未声明字段。新 Tool 继续用同一套 Envelope 和公共类，不能新建第二个坐标类型。

**POI 的关键语义：**支持 `market`、`pharmacy`、`primary_school`。每类由多个关键词搜索并按百度 UID（无 UID 时按稳定指纹）去重。某类任一关键词失败或结果分页达到上限，该类 `counts` 为 `null` 且出现 `PARTIAL_POI_RESULTS`；未请求的类别也为 `null`。只有完整搜索得到 `0`，才表示本次搜索在该范围内没有匹配 POI。即使 `pois=[]`，只要该类计数是 `null`，也**不能判定盲区**。POI Service 在所有请求关键词都失败时返回失败 Envelope。

**Routing 的关键语义：**目前只支持 `walking`。Service 自动按最多 50 个目标分批，每批可重试一次；批次失败对应目标 `unavailable`，部分失败给 `PARTIAL_ROUTING_RESULTS`，全失败返回失败 Envelope。百度返回的不同点 `0/0` 被归为 `no_route`，起终点相同的 `0/0` 可为 `success`。成功路线的米数和秒数均为非空，`no_route` / `unavailable` 均为空。`target_id` 必须在同一请求内唯一；用它连接 POI，切勿依赖返回位置或数组下标。

**Isochrone 的关键语义：**默认 24 个方向、六圈初始样本、有限外扩、两轮细化。`quality` 有 `requested_directions`、`valid_boundary_directions`、`routing_sample_count`、`routing_success_count`、`sample_success_rate` 和 `high|medium|low`。少于三个有效方向直接失败；部分缺口可返回成功但有 `BOUNDARY_GAPS`、`BOUNDARY_NOT_BRACKETED`、`PARTIAL_ROUTING_RESULTS`、`LOW_CONFIDENCE` 等警告。它是**基于路由采样的近似边界**，并非每个边界内点都已证明 15 分钟可达。

**百度 Provider：**Location 用 Geocoding v3；POI 用 Place v2 圆形检索并逐页读取；Routing 用 Walking RouteMatrix v2。对外坐标是 `{lng,lat}`，Provider 内才组装百度请求所需的 `lat,lng` 字符串。现有 POI Provider 每关键词最多读取 150 条，触顶会报不完整，不能把截断数据当完整设施清单。新 Tool 不得解析百度原始 JSON，也不应添加新的百度 Provider。

### 1.2 测试和脚本核验

- `scripts/validate_contracts.py` 已在本次审阅中运行成功，覆盖 Tool 1–4 示例及部分拒绝用例。
- ZIP 的 README **记载**离线 `120 passed`、在线集成 `3 passed`；本次环境缺少 `pytest` 且无法从 PyPI 安装，因此没有重新证明该数字。正式仓库合并时必须重新运行完整测试并保存输出。
- `tests/integration/` 有 Location、Routing、Isochrone 的按需真实百度 smoke test，只有 `RUN_BAIDU_SMOKE=1` 且设置 `BAIDU_MAP_AK` 才执行；POI 的真实验证入口是 `scripts/verify_poi_live.py`。
- `verify_routing_live.py`、`verify_isochrone_live.py` 只读环境变量中的 AK；`verify_poi_live.py` 可读环境变量或根目录被忽略的 `.env`。`data/samples/isochrone-live.json` 内已有一份成功样例：24/24 方向、192/192 路由样本、`high`；这是**已有样本文件**，不能代替本阶段现场复验。
- `requirements.txt` 目前只有 Pydantic 和 pytest。ZIP 不包含 `.env` 或 AK。

## 2. 两个新 Tool 的边界

```text
Location ── CenterPoint ───────────────┬─> POI ──────────────┐
                                      ├─> Routing(中心→POI) ┤
                                      └─> Isochrone ─────────┤
                                           ↑ Routing(采样)    │
POI + Isochrone + Routing(网格→POI) ─────> Blindspot ────────┤
                                                            ↓
                                         DiagnosisResult（统一汇总）
```

- **Blindspot** 是确定性的空间分析 Service。它接收已经标准化的 POI 和 Isochrone 结果，并通过注入的**现有 Routing Service**按需查询网格点到设施的步行距离；不直接请求百度。
- **Diagnosis** 是一次体检的业务编排 Service。它先解析位置，再并行条件允许地取得 POI、中心到 POI 的 Routing、Isochrone，然后构造 Blindspot 输入，最后汇总结果、状态和指标。可顺序实现，先保证正确性。
- 15 分钟边界与“1 公里服务盲区”是**两个不同阈值**：前者用步行时间 `duration_s <= 900`，后者用步行路程 `distance_m <= 1000`。一个设施可以位于等时圈外，却服务于圈内边缘网格；检索范围必须为它留出缓冲。

## 3. Tool 5：Blindspot Contract 草案

### 3.1 定义、输入与调用方式

**定义：**在 15 分钟等时圈的分析域内，对每个设施类别，将网格代表点到该类别所有候选 POI 的**步行路程**与 1000 米比较。能确认至少一条路线 `distance_m <= 1000` 的网格为 `covered`；能确认所有可能候选都无法在 1000 米内服务、且 POI 检索完整的网格为 `blind`；证据缺失的网格为 `unknown`。所谓“1km 内没有菜市场/药店/小学”必须按**每一类别分别判定**。1000 米是路径长度，不是直线半径；先用直线距离排除不可能在 1000 米步行内的设施。

新增 `app/schemas/blindspot.py`。建议 JSON 请求模型：

| 字段 | 类型、规则 |
|---|---|
| `schema_version` | 只能为 `"1.0"`。 |
| `poi_request` | 现有 `POISearchRequest` 原样嵌入，保留请求中心、类别、搜索半径。 |
| `poi_result` | 现有 `POISearchResult` Envelope 原样嵌入；允许成功、部分成功和失败。 |
| `isochrone_result` | 现有 `IsochroneResult` Envelope 原样嵌入；失败时 Blindspot 返回失败 Envelope。 |
| `service_distance_m` | 正整数，MVP 固定调用值 `1000`；Schema 可只允许 1000，避免对外宣称未验证的任意半径。 |
| `grid_size_m` | 正整数，MVP 固定 `200`；分辨率写入输出，未来改动需比较结果。 |
| `max_route_pairs` | 正整数，MVP 默认 `5000`，限制一次分析的路由目标对数；耗尽的判定留为 `unknown`。 |

Python Service 入口建议为 `analyze_blindspots(request: BlindspotRequest, route: Callable[[RoutingRequest], RoutingResult]) -> BlindspotResult`。`route` 是**内部注入的函数**，不序列化进 JSON；真实运行传 `RoutingService(...).route`，Mock 测试传假函数。`POISearchResult`、`IsochroneResult` 是 Pydantic `RootModel`；序列化到 JSON 时直接嵌入 `{ok,...}`，不能多包一层 `root`。

在业务层验证：`poi_request.center == isochrone_result.data.center`；POI 成功时 `poi_result.data.center` 也须相同；所有 POI 坐标须为 BD09LL；只分析 `poi_request.facility_types`。中心不一致返回 `INVALID_REQUEST`，不得自动做坐标转换。若 Isochrone 失败，返回 `ISOCHRONE_UNAVAILABLE`，保留它的相关 warnings；若几何环无效或空间运算失败，返回 `INVALID_GEOMETRY` 或 `COMPUTATION_ERROR`。POI 失败并非几何计算失败，应该输出 `unknown`，不能输出一张假盲区图。

### 3.2 搜索范围与设施完整性

在调用 POI 之前，由 Diagnosis 从 Isochrone Polygon 所有顶点求相对中心的最大**局部平面距离** `R`。要求 `poi_request.search_radius_m >= ceil(R + 1000 + 数值余量)`；建议余量 100 米，作为局部投影与边界近似的保护带。理由：任何网格代表点都在分析域内，而距它步行不超过 1 公里的设施，其直线距离也不会超过 1 公里；相对原中心的距离最多为 `R+1000`。不要简单固定搜 1500 米，因为等时圈可能伸得更远。

Blindspot 自身重新计算并核验这个下限。若请求半径不足，仍可确认已有 POI 所覆盖的网格；其余网格为 `unknown`，发 `POI_RADIUS_INSUFFICIENT`，不能标 `blind`。若百度对实际检索半径有额外限制、关键词不全、触发 150 条上限、某关键词报错或请求类别未查，则对应类别视为不完整。现有 `counts[type] == null` 是必看的不完整信号；`counts[type] == 0` 仅在类别已完整请求且范围足够时才可参与盲区判断。百度数据对设施真实性和营业状态仍有外部局限，不应把高置信度解释为现实世界的绝对完整。

### 3.3 网格计算的确定性规则

1. 用中心附近的**局部米制坐标**建立固定原点、东西向/南北向轴和 200m 正方形网格。仅在这个局部范围做距离、面积和裁剪；不要直接用经纬度的“度”算米或面积。将输出顶点转回 `[lng,lat]` BD09LL。
2. 对等时圈 Polygon 做拓扑校验。现有 Schema 只检查闭合和坐标范围，**没有证明不自交**；无效几何返回失败，禁止用自动修补后悄悄继续。
3. 生成与等时圈相交的网格，将每格裁剪到域内；相交结果为空则跳过。取**裁剪后形状内部的代表点**作为该格的 Routing 起点。每个类别给该格一个状态。一个格即使只有部分落在圈内，面积只计入裁剪部分。
4. 对每类 POI，先用代表点到 POI 的局部直线距离筛选 `<=1000m` 候选；直线距离大于 1000m 的 POI 不可能满足 1000m 步行路程。边界附近保留微小数值容差用于候选筛选；最终状态仍以 Routing 的整数米数为准。
5. 每个网格起点调用现有 Routing 的“一个起点→一批目标”接口。`RouteTarget.target_id` 用 `poi_id`；同一 POI 若跨类别重复，只路由一次再按 `poi_id` 回连。Service 自行按 50 个目标拆批。绝不拿 POI 名称或数组位置回连。
6. 对一类设施，只要有一条 `success` 且 `distance_m <=1000`，该格就是 `covered`，即使同格其他候选失败。若不存在覆盖路线，则只有在**该类 POI 搜索完整、检索半径足够、所有候选均已评估**，且每条已评估路由为 `success` 但 `distance_m>1000` 或明确 `no_route` 时，才判 `blind`。任一候选 `unavailable`、整次 Routing 失败、路由预算耗尽或搜索不完整，均判 `unknown`。无任何直线 1km 候选且 POI 搜索完整、范围足够时，可直接判 `blind`，无需 Routing。
7. 按行列固定顺序处理网格和类别。达到 `max_route_pairs` 后停止派发新的路由；已确认覆盖仍为 `covered`，其余有未测候选的格为 `unknown`。记录预算耗尽警告。不能只测最近几个 POI 后把其余未测的格判为 `blind`。

**空间运算实现：**建议在 Blindspot 的 Service 阶段加入一个经过团队验证的 `Shapely` 依赖，用于 Polygon 有效性、网格相交裁剪、内部代表点和面积。当前 ZIP 尚未包含它。不要自行写一套复杂多边形裁剪；也不要为了省依赖而输出越出等时圈的完整方格。局部投影可用短距离等距近似并在 README 中注明适用范围和误差；本 MVP 限于一个社区尺度，不支持跨经度 180°或极区数据。投影与裁剪只放在新模块，不改 Tool 1–4 的坐标契约。

### 3.4 输出形状、面积与置信度

新增 `BlindspotResult`，沿用共同成功/失败 Envelope。成功的 `data` 建议固定为：

| 字段 | 含义 |
|---|---|
| `center` | 复用 `CenterPoint`。 |
| `analysis_geometry` | 与输入 Isochrone 一致的 Polygon；供地图显示分析域。 |
| `service_distance_m`, `grid_size_m` | 明确计算口径。 |
| `blind_spots` | GeoJSON 形状的 `FeatureCollection`；每个 Feature 是裁剪后的盲区网格，`properties={cell_id,facility_type,classification:"blind"}`。 |
| `unknown_areas` | 相同结构，`classification:"unknown"`；前端必须用不同颜色或图案显示，不能和盲区混画。 |
| `coverage` | 三类各一条：`facility_type`、`total_area_m2`、`covered_area_m2`、`blind_area_m2`、`unknown_area_m2`、`covered_ratio`、`blind_ratio`、`unknown_ratio`、`covered_cells`、`blind_cells`、`unknown_cells`、`confidence`。 |
| `quality` | `grid_cell_count`、`route_pairs_requested`、`route_pairs_succeeded`、`route_pairs_unavailable`、`route_budget_exhausted`、`method:"grid_representative_walking_distance"`、整体 `confidence`。 |

每个类别单独把所有裁剪单元面积作为分母；三种面积之和应在舍入容差内等于 `total_area_m2`，三种比例之和等于 1。比例按**面积**计算，是网格代表点法的估计，不是人口覆盖率，也不是精确到每户的服务范围。空盲区应返回合法的 `FeatureCollection` 且 `features:[]`。要能在 GeoJSON 中表达裁剪后出现的 Polygon / MultiPolygon 与孔洞，不允许无效环、自交、NaN 或经纬度顺序颠倒。现有 Isochrone 几何使用 BD09LL `[lng,lat]`；新输出保持这个项目内部约定，并在文档/前端注明其坐标系，外部 GIS 交换前需显式转换。

置信度采用保守规则：任一类别 POI 搜索不完整或检索范围不足，该类为 `low`；该类完整、无 unknown 且 Isochrone 为 `high`，可为 `high`；Isochrone 为 `medium` 或有少量未知时最多 `medium`；Isochrone 为 `low` 时整体不高于 `low`。**建议 Contract 固定更精确的阈值**：`unknown_ratio==0` 为完整；`0<unknown_ratio<=0.1` 且其他来源完整为 `medium`；其余为 `low`。整体 confidence 取各类与 Isochrone 的最低等级。注意这是数据和算法质量等级，不是统计概率。

结果 warnings 至少规定：`PARTIAL_POI_RESULTS`（上游透传）、`POI_RADIUS_INSUFFICIENT`、`PARTIAL_ROUTING_RESULTS`（上游透传）、`ROUTING_BUDGET_EXCEEDED`、`UNKNOWN_BLINDSPOT_AREA`、`LOW_CONFIDENCE`。去重时按 `(code,message)` 保留，不能删去上游有价值的警告。`meta={schema_version:"1.0",provider:"baidu"}` 表示事实数据来源；Mock 示例可用 `provider:null`，不得在 Meta 中加入密钥或原始请求。

**示例 Feature（结构示意，坐标须由真实/Mock 空间计算产生）：**

```json
{
  "type": "Feature",
  "geometry": {
    "type": "Polygon",
    "coordinates": [[[121.5000, 31.2800], [121.5005, 31.2800], [121.5005, 31.2805], [121.5000, 31.2800]]]
  },
  "properties": {
    "cell_id": "grid:r0003:c0007",
    "facility_type": "market",
    "classification": "blind"
  }
}
```

正式 fixture 的环必须包含真实裁剪得到的所有顶点，几何拓扑和面积都要测试；上例只示范字段，不代表实际 200 米方格。

### 3.5 必须落库的 Contract 文件与状态

在 `contracts/v1/` 新增 `blindspot-request.example.json`、`blindspot-result.example.json`、`blindspot-empty.example.json`（完整检索且无盲区）、`blindspot-partial.example.json`（POI/路由缺失产生 unknown）、`blindspot-failure.example.json`（等时圈失败或无效）。所有文件都要能由新 Schema `model_validate`，并加到 `scripts/validate_contracts.py`。请求示例嵌入**现有** POI 和 Isochrone Envelope，不发明新 POI 字段；示例中的 `poi_request.search_radius_m` 必须覆盖等时圈加 1 公里。`partial` 必须能证明 `counts=null` 不变成盲区。

## 4. Tool 6：Diagnosis Contract 草案

### 4.1 请求和编排顺序

新增 `app/schemas/diagnosis.py`，公共 JSON 请求 `DiagnosisRequest`：

| 字段 | 规则 |
|---|---|
| `schema_version` | `"1.0"`。 |
| `location` | **复用**现有 `LocationInput`：地址 `{type:"address",query,city?}` 或坐标 `{type:"coordinate",lng,lat,crs:"BD09LL"}`。 |
| `facility_types` | 默认/示例为三类 `market,pharmacy,primary_school`；至少一类，去重后稳定顺序。 |
| `time_limit_s` | MVP 固定 `900`。 |
| `service_distance_m` | MVP 固定 `1000`。 |
| `grid_size_m` | MVP 固定 `200`。 |

**最小 Diagnosis 请求示例：**

```json
{
  "schema_version": "1.0",
  "location": {"type": "address", "query": "同济大学四平路校区", "city": "上海市"},
  "facility_types": ["market", "pharmacy", "primary_school"],
  "time_limit_s": 900,
  "service_distance_m": 1000,
  "grid_size_m": 200
}
```

建议入口 `diagnose(request: DiagnosisRequest, *, resolve_location, search_pois, route, generate_isochrone, analyze_blindspots) -> DiagnosisResult`；这些均为现有或新增 Service 的可注入调用，不把 API Key 放进 Contract。可先以一个薄的调用对象集中注入，避免五个松散参数；不要为单一实现建复杂插件架构。

执行顺序：

1. 调 Location，取得唯一 `CenterPoint`。失败直接返回 Diagnosis 失败，错误码 `LOCATION_FAILED`，保留原始失败代码于**内部日志**或安全的阶段状态中，不把百度原始错误泄漏到公共结果。
2. 用中心调用 Isochrone（`time_limit_s=900`），其内部复用 Routing。若无可用 Polygon，Diagnosis 返回 `ISOCHRONE_FAILED`，因为空间分析域不存在。
3. 根据真实 Polygon 求 `R+1000+100m`，创建 POI 请求，搜索三类设施。绝不能先随意固定半径，再因搜索范围不足给出盲区结论。
4. 若 POI 成功，按 `POI.poi_id -> RouteTarget.target_id` 从**中心**路由到所有去重 POI，形成实际 15 分钟可达指标；若没有 POI，不发空 RoutingRequest。Routing 失败时保留 POI 与 Isochrone，相关指标设为未知。
5. 将原 `POISearchRequest/Result`、`IsochroneResult` 交给 Blindspot，并注入同一个 Routing Service 用于网格点到候选 POI 的步行距离。
6. 统一组装 `DiagnosisResult`，聚合警告和阶段状态，验证数据互相一致。异常不应无提示吞掉；上游失败不得被解释成“零设施”或“零盲区”。

### 4.2 统一 DiagnosisResult

继续使用 `{ok,data,warnings,meta}` / `{ok,error,warnings,meta}`。成功 `data` 建议固定为下表；未来 Web、Agent、Reviewer 均读取此结果，而非重新组合各工具私有响应。

| 字段 | 类型与用途 |
|---|---|
| `center`、`location_label` | `CenterPoint`、可空文字；地图定位和报告标题。 |
| `poi_search_radius_m` | 此次 POI 查询半径；解释所有设施数量的空间范围。 |
| `poi` | `POISearchData | null`；失败时 `null`，不能造空列表代替。 |
| `facility_routes` | `RoutingData | null`；中心到 POI 的批量步行结果，失败或无可路由目标时为 `null`。 |
| `isochrone` | `IsochroneData`；成功 Diagnosis 必填。 |
| `blindspot` | `BlindspotData | null`；若失败为 `null`，不得伪装成空盲区。 |
| `metrics` | 每设施类别的 `poi_count: int|null`、`confirmed_reachable_15m_count: int`、`unresolved_route_count: int`、`nearest_walk_distance_m: int|null`、`nearest_walk_duration_s: int|null`、`covered_ratio: float|null`、`blind_ratio: float|null`、`unknown_ratio: float|null`。 |
| `stages` | `location/poi/routing/isochrone/blindspot` 各为 `complete|partial|unavailable|skipped`，便于 UI 和 Agent 解释缺项。 |
| `quality` | 整体 `high|medium|low`，并明确 `method:"sampled_isochrone_and_grid_walking_distance"`。 |

`confirmed_reachable_15m_count` 仅数**本次检索到的 POI**中 `Routing` 的 `success && duration_s<=900`，是已确认数量下界，不宣称全域真实设施总数；未路由/不可用数量单列 `unresolved_route_count`。`nearest_walk_*` 只从成功路线求最小值；无成功路线可为空，其含义由阶段状态和完整性解释。`poi_count` 直接沿用 POI 的整数或 `null`，其范围由 `poi_search_radius_m` 限定；覆盖比例直接沿用 Blindspot，不自行重算。设施 POI 的坐标是否落在近似 Polygon 内**不能代替**中心到 POI 的真实步行时间。结果不包含虚构的评分、人口覆盖、学校学区或设施营业状态。

若 Location 或 Isochrone 失败：Diagnosis 失败 Envelope，`error.code` 为 `LOCATION_FAILED` 或 `ISOCHRONE_FAILED`，`retryable` 继承或由具体上游失败推导。POI 失败、Routing 失败或 Blindspot 因 POI/路由不全产生 unknown：可返回成功 Envelope，但相应 `stages` 为 `partial/unavailable`、指标为 `null`、`quality=low` 并带警告。Blindspot 自身计算失败时 `blindspot=null`，返回部分 Diagnosis，警告 `BLINDSPOT_UNAVAILABLE`。不要让 `ok:true` 被误读成所有阶段成功；判断完整性必须看 `stages` 和 `quality`。

错误码建议：`LOCATION_FAILED`、`ISOCHRONE_FAILED`、`INVALID_REQUEST`、`COMPUTATION_ERROR`。警告聚合保留 Tool 1–5 的既有代码/文字，并新增 `POI_UNAVAILABLE`、`FACILITY_ROUTING_UNAVAILABLE`、`BLINDSPOT_UNAVAILABLE`、`PARTIAL_DIAGNOSIS`。公共 warnings 仍用原 `WarningItem`，不能擅自增加 `source` 字段；阶段归属由 `stages` 与文字说明表达。`meta.provider` 对真实百度链路为 `"baidu"`，Mock 可为 `null`。

新增 `diagnosis-request.example.json`、`diagnosis-result.example.json`、`diagnosis-partial.example.json`、`diagnosis-failure.example.json`。完整样例至少体现一类覆盖、一类盲区和一类零 POI；部分样例至少体现 `counts=null` 与 unknown 区；失败样例体现无位置或无等时圈。四个都应通过 Schema 与验证脚本。

## 5. Mock、Schema、Service、测试与真实数据的执行顺序

### 阶段 A：先冻结 Blindspot Contract

1. 双人 Review 第 3 节，确定固定参数、空结果、partial 与 failure 的 JSON 字段和面积/置信度口径。把例子写到 `contracts/v1/`，不改已有 Tool 1–4 示例。
2. 写 `app/schemas/blindspot.py`，复用 `common.py`、`poi.py`、`isochrone.py`、`routing.py` 的公共类型。对 GeoJSON、非负面积、比例范围、分类枚举、面积守恒（容差）、类别唯一和成功/失败互斥做校验。脚本加入全部新示例及至少一个拒绝“失败混入 data / 未知字段 / 无效环”的用例。
3. 用确定的 Mock：完整 POI、有 POI 但步行路程超过 1km、完整空 POI、`counts=null`、Routing `unavailable`、Isochrone 部分方向、预算耗尽。Mock 中的坐标和距离要一致；不要使用随机值或真实 AK。
4. 写 `app/modules/blindspot/{service.py,README.md}`；空间逻辑可拆成 `grid.py`、`coverage.py`，仅在文件确实有独立职责时拆。加入经过验证的空间依赖并锁定安装方式。
5. 测试：网格裁剪严格位于 Polygon 内；边缘格面积守恒；跨类别独立；1km 恰好等于阈值算 covered；直线远于 1km 不请求路线；近但步行远判 blind；同类别一条成功覆盖压过其他 unavailable；POI 部分失败、半径不足、路由失败/预算耗尽均判 unknown；`no_route` 与 `unavailable` 区分；Polygon 自交失败；输出 BD09LL `[lng,lat]`、合法闭环、稳定排序与 `cell_id`；不泄漏 AK。
6. 通过 `validate_contracts.py`、完整离线 pytest 后，以真实 Location/POI/Routing/Isochrone 链路跑至少一个社区；核对地图上盲区和 unknown 图层，并保留去敏的日期、中心、参数、三类计数、网格/路由数量、warnings、quality、运行命令与结果。不能把一次真实运行当作所有类别准确性证明。

### 阶段 B：Blindspot 合并后做 Diagnosis

1. 冻结第 4 节 Contract，新增 Schema、四种例子与验证脚本。
2. 用注入的五个 Mock Service 走全链：地址/坐标输入；正常、POI 全失败、Routing 部分失败、Isochrone 失败、Blindspot 部分/失败；逐项断言阶段状态、`null` 指标、警告和可重现输出。
3. 实现 `app/modules/diagnosis/service.py` 和 README；只做编排与少量确定性指标，不复制 POI 清洗、路由批处理、等时圈采样或盲区空间算法。
4. 加 `tests/test_diagnosis_contracts.py`、`tests/test_diagnosis_integration.py` 和必要的模块测试；把真实百度 smoke test 设为显式 opt-in，默认不联网。真实数据从 Location 到 Diagnosis 跑全链，验证 POI 搜索半径、成本/耗时、部分结果语义和 GeoJSON 在地图上的显示。
5. 项目当前没有 API 目录；若需要 HTTP 演示，在两个 Tool 的 Contract 与集成测试稳定后另开小 PR 接 API。不要将 Web/Agent 当成 Tool 5/6 的完成条件。

**每阶段完成定义：** Contract 示例可校验；Mock 覆盖正常/空/部分/失败；完整离线测试通过；真实百度数据验证完成并有可复核记录；密钥无泄漏；另一人 Review 后合并。真实百度验证失败时定位是 AK/IP 白名单、配额、网络、Provider、空间计算还是 Contract，不用 Mock 通过来代替。

## 6. Git 协作和合并规则

1. 在正式仓库记录共同基线提交，并保存本 ZIP 的 SHA-256。两人只从该共同提交建分支；没有正式仓库时先建仓库，不把 ZIP 内文件当成可直接合并的 Git 历史。
2. 建议小 PR 顺序：`contract/tool5-blindspot` → `feat/tool5-blindspot` → `contract/tool6-diagnosis` → `feat/tool6-diagnosis`。队友可提前读 Tool 6 草案或写 Mock，但 Tool 6 正式 Schema 应在 Tool 5 Contract 确定后合并。
3. 一个 PR 只维护自己的新模块、对应 Contract/测试/文档。`app/schemas/common.py` 是**唯一共享定义**，原则上不改；确需改时单独 Review 并证明 Tool 1–4 向后兼容。任何同名文件都逐行比较和合并，不整包覆盖。
4. 每次 PR 至少由另一人检查：输入输出、`0/null/unknown` 区别、坐标顺序、GeoJSON 裁剪、错误/警告传播、请求量、AK 泄漏、真实验证记录。Review 通过后才合并，合并后重新运行 `python scripts/validate_contracts.py` 与完整 `python -m pytest -q`，再做显式真实百度 smoke test。
5. 遇到合并冲突，先解决 Contract 和 `common.py`，再解决 Service，最后更新测试；不使用 `ours/theirs` 粗暴吞掉另一个人的改动。`.env`、AK、缓存、原始百度响应、含 AK 的请求 URL 不进 Git 或交接包。

## 7. 最容易出错的验收判据

| 场景 | 正确结论 |
|---|---|
| POI `pois=[]`、该类 `counts=0`、搜索范围足够且完整 | 该类网格可判盲；仍只是百度关键词搜索下的结论。 |
| POI `pois=[]`、该类 `counts=null` | 该类网格 unknown；不能说“没有设施”。 |
| POI 有一处距离很近，但步行路线为 1300m | 不能按直线距离判 covered；若其他候选都被排除/判超距且检索完整，可判 blind。 |
| POI 有一条 900m 步行路线，同时其他候选 unavailable | 该网格 covered；已有充分覆盖证据。 |
| 有候选但 Routing unavailable 或预算未测完 | 没有确认覆盖时判 unknown。 |
| Isochrone 只有三个有效方向且质量 low | 可按现有 Contract 返回 Polygon，但 Diagnosis 整体质量不能 high；地图须显示低置信度。 |
| 设施在 15 分钟圈外，距离圈内边缘网格 800m 步行 | 仍可能覆盖该格；POI 检索必须包含圈外 1km 缓冲。 |
| 中心到 POI `duration_s=850`，`distance_m=1100` | 15 分钟设施计数可计入，但 1km 盲区判定中该路程超阈值。 |

**给接手 Work 的第一条执行任务：**核验正式仓库与这份 ZIP 的差异，提交 Blindspot 的五份 Contract 草案和 Schema PR，请队友先 Review `counts=null`、搜索半径与 `unknown` 规则；确认后才开始空间 Service。
