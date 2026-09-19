"""Tool 5 public contract; spatial classification belongs to the service."""

import math
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import Field, RootModel, model_validator

from app.schemas.common import (
    CenterPoint,
    ContractModel,
    ErrorDetail,
    Meta,
    SchemaVersion,
    WarningItem,
)
from app.schemas.isochrone import (
    GeoJSONPolygon,
    IsochroneConfidence,
    IsochroneSuccess,
    IsochroneResult,
)
from app.schemas.poi import FacilityType, POISearchResult, POISearchRequest, POISearchSuccess


_EARTH_RADIUS_M = 6_371_008.8
_POI_RADIUS_MARGIN_M = 100
_CONFIDENCE_RANK = {
    IsochroneConfidence.LOW: 0,
    IsochroneConfidence.MEDIUM: 1,
    IsochroneConfidence.HIGH: 2,
}


def minimum_poi_search_radius_m(
    center: CenterPoint, geometry: GeoJSONPolygon, service_distance_m: int = 1000
) -> int:
    """Community-scale local-plane radius covering polygon plus service buffer."""
    lat_scale = math.cos(math.radians(center.lat))
    radius = max(
        math.hypot(
            math.radians(lng - center.lng) * _EARTH_RADIUS_M * lat_scale,
            math.radians(lat - center.lat) * _EARTH_RADIUS_M,
        )
        for lng, lat in geometry.coordinates[0]
    )
    return math.ceil(radius + service_distance_m + _POI_RADIUS_MARGIN_M)


class BlindspotErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    ISOCHRONE_UNAVAILABLE = "ISOCHRONE_UNAVAILABLE"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    COMPUTATION_ERROR = "COMPUTATION_ERROR"


class BlindspotRequest(ContractModel):
    schema_version: SchemaVersion
    poi_request: POISearchRequest
    poi_result: POISearchResult
    isochrone_result: IsochroneResult
    service_distance_m: Literal[1000]
    grid_size_m: Literal[200]
    max_route_pairs: int = Field(default=5000, gt=0, le=5000)

    @model_validator(mode="after")
    def inputs_are_consistent(self) -> "BlindspotRequest":
        center = self.poi_request.center
        requested = [item.value for item in self.poi_request.facility_types]
        if len(requested) != len(set(requested)):
            raise ValueError("facility_types 不能重复。")

        isochrone = self.isochrone_result.root
        if isinstance(isochrone, IsochroneSuccess):
            if isochrone.data.center != center or isochrone.data.time_limit_s != 900:
                raise ValueError("等时圈必须与 POI 共用中心且为 15 分钟。")

        poi = self.poi_result.root
        if isinstance(poi, POISearchSuccess):
            if poi.data.center != center:
                raise ValueError("POI 结果中心与请求中心不一致。")
            counts = poi.data.counts.model_dump()
            for facility in FacilityType:
                name = facility.value
                actual = sum(item.facility_type is facility for item in poi.data.pois)
                count = counts[name]
                if name not in requested:
                    if count is not None or actual:
                        raise ValueError("未请求的类别不能包含 POI 或确定计数。")
                elif count is not None and count != actual:
                    raise ValueError("完整类别的 counts 必须等于返回的去重 POI 数量。")
            if any(item.facility_type.value not in requested for item in poi.data.pois):
                raise ValueError("POI 结果包含未请求的类别。")
            if any(counts[name] is None for name in requested) and not any(
                item.code == "PARTIAL_POI_RESULTS" for item in poi.warnings
            ):
                raise ValueError("请求类别 counts=null 时必须标注 PARTIAL_POI_RESULTS。")
        return self

    def minimum_poi_search_radius_m(self) -> int | None:
        """Community-scale local-plane bound: polygon radius + 1 km + 100 m."""
        isochrone = self.isochrone_result.root
        if not isinstance(isochrone, IsochroneSuccess):
            return None
        return minimum_poi_search_radius_m(
            self.poi_request.center, isochrone.data.geometry, self.service_distance_m
        )

    def facility_search_complete(self, facility_type: FacilityType) -> bool:
        """Necessary, not sufficient, evidence for classifying a cell as blind."""
        minimum = self.minimum_poi_search_radius_m()
        poi = self.poi_result.root
        return (
            facility_type in self.poi_request.facility_types
            and minimum is not None
            and self.poi_request.search_radius_m >= minimum
            and isinstance(poi, POISearchSuccess)
            and getattr(poi.data.counts, facility_type.value) is not None
        )


def _valid_ring(ring: list[list[float]]) -> None:
    if len(ring) < 4 or ring[0] != ring[-1]:
        raise ValueError("GeoJSON 环必须有至少三个顶点并闭合。")
    if len({tuple(point) for point in ring[:-1]}) < 3:
        raise ValueError("GeoJSON 环至少需要三个不同顶点。")
    for point in ring:
        if (
            len(point) != 2
            or not all(math.isfinite(value) for value in point)
            or not -180 <= point[0] <= 180
            or not -90 <= point[1] <= 90
        ):
            raise ValueError("GeoJSON 坐标必须是有限的 [lng, lat]。")


class ClippedPolygon(ContractModel):
    type: Literal["Polygon"]
    coordinates: list[list[list[float]]] = Field(min_length=1)

    @model_validator(mode="after")
    def rings_are_closed(self) -> "ClippedPolygon":
        for ring in self.coordinates:
            _valid_ring(ring)
        return self


class ClippedMultiPolygon(ContractModel):
    type: Literal["MultiPolygon"]
    coordinates: list[list[list[list[float]]]] = Field(min_length=1)

    @model_validator(mode="after")
    def rings_are_closed(self) -> "ClippedMultiPolygon":
        for polygon in self.coordinates:
            if not polygon:
                raise ValueError("MultiPolygon 不能包含空 Polygon。")
            for ring in polygon:
                _valid_ring(ring)
        return self


ClippedGeometry = Annotated[
    Union[ClippedPolygon, ClippedMultiPolygon], Field(discriminator="type")
]


class CellClassification(str, Enum):
    BLIND = "blind"
    UNKNOWN = "unknown"


class BlindspotFeatureProperties(ContractModel):
    cell_id: str = Field(min_length=1)
    facility_type: FacilityType = Field(strict=False)
    classification: CellClassification = Field(strict=False)


class BlindspotFeature(ContractModel):
    type: Literal["Feature"]
    geometry: ClippedGeometry
    properties: BlindspotFeatureProperties


class BlindspotFeatureCollection(ContractModel):
    type: Literal["FeatureCollection"]
    features: list[BlindspotFeature]


class FacilityCoverage(ContractModel):
    facility_type: FacilityType = Field(strict=False)
    total_area_m2: float = Field(gt=0, allow_inf_nan=False)
    covered_area_m2: float = Field(ge=0, allow_inf_nan=False)
    blind_area_m2: float = Field(ge=0, allow_inf_nan=False)
    unknown_area_m2: float = Field(ge=0, allow_inf_nan=False)
    covered_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    blind_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    unknown_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    covered_cells: int = Field(ge=0)
    blind_cells: int = Field(ge=0)
    unknown_cells: int = Field(ge=0)
    confidence: IsochroneConfidence = Field(strict=False)

    @model_validator(mode="after")
    def areas_and_ratios_agree(self) -> "FacilityCoverage":
        areas = (self.covered_area_m2, self.blind_area_m2, self.unknown_area_m2)
        ratios = (self.covered_ratio, self.blind_ratio, self.unknown_ratio)
        cells = (self.covered_cells, self.blind_cells, self.unknown_cells)
        if abs(sum(areas) - self.total_area_m2) > max(0.01, self.total_area_m2 * 1e-6):
            raise ValueError("三类面积之和必须等于分析域面积。")
        if any(abs(ratio - area / self.total_area_m2) > 1e-4 for area, ratio in zip(areas, ratios)):
            raise ValueError("面积比例与面积不一致。")
        if abs(sum(ratios) - 1) > 1e-4 or any((area == 0) != (count == 0) for area, count in zip(areas, cells)):
            raise ValueError("面积、比例和网格数量不一致。")
        if self.unknown_ratio > 0.1 and self.confidence is not IsochroneConfidence.LOW:
            raise ValueError("未知面积超过 10% 时置信度必须为 low。")
        if self.unknown_ratio > 0 and self.confidence is IsochroneConfidence.HIGH:
            raise ValueError("存在未知面积时置信度不能为 high。")
        return self


class BlindspotQuality(ContractModel):
    grid_cell_count: int = Field(gt=0)
    route_pairs_requested: int = Field(ge=0)
    route_pairs_succeeded: int = Field(ge=0)
    route_pairs_unavailable: int = Field(ge=0)
    route_budget_exhausted: bool
    method: Literal["grid_representative_walking_distance"]
    confidence: IsochroneConfidence = Field(strict=False)

    @model_validator(mode="after")
    def route_counts_agree(self) -> "BlindspotQuality":
        if self.route_pairs_succeeded + self.route_pairs_unavailable > self.route_pairs_requested:
            raise ValueError("路由成功与不可用数量不能超过请求数量。")
        return self


class BlindspotData(ContractModel):
    center: CenterPoint
    analysis_geometry: GeoJSONPolygon
    service_distance_m: Literal[1000]
    grid_size_m: Literal[200]
    blind_spots: BlindspotFeatureCollection
    unknown_areas: BlindspotFeatureCollection
    coverage: list[FacilityCoverage] = Field(min_length=1, max_length=3)
    quality: BlindspotQuality

    @model_validator(mode="after")
    def cells_and_coverage_agree(self) -> "BlindspotData":
        by_type = {item.facility_type: item for item in self.coverage}
        if len(by_type) != len(self.coverage):
            raise ValueError("coverage 的设施类别不能重复。")
        if len({item.total_area_m2 for item in self.coverage}) != 1:
            raise ValueError("各设施类别的分析域面积必须相同。")

        seen: set[tuple[str, FacilityType]] = set()
        for collection, expected in (
            (self.blind_spots, CellClassification.BLIND),
            (self.unknown_areas, CellClassification.UNKNOWN),
        ):
            for feature in collection.features:
                props = feature.properties
                key = (props.cell_id, props.facility_type)
                if props.classification is not expected or props.facility_type not in by_type or key in seen:
                    raise ValueError("Feature 的分类、类别或 cell_id 不一致。")
                seen.add(key)

        for facility, item in by_type.items():
            if item.covered_cells + item.blind_cells + item.unknown_cells != self.quality.grid_cell_count:
                raise ValueError("每类网格数量必须等于 grid_cell_count。")
            if item.blind_cells != sum(
                feature.properties.facility_type is facility for feature in self.blind_spots.features
            ) or item.unknown_cells != sum(
                feature.properties.facility_type is facility for feature in self.unknown_areas.features
            ):
                raise ValueError("盲区/未知 Feature 数量与 coverage 不一致。")
        if _CONFIDENCE_RANK[self.quality.confidence] > min(
            _CONFIDENCE_RANK[item.confidence] for item in self.coverage
        ):
            raise ValueError("整体置信度不能高于任一设施类别。")
        return self


class BlindspotErrorDetail(ErrorDetail):
    code: BlindspotErrorCode = Field(strict=False)


class BlindspotSuccess(ContractModel):
    ok: Literal[True]
    data: BlindspotData
    warnings: list[WarningItem]
    meta: Meta

    @model_validator(mode="after")
    def warning_matches_unknown(self) -> "BlindspotSuccess":
        if len({(item.code, item.message) for item in self.warnings}) != len(self.warnings):
            raise ValueError("重复的 warning 必须按代码和消息去重。")
        if self.data.unknown_areas.features and not any(
            item.code == "UNKNOWN_BLINDSPOT_AREA" for item in self.warnings
        ):
            raise ValueError("存在 unknown 区时必须提供 UNKNOWN_BLINDSPOT_AREA 警告。")
        if self.data.quality.route_budget_exhausted and not any(
            item.code == "ROUTING_BUDGET_EXCEEDED" for item in self.warnings
        ):
            raise ValueError("路由预算耗尽时必须提供警告。")
        if self.data.quality.confidence is IsochroneConfidence.LOW and not any(
            item.code == "LOW_CONFIDENCE" for item in self.warnings
        ):
            raise ValueError("整体置信度 low 时必须提供 LOW_CONFIDENCE 警告。")
        return self


class BlindspotFailure(ContractModel):
    ok: Literal[False]
    error: BlindspotErrorDetail
    warnings: list[WarningItem]
    meta: Meta

    @model_validator(mode="after")
    def warnings_are_unique(self) -> "BlindspotFailure":
        if len({(item.code, item.message) for item in self.warnings}) != len(self.warnings):
            raise ValueError("重复的 warning 必须按代码和消息去重。")
        return self


BlindspotEnvelope = Annotated[
    Union[BlindspotSuccess, BlindspotFailure], Field(discriminator="ok")
]


class BlindspotResult(RootModel[BlindspotEnvelope]):
    pass


def validate_blindspot_exchange(request: BlindspotRequest, result: BlindspotResult) -> None:
    """Validate facts that require both envelopes, before publishing a result."""
    isochrone = request.isochrone_result.root
    response = result.root
    if not isinstance(isochrone, IsochroneSuccess):
        if isinstance(response, BlindspotSuccess) or response.error.code is not BlindspotErrorCode.ISOCHRONE_UNAVAILABLE:
            raise ValueError("等时圈失败时 Blindspot 必须返回 ISOCHRONE_UNAVAILABLE。")
        if response.error.retryable != isochrone.error.retryable:
            raise ValueError("等时圈失败时 retryable 必须沿用上游。")
        observed = {(item.code, item.message) for item in response.warnings}
        if any((item.code, item.message) not in observed for item in isochrone.warnings):
            raise ValueError("等时圈失败时必须保留上游警告。")
        return
    if not isinstance(response, BlindspotSuccess):
        if response.error.code is BlindspotErrorCode.ISOCHRONE_UNAVAILABLE:
            raise ValueError("等时圈成功时不能报告 ISOCHRONE_UNAVAILABLE。")
        return  # Invalid geometry/computation can still fail with a usable isochrone.

    data = response.data
    if (
        data.center != request.poi_request.center
        or data.analysis_geometry != isochrone.data.geometry
        or data.service_distance_m != request.service_distance_m
        or data.grid_size_m != request.grid_size_m
    ):
        raise ValueError("Blindspot 输出必须与请求中心、分析域和阈值一致。")

    requested = list(request.poi_request.facility_types)
    if [item.facility_type for item in data.coverage] != requested:
        raise ValueError("coverage 必须按请求顺序包含且只包含请求的设施类别。")
    if data.quality.route_pairs_requested > request.max_route_pairs:
        raise ValueError("路由请求目标对数不能超过请求预算。")

    poi = request.poi_result.root
    for item in data.coverage:
        if not request.facility_search_complete(item.facility_type):
            if item.blind_cells or item.blind_area_m2 or item.confidence is not IsochroneConfidence.LOW:
                raise ValueError("POI 搜索不完整或半径不足的类别不能判 blind，且置信度必须 low。")
            if not isinstance(poi, POISearchSuccess) and item.covered_cells:
                raise ValueError("POI 整体失败时不能确认任何覆盖网格。")

    if request.poi_request.search_radius_m < request.minimum_poi_search_radius_m() and not any(
        item.code == "POI_RADIUS_INSUFFICIENT" for item in response.warnings
    ):
        raise ValueError("半径不足必须发 POI_RADIUS_INSUFFICIENT 警告。")

    upstream = [*isochrone.warnings, *poi.warnings]
    observed = {(item.code, item.message) for item in response.warnings}
    if any((item.code, item.message) not in observed for item in upstream):
        raise ValueError("Blindspot 必须保留上游 POI/Isochrone 警告。")
    if any(
        _CONFIDENCE_RANK[item.confidence] > _CONFIDENCE_RANK[isochrone.data.quality.confidence]
        for item in data.coverage
    ):
        raise ValueError("设施类别置信度不能高于等时圈置信度。")
