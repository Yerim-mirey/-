"""Thin orchestration of the five deterministic Core MVP tools."""

from collections.abc import Callable

from app.schemas.blindspot import BlindspotRequest, BlindspotResult, minimum_poi_search_radius_m
from app.schemas.common import Meta, WarningItem
from app.schemas.diagnosis import (
    DiagnosisData,
    DiagnosisErrorCode,
    DiagnosisFailure,
    DiagnosisQuality,
    DiagnosisRequest,
    DiagnosisResult,
    DiagnosisStages,
    DiagnosisSuccess,
    FacilityMetric,
    StageStatus,
    validate_diagnosis_exchange,
)
from app.schemas.isochrone import IsochroneConfidence, IsochroneRequest, IsochroneResult, IsochroneSuccess
from app.schemas.location import LocationRequest, LocationResult, LocationSuccess
from app.schemas.poi import POISearchFailure, POISearchRequest, POISearchResult, POISearchSuccess
from app.schemas.routing import RouteStatus, RouteTarget, RoutingRequest, RoutingResult, RoutingSuccess


def diagnose(
    request: DiagnosisRequest,
    *,
    resolve_location: Callable[[LocationRequest], LocationResult],
    search_pois: Callable[[POISearchRequest], POISearchResult],
    route: Callable[[RoutingRequest], RoutingResult],
    generate_isochrone: Callable[[IsochroneRequest], IsochroneResult],
    analyze_blindspots: Callable[[BlindspotRequest, Callable[[RoutingRequest], RoutingResult]], BlindspotResult],
) -> DiagnosisResult:
    """Return a partial success when POI, facility Routing, or Blindspot degrades."""
    try:
        location = resolve_location(LocationRequest(schema_version="1.0", input=request.location)).root
    except Exception:
        return _failure(DiagnosisErrorCode.LOCATION_FAILED, "位置解析失败。", False, [], None)
    if not isinstance(location, LocationSuccess):
        return _failure(DiagnosisErrorCode.LOCATION_FAILED, "位置解析失败。", location.error.retryable,
                        location.warnings, location.meta.provider)
    center = location.data.center
    try:
        isochrone = generate_isochrone(IsochroneRequest(
            schema_version="1.0", center=center, time_limit_s=request.time_limit_s,
        )).root
    except Exception:
        return _failure(DiagnosisErrorCode.ISOCHRONE_FAILED, "等时圈生成失败。", False,
                        location.warnings, location.meta.provider)
    if not isinstance(isochrone, IsochroneSuccess):
        return _failure(DiagnosisErrorCode.ISOCHRONE_FAILED, "等时圈生成失败。", isochrone.error.retryable,
                        [*location.warnings, *isochrone.warnings], isochrone.meta.provider or location.meta.provider)

    radius = minimum_poi_search_radius_m(center, isochrone.data.geometry, request.service_distance_m)
    poi_request = POISearchRequest(
        schema_version="1.0", center=center, facility_types=request.facility_types,
        search_radius_m=radius,
    )
    try:
        poi_result = search_pois(poi_request)
    except Exception:
        poi_result = POISearchResult(root=POISearchFailure(
            ok=False, error={"code": "PROVIDER_ERROR", "message": "POI 检索不可用。", "retryable": False},
            warnings=[], meta=Meta(schema_version="1.0", provider=None),
        ))
    poi = poi_result.root
    warnings = [*location.warnings, *isochrone.warnings, *poi.warnings]
    if isinstance(poi, POISearchSuccess):
        poi_status = (
            StageStatus.PARTIAL if any(getattr(poi.data.counts, item.value) is None for item in request.facility_types)
            else StageStatus.COMPLETE
        )
    else:
        poi_status = StageStatus.UNAVAILABLE
        warnings.append(WarningItem(code="POI_UNAVAILABLE", message="POI 检索失败，设施数量未知。"))

    facility_routes = None
    route_status = StageStatus.SKIPPED
    if isinstance(poi, POISearchSuccess) and poi.data.pois:
        unique = {item.poi_id: item for item in poi.data.pois}
        if any(unique[item.poi_id].location != item.location for item in poi.data.pois):
            return _failure(DiagnosisErrorCode.COMPUTATION_ERROR, "同一 POI ID 对应不同坐标。", False, warnings, poi.meta.provider)
        route_request = RoutingRequest(
            schema_version="1.0", origin=center,
            targets=[RouteTarget(target_id=item.poi_id, location=item.location) for item in unique.values()],
            travel_mode="walking",
        )
        try:
            route_result = route(route_request).root
        except Exception:
            route_result = None
        if route_result is not None:
            warnings.extend(route_result.warnings)
        if isinstance(route_result, RoutingSuccess) and _routing_matches(route_result, route_request):
            facility_routes = route_result.data
            route_status = StageStatus.PARTIAL if route_result.data.summary.unavailable else StageStatus.COMPLETE
        else:
            route_status = StageStatus.UNAVAILABLE
            warnings.append(WarningItem(code="FACILITY_ROUTING_UNAVAILABLE", message="中心到设施的步行路线不可用。"))

    blindspot = None
    blindspot_status = StageStatus.UNAVAILABLE
    blindspot_request = BlindspotRequest(
        schema_version="1.0", poi_request=poi_request, poi_result=poi_result,
        isochrone_result=IsochroneResult(root=isochrone),
        service_distance_m=request.service_distance_m, grid_size_m=request.grid_size_m,
    )
    try:
        blindspot_result = analyze_blindspots(blindspot_request, route).root
    except Exception:
        blindspot_result = None
    if blindspot_result is not None:
        warnings.extend(blindspot_result.warnings)
    if blindspot_result is not None and blindspot_result.ok:
        blindspot = blindspot_result.data
        blindspot_status = (
            StageStatus.PARTIAL
            if blindspot.quality.confidence is IsochroneConfidence.LOW
            or any(item.unknown_cells for item in blindspot.coverage)
            else StageStatus.COMPLETE
        )
    else:
        warnings.append(WarningItem(code="BLINDSPOT_UNAVAILABLE", message="盲区分析不可用。"))

    metrics = _metrics(request, poi, facility_routes, blindspot)
    isochrone_status = StageStatus.PARTIAL if isochrone.warnings or isochrone.data.quality.confidence is not IsochroneConfidence.HIGH else StageStatus.COMPLETE
    confidence = _confidence(isochrone.data.quality.confidence, blindspot, poi_status, route_status, blindspot_status)
    stages = DiagnosisStages(
        location=StageStatus.COMPLETE, poi=poi_status, routing=route_status,
        isochrone=isochrone_status, blindspot=blindspot_status,
    )
    if any(item in {StageStatus.PARTIAL, StageStatus.UNAVAILABLE} for item in (
        poi_status, route_status, isochrone_status, blindspot_status
    )):
        warnings.append(WarningItem(code="PARTIAL_DIAGNOSIS", message="本次体检有部分阶段未完成或结果不完整。"))

    try:
        result = DiagnosisResult(root=DiagnosisSuccess(
            ok=True,
            data=DiagnosisData(
                center=center, location_label=location.data.label,
                poi_search_radius_m=radius,
                poi=poi.data if isinstance(poi, POISearchSuccess) else None,
                facility_routes=facility_routes, isochrone=isochrone.data,
                blindspot=blindspot, metrics=metrics, stages=stages,
                quality=DiagnosisQuality(confidence=confidence, method="sampled_isochrone_and_grid_walking_distance"),
            ),
            warnings=_unique(warnings),
            meta=Meta(schema_version="1.0", provider=_provider(location, isochrone, poi)),
        ))
        validate_diagnosis_exchange(request, result)
        return result
    except Exception:
        return _failure(DiagnosisErrorCode.COMPUTATION_ERROR, "体检结果组装失败。", False,
                        warnings, _provider(location, isochrone, poi))


def _routing_matches(result: RoutingSuccess, request: RoutingRequest) -> bool:
    expected = {item.target_id: item.location for item in request.targets}
    routes = result.data.routes
    return (
        result.data.origin == request.origin
        and len(routes) == len(expected)
        and len({item.target_id for item in routes}) == len(routes)
        and all(expected.get(item.target_id) == item.location for item in routes)
    )


def _metrics(request, poi, routes, blindspot) -> list[FacilityMetric]:
    by_id = {item.target_id: item for item in routes.routes} if routes else {}
    spatial = {item.facility_type: item for item in blindspot.coverage} if blindspot else {}
    result = []
    for facility in request.facility_types:
        items = [item for item in poi.data.pois if item.facility_type is facility] if isinstance(poi, POISearchSuccess) else []
        successful = [by_id[item.poi_id] for item in items if item.poi_id in by_id and by_id[item.poi_id].status is RouteStatus.SUCCESS]
        nearest = min(successful, key=lambda item: item.distance_m) if successful else None
        if not isinstance(poi, POISearchSuccess):
            confirmed = unresolved = None
        elif routes is not None or not items:
            confirmed = sum(item.duration_s <= request.time_limit_s for item in successful)
            unresolved = len(items) - sum(
                item.poi_id in by_id and by_id[item.poi_id].status in {RouteStatus.SUCCESS, RouteStatus.NO_ROUTE}
                for item in items
            )
        else:
            confirmed, unresolved = None, len(items)
        coverage = spatial.get(facility)
        result.append(FacilityMetric(
            facility_type=facility,
            poi_count=getattr(poi.data.counts, facility.value) if isinstance(poi, POISearchSuccess) else None,
            confirmed_reachable_15m_count=confirmed, unresolved_route_count=unresolved,
            nearest_walk_distance_m=nearest.distance_m if nearest else None,
            nearest_walk_duration_s=nearest.duration_s if nearest else None,
            covered_ratio=coverage.covered_ratio if coverage else None,
            blind_ratio=coverage.blind_ratio if coverage else None,
            unknown_ratio=coverage.unknown_ratio if coverage else None,
        ))
    return result


def _confidence(iso, blindspot, poi_status, route_status, blindspot_status):
    if any(item in {StageStatus.PARTIAL, StageStatus.UNAVAILABLE} for item in (
        poi_status, route_status, blindspot_status
    )) or iso is IsochroneConfidence.LOW:
        return IsochroneConfidence.LOW
    if iso is IsochroneConfidence.MEDIUM or (blindspot and blindspot.quality.confidence is IsochroneConfidence.MEDIUM):
        return IsochroneConfidence.MEDIUM
    return IsochroneConfidence.HIGH


def _provider(location, isochrone, poi) -> str | None:
    return poi.meta.provider or isochrone.meta.provider or location.meta.provider


def _unique(warnings: list[WarningItem]) -> list[WarningItem]:
    return list({(item.code, item.message): item for item in warnings}.values())


def _failure(code, message, retryable, warnings, provider) -> DiagnosisResult:
    return DiagnosisResult(root=DiagnosisFailure(
        ok=False, error={"code": code, "message": message, "retryable": retryable},
        warnings=_unique(warnings), meta=Meta(schema_version="1.0", provider=provider),
    ))
