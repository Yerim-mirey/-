# Tool 5 — Blindspot

`analyze_blindspots(request: BlindspotRequest, route: Callable[[RoutingRequest], RoutingResult]) -> BlindspotResult` 接收 Tool 2/4 的公共结果，内部注入 Tool 3 Routing；不访问百度原始响应。对每个裁剪到 15 分钟等时圈内的 200m 网格，用内部代表点到候选 POI 的步行路程判断 1km 设施覆盖。

- `covered`：该类至少一条已确认步行路线 `distance_m <= 1000`。
- `blind`：该类 POI 完整、半径覆盖 `R+1100m`，且所有直线距离不超过 1km 的候选路线均已确认超距或 `no_route`；完整空类也可判盲。
- `unknown`：POI `counts=null`、半径不足、路由不可用或预算耗尽，且没有已确认覆盖路线。

Shapely 仅在局部米制平面做多边形拓扑、裁剪、代表点和面积；输出坐标转回项目内部 BD09LL `[lng,lat]`。支持社区尺度，不支持极区、跨 180° 经线或大区域。无效多边形直接返回 `INVALID_GEOMETRY`，不自动修补。每次最多 5000 个网格→POI 路由目标对；原始百度数据、AK 和 URL 不进入结果。

Contract 细节与限制见 `docs/architecture/blindspot-contract-v1.md`。默认测试只用 Mock，不调用真实百度。联网验证应单独设置 AK、限制设施类别和请求预算，并记录去敏结果。
