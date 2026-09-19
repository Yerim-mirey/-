"""Deterministic grid-based walking-distance blindspot analysis."""

import math
from collections.abc import Callable

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.schemas.blindspot import (
    BlindspotData,
    BlindspotErrorCode,
    BlindspotFailure,
    BlindspotQuality,
    BlindspotRequest,
    BlindspotResult,
    BlindspotSuccess,
    FacilityCoverage,
    validate_blindspot_exchange,
)
from app.schemas.common import CenterPoint, Meta, WarningItem
from app.schemas.isochrone import IsochroneConfidence, IsochroneSuccess
from app.schemas.poi import FacilityType, POI, POISearchSuccess
from app.schemas.routing import (
    RouteResult,
    RouteStatus,
    RouteTarget,
    RoutingRequest,
    RoutingResult,
    RoutingSuccess,
)


Route = Callable[[RoutingRequest], RoutingResult]
_EARTH_RADIUS_M = 6_371_008.8
_CONFIDENCE = {"low": 0, "medium": 1, "high": 2}


def analyze_blindspots(request: BlindspotRequest, route: Route) -> BlindspotResult:
    """Use only normalized upstream contracts; never call a provider directly."""
    isochrone = request.isochrone_result.root
    if not isinstance(isochrone, IsochroneSuccess):
        result = _failure(
            request,
            BlindspotErrorCode.ISOCHRONE_UNAVAILABLE,
            "等时圈不可用，无法确定盲区分析范围。",
            isochrone.error.retryable,
            isochrone.warnings,
        )
        validate_blindspot_exchange(request, result)
        return result

    center = request.poi_request.center
    cosine = math.cos(math.radians(center.lat))
    if abs(cosine) < 0.01:
        return _failure(request, BlindspotErrorCode.INVALID_REQUEST, "分析范围不支持极区坐标。", False)

    def to_m(lng: float, lat: float) -> tuple[float, float]:
        return (
            math.radians(lng - center.lng) * _EARTH_RADIUS_M * cosine,
            math.radians(lat - center.lat) * _EARTH_RADIUS_M,
        )

    def to_center(x: float, y: float) -> CenterPoint:
        return CenterPoint(
            lng=center.lng + math.degrees(x / (_EARTH_RADIUS_M * cosine)),
            lat=center.lat + math.degrees(y / _EARTH_RADIUS_M),
            crs="BD09LL",
        )

    try:
        domain = Polygon([to_m(*point) for point in isochrone.data.geometry.coordinates[0]])
        if not domain.is_valid or domain.is_empty or domain.area <= 0:
            return _failure(request, BlindspotErrorCode.INVALID_GEOMETRY, "等时圈多边形无效。", False)
        if max(abs(value) for value in domain.bounds) > 20_000:
            return _failure(request, BlindspotErrorCode.INVALID_REQUEST, "分析范围超出社区尺度。", False)
        cells = _grid_cells(domain, request.grid_size_m)
        if not cells or len(cells) > 10_000:
            return _failure(request, BlindspotErrorCode.INVALID_GEOMETRY, "分析域无法生成有效网格。", False)
        result = _analyze(request, route, isochrone, cells, to_m, to_center)
        validate_blindspot_exchange(request, result)
        return result
    except Exception:
        # Public errors must not expose provider credentials or raw responses.
        return _failure(request, BlindspotErrorCode.COMPUTATION_ERROR, "盲区空间计算失败。", False)


def _grid_cells(domain: Polygon, grid_size: int) -> list[tuple[str, BaseGeometry]]:
    minx, miny, maxx, maxy = domain.bounds
    half = grid_size / 2
    rows = range(math.floor((miny + half) / grid_size), math.ceil((maxy + half) / grid_size))
    cols = range(math.floor((minx + half) / grid_size), math.ceil((maxx + half) / grid_size))
    cells = []
    for row in rows:
        for col in cols:
            x0, y0 = col * grid_size - half, row * grid_size - half
            clipped = domain.intersection(box(x0, y0, x0 + grid_size, y0 + grid_size))
            parts = _polygon_parts(clipped)
            if parts:
                cells.append((f"grid:r{row:04d}:c{col:04d}", unary_union(parts)))
    return cells


def _polygon_parts(geometry: BaseGeometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry] if geometry.area > 0 else []
    return [part for item in getattr(geometry, "geoms", ()) for part in _polygon_parts(item)]


def _analyze(request, route, isochrone, cells, to_m, to_center) -> BlindspotResult:
    poi_result = request.poi_result.root
    pois = poi_result.data.pois if isinstance(poi_result, POISearchSuccess) else []
    locations = {poi.poi_id: poi.location for poi in pois}
    if any(locations[poi.poi_id] != poi.location for poi in pois):
        return _failure(request, BlindspotErrorCode.INVALID_REQUEST, "同一 POI ID 对应不同坐标。", False)
    by_type = {facility: [poi for poi in pois if poi.facility_type is facility] for facility in FacilityType}
    poi_xy = {poi.poi_id: to_m(poi.location.lng, poi.location.lat) for poi in pois}
    requested = list(request.poi_request.facility_types)
    area = sum(cell.area for _, cell in cells)
    stats = {item: {"covered": 0.0, "blind": 0.0, "unknown": 0.0,
                    "covered_cells": 0, "blind_cells": 0, "unknown_cells": 0} for item in requested}
    features = {"blind": [], "unknown": []}
    warnings = [*isochrone.warnings, *poi_result.warnings]
    if not isinstance(poi_result, POISearchSuccess):
        warnings.append(WarningItem(code="POI_UNAVAILABLE", message="POI 检索失败，盲区无法确认。"))
    if request.poi_request.search_radius_m < request.minimum_poi_search_radius_m():
        warnings.append(WarningItem(code="POI_RADIUS_INSUFFICIENT", message="POI 检索范围未覆盖等时圈外 1 公里缓冲。"))
    route_requested = route_succeeded = route_unavailable = 0
    budget_exhausted = False

    for cell_id, clipped in cells:
        origin_xy = clipped.representative_point()
        origin = to_center(origin_xy.x, origin_xy.y)
        candidates = {
            facility: [poi for poi in by_type[facility]
                       if math.dist((origin_xy.x, origin_xy.y), poi_xy[poi.poi_id]) <= request.service_distance_m + 1e-6]
            for facility in requested
        }
        unique = {poi.poi_id: poi for group in candidates.values() for poi in group}
        ordered = [unique[poi_id] for poi_id in sorted(unique)]
        remaining = request.max_route_pairs - route_requested
        selected = ordered[:remaining]
        if len(selected) < len(ordered):
            budget_exhausted = True
        measured = _measure(origin, selected, route, warnings)
        route_requested += len(selected)
        route_succeeded += sum(item is not None and item.status is RouteStatus.SUCCESS for item in measured.values())
        route_unavailable += sum(item is None or item.status is RouteStatus.UNAVAILABLE for item in measured.values())

        for facility in requested:
            group = candidates[facility]
            if any(
                (item := measured.get(poi.poi_id)) is not None
                and item.status is RouteStatus.SUCCESS
                and item.distance_m <= request.service_distance_m
                for poi in group
            ):
                status = "covered"
            elif not request.facility_search_complete(facility) or any(
                poi.poi_id not in measured
                or measured[poi.poi_id] is None
                or measured[poi.poi_id].status is RouteStatus.UNAVAILABLE
                for poi in group
            ):
                status = "unknown"
            else:
                status = "blind"  # All nearby candidates are success >1 km or no_route.
            stats[facility][status] += clipped.area
            stats[facility][f"{status}_cells"] += 1
            if status != "covered":
                features[status].append({
                    "type": "Feature",
                    "geometry": _geojson(clipped, to_center),
                    "properties": {"cell_id": cell_id, "facility_type": facility, "classification": status},
                })

    if budget_exhausted:
        warnings.append(WarningItem(code="ROUTING_BUDGET_EXCEEDED", message="部分候选设施未测时，相关网格标为未知。"))
    if route_unavailable:
        warnings.append(WarningItem(code="PARTIAL_ROUTING_RESULTS", message="部分网格到设施的步行路线不可用。"))
    if features["unknown"]:
        warnings.append(WarningItem(code="UNKNOWN_BLINDSPOT_AREA", message="存在证据不足、不能判为盲区的网格。"))

    coverage = []
    for facility in requested:
        values = stats[facility]
        unknown_ratio = values["unknown"] / area
        if not request.facility_search_complete(facility) or isochrone.data.quality.confidence is IsochroneConfidence.LOW or unknown_ratio > 0.1:
            confidence = IsochroneConfidence.LOW
        elif unknown_ratio > 0 or isochrone.data.quality.confidence is IsochroneConfidence.MEDIUM:
            confidence = IsochroneConfidence.MEDIUM
        else:
            confidence = IsochroneConfidence.HIGH
        coverage.append(FacilityCoverage(
            facility_type=facility, total_area_m2=area,
            covered_area_m2=values["covered"], blind_area_m2=values["blind"], unknown_area_m2=values["unknown"],
            covered_ratio=values["covered"] / area, blind_ratio=values["blind"] / area, unknown_ratio=unknown_ratio,
            covered_cells=values["covered_cells"], blind_cells=values["blind_cells"], unknown_cells=values["unknown_cells"],
            confidence=confidence,
        ))
    confidence = min((item.confidence for item in coverage), key=lambda item: _CONFIDENCE[item.value])
    if confidence is IsochroneConfidence.LOW:
        warnings.append(WarningItem(code="LOW_CONFIDENCE", message="本次盲区分析置信度较低。"))

    result = BlindspotResult(root=BlindspotSuccess(
        ok=True,
        data=BlindspotData(
            center=request.poi_request.center, analysis_geometry=isochrone.data.geometry,
            service_distance_m=request.service_distance_m, grid_size_m=request.grid_size_m,
            blind_spots={"type": "FeatureCollection", "features": features["blind"]},
            unknown_areas={"type": "FeatureCollection", "features": features["unknown"]},
            coverage=coverage,
            quality=BlindspotQuality(
                grid_cell_count=len(cells), route_pairs_requested=route_requested,
                route_pairs_succeeded=route_succeeded, route_pairs_unavailable=route_unavailable,
                route_budget_exhausted=budget_exhausted, method="grid_representative_walking_distance", confidence=confidence,
            ),
        ),
        warnings=_unique_warnings(warnings), meta=_meta(request),
    ))
    return result


def _measure(origin: CenterPoint, selected: list[POI], route: Route, warnings: list[WarningItem]) -> dict[str, RouteResult | None]:
    if not selected:
        return {}
    request = RoutingRequest(
        schema_version="1.0", origin=origin,
        targets=[RouteTarget(target_id=poi.poi_id, location=poi.location) for poi in selected],
        travel_mode="walking",
    )
    try:
        response = route(request).root
    except Exception:
        return {poi.poi_id: None for poi in selected}
    warnings.extend(response.warnings)
    if not isinstance(response, RoutingSuccess) or response.data.origin != origin:
        return {poi.poi_id: None for poi in selected}
    expected = {poi.poi_id: poi.location for poi in selected}
    found: dict[str, RouteResult | None] = {}
    for item in response.data.routes:
        if item.target_id in expected:
            if item.target_id in found or item.location != expected[item.target_id]:
                found[item.target_id] = None
            else:
                found[item.target_id] = item
    return {poi.poi_id: found.get(poi.poi_id) for poi in selected}


def _geojson(geometry: BaseGeometry, to_center: Callable[[float, float], CenterPoint]) -> dict:
    parts = _polygon_parts(geometry)

    def polygon_coordinates(polygon: Polygon) -> list[list[list[float]]]:
        rings = [polygon.exterior, *polygon.interiors]
        return [[[to_center(x, y).lng, to_center(x, y).lat] for x, y in ring.coords] for ring in rings]

    if len(parts) == 1:
        return {"type": "Polygon", "coordinates": polygon_coordinates(parts[0])}
    return {"type": "MultiPolygon", "coordinates": [polygon_coordinates(part) for part in parts]}


def _unique_warnings(warnings: list[WarningItem]) -> list[WarningItem]:
    return list({(item.code, item.message): item for item in warnings}.values())


def _meta(request: BlindspotRequest) -> Meta:
    provider = request.poi_result.root.meta.provider or request.isochrone_result.root.meta.provider
    return Meta(schema_version="1.0", provider=provider)


def _failure(
    request: BlindspotRequest, code: BlindspotErrorCode, message: str,
    retryable: bool, warnings: list[WarningItem] | None = None,
) -> BlindspotResult:
    return BlindspotResult(root=BlindspotFailure(
        ok=False, error={"code": code, "message": message, "retryable": retryable},
        warnings=_unique_warnings(warnings or []), meta=_meta(request),
    ))
