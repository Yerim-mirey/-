# Tool 5 Blindspot Contract v1.0（技术确认版）

本版已由项目负责人授权独立确认，作为 Tool 5 开发基线；网格计算 Service 已实现，但完整线上地图展示验收仍未完成。继续复用 Tool 1–4 的 `CenterPoint`、`POISearchRequest/Result`、`IsochroneResult`、`WarningItem`、`ErrorDetail`、`Meta`；不改 `common.py`。外层仍是互斥的 `{ok,data,warnings,meta}` / `{ok,error,warnings,meta}`，版本固定为 `1.0`。

## 输入与输出

- 请求：`schema_version`、原样嵌入的 `poi_request`、`poi_result`、`isochrone_result`，以及固定的 `service_distance_m=1000`、`grid_size_m=200` 和默认、上限均为 `max_route_pairs=5000`。Route callable 是 Python 内部依赖，不属于 JSON。
- 成功：`center`、`analysis_geometry`、两个固定阈值、`blind_spots` 与 `unknown_areas` 两个 GeoJSON FeatureCollection、按请求类别列出的 `coverage`、`quality`。无盲区时 `blind_spots.features=[]`，不是失败或 `null`。
- 失败：等时圈失败返回 `ISOCHRONE_UNAVAILABLE`；几何无效、请求无效和空间计算错误分别用 `INVALID_GEOMETRY`、`INVALID_REQUEST`、`COMPUTATION_ERROR`。POI 查询失败本身不等于几何失败，应返回 `unknown`，不能伪造空盲区。
- GeoJSON 顶点使用项目内部的 BD09LL `[lng,lat]`。Feature 可为 Polygon 或 MultiPolygon，允许孔洞；拓扑有效性与裁剪范围由后续空间 Service 用空间库验证。Schema 负责闭环、有限坐标、分类及面积/比例一致性。

## 三条不可混淆的规则

| 输入或路由证据 | 允许的网格状态 |
| --- | --- |
| 某类 `counts=0`，该类确实被请求、检索完整且半径足够 | 无候选时可判 `blind`；`0` 只表示本次百度关键词检索范围内未找到，不是现实中绝对没有。 |
| 某类 `counts=null`、POI 整体失败、未请求该类、或半径不足 | 已有成功路线且步行距离 `<=1000m` 可判 `covered`；其余只能是 `unknown`，绝不可判 `blind`。 |
| 候选 POI 存在，但部分路由 `unavailable` / 未测完 | 有任一已确认 `<=1000m` 路线即可 `covered`；否则 `unknown`。只有所有可能候选均已评估为 `success` 且 `>1000m` 或明确 `no_route`，且 POI 完整、半径足够，才可判 `blind`。 |

1000m 比较的是步行路程，不是直线距离；直线 `>1000m` 仅可作为排除候选的安全预筛。等于 1000m 算 `covered`。等时圈 15 分钟使用步行时间 `<=900s`，不是本 Tool 的盲区距离阈值。

## POI 检索半径

必须覆盖等时圈**最远顶点至中心的局部米制距离 `R` + 1000m 服务缓冲 + 100m 数值余量**：`search_radius_m >= ceil(R + 1100)`。`BlindspotRequest.minimum_poi_search_radius_m()` 给出这一社区尺度近似下限；后续 Diagnosis 用同一方法构造 POI 请求。半径不足时请求仍合法，因为已有 POI 可证明部分 `covered`，但未覆盖网格必须 `unknown` 并发 `POI_RADIUS_INSUFFICIENT`。不得固定搜索 1500m 后宣称盲区完整。

`BlindspotRequest.facility_search_complete(type)` 只表示“该类搜索可用于**尝试**确认 blind”的必要条件：`counts` 是整数且半径足够。它不代替路由证据。发布结果前调用 `validate_blindspot_exchange(request, result)`，核对来源中心/几何、类别、半径、上游警告，以及任何搜索不完整类别都没有 `blind`。POI 部分成功的 `counts=null` 即使携带部分 POI，也不能确认该类盲区。

## 面积、质量与示例

每类覆盖、盲区和未知面积之和等于同一分析域面积，比例按裁剪后网格面积计算；这是网格代表点的估计，不是人口覆盖率。`coverage` 仅包含请求类别，每类网格数之和等于 `quality.grid_cell_count`。未知比例为 0 才可能 `high`；`0<unknown_ratio<=0.1` 最多 `medium`；更高则 `low`。来源 POI 不完整或半径不足时该类为 `low`，整体置信度不高于任一类别及等时圈质量。

`contracts/v1/` 中有六份文件：正常请求/结果、无盲区结果、部分请求/结果、失败结果。正常 Mock 设市场 `counts=0`、药店路线 800m、小学路线 1300m；所以同一分析格分别为 `blind`、`covered`、`blind`。部分 Mock 设市场 `counts=0` 但半径不足、药店 `counts=null`、小学有 800m 已确认路线；因此分别为 `unknown`、`unknown`、`covered`，而非假盲区。示例小方形按局部平面近似为 `9510.11m²`，仅用于 Contract；正式面积须从真实裁剪几何计算。

## Service 实现边界

已实现局部米制平面空间裁剪、无效几何拒绝、内部代表点、按 `poi_id` 回连 Routing、`max_route_pairs` 预算及基于步行路程的状态判定。默认测试注入 Mock 路由，另有保存的真实等时圈样本处理测试；`validate_blindspot_exchange` 在结果发布前运行。未做完整线上地图展示验收。

## 技术确认记录（2026-09-19）

- 六份 JSON 示例均可校验；正常和部分请求/结果可以成对校验，嵌入的 POI、Isochrone RootModel 序列化后没有多余 `root` 字段。
- `counts=0`、`counts=null`、半径刚好达到下限/不足、POI 失败、等时圈失败、错误警告传播、面积/比例/网格数量、类别置信度上限均有拒绝或接受测试。
- 桌面合并目录中 `python scripts/validate_contracts.py` 通过，离线测试为 `152 passed, 3 skipped`。跳过的是需要真实百度请求的测试；本次没有调用百度接口。
- 后续若要改变字段或以上语义，先更新示例和测试，并与队友复核版本影响；不要在 Tool 5 Service 中悄悄改契约。

## 实现复核（2026-09-19）

- 已覆盖正常盲区/覆盖、POI `counts=null`、半径不足、路由超额、路由不可用、`no_route`、无效拓扑、刚好 1km 等边界。
- Tool 1–6 接口校验通过；全套离线测试 `184 passed, 3 skipped`。联网 smoke check 需显式启用，结果另记测试报告。
