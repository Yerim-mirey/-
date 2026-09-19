# Tool 6 Diagnosis Contract v1.0

Diagnosis 是一次体检的薄编排，不直接访问百度原始 JSON，也不复制 Tool 1–5 的算法。请求复用 `LocationInput`，类别限 `market`、`pharmacy`、`primary_school`，15 分钟、1km 服务距离和 200m 网格在 v1 固定为 `900s`、`1000m`、`200m`。成功/失败仍使用共享 Envelope 和 `schema_version="1.0"`。

## 调用顺序

1. Location 取得唯一 BD09LL `CenterPoint`；失败返回 `LOCATION_FAILED`。
2. Isochrone 生成 900 秒分析域；失败返回 `ISOCHRONE_FAILED`。
3. 用多边形最远顶点的局部米制距离 `R + 1000m + 100m` 计算 POI 搜索半径。不可预先固定为 1500m。
4. POI 成功时按唯一 `poi_id` 路由中心到设施；空 POI 跳过空 RoutingRequest。中心到设施的 15 分钟可达数只使用 `success && duration_s <= 900`。
5. Blindspot 接收原始的 `POISearchRequest/Result`、`IsochroneResult`，另用同一 Routing 接口计算网格到设施的 **步行距离** `<=1000m`。

## `null`、0 与阶段状态

- `poi_count` 原样沿用 POI 的整数或 `null`；`null` 是检索不完整/未知，绝非 0。
- `confirmed_reachable_15m_count` 是**已检索且已确认**的数量下界，不是全域真实设施总数。POI 失败、或该类别有 POI 但中心路由整体失败时为 `null`；完整空 POI 时为 `0`；部分路由成功时仍可给出已确认数，并用 `unresolved_route_count` 显示未决目标。
- `nearest_walk_distance_m` 与 `nearest_walk_duration_s` 来自同一条最短成功步行路线；没有成功路线则都为 `null`。
- `covered_ratio`、`blind_ratio`、`unknown_ratio` 只沿用 Blindspot；Blindspot 失败时全部为 `null`，不能用 0 冒充。
- `stages` 的 `complete|partial|unavailable|skipped` 分别记录 location、poi、routing、isochrone、blindspot。`ok:true` 仅表示可返回带分析域的体检结果，不表示每个阶段完整；还须看 `stages`、`quality` 和 `warnings`。
- Location/Isochrone 是成功结果的必需阶段。POI、中心 Routing、Blindspot 失败可保留部分成功 Envelope，质量降为 `low` 并带 `PARTIAL_DIAGNOSIS` 等警告。

四份 `contracts/v1/diagnosis-*.example.json` 覆盖正常、`counts=null` 与 unknown 的部分结果、以及位置失败。Schema 和 `validate_diagnosis_exchange` 核对类别顺序、中心、半径、POI 计数、路线目标及 Blindspot 比例。示例均为 Mock，不含 AK。真实百度验证应显式启动并记录去敏信息；离线测试不自动联网。
