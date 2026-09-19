"""Tool 6 public contract for one Core MVP diagnosis."""

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import Field, RootModel, model_validator

from app.schemas.blindspot import BlindspotData, minimum_poi_search_radius_m
from app.schemas.common import CenterPoint, ContractModel, ErrorDetail, Meta, SchemaVersion, WarningItem
from app.schemas.isochrone import IsochroneConfidence, IsochroneData
from app.schemas.location import LocationInput
from app.schemas.poi import FacilityType, POISearchData
from app.schemas.routing import RoutingData


class DiagnosisErrorCode(str, Enum):
    LOCATION_FAILED = "LOCATION_FAILED"
    ISOCHRONE_FAILED = "ISOCHRONE_FAILED"
    INVALID_REQUEST = "INVALID_REQUEST"
    COMPUTATION_ERROR = "COMPUTATION_ERROR"


class StageStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


class DiagnosisRequest(ContractModel):
    schema_version: SchemaVersion
    location: LocationInput
    facility_types: list[Annotated[FacilityType, Field(strict=False)]] = Field(
        default_factory=lambda: list(FacilityType), min_length=1
    )
    time_limit_s: Literal[900] = 900
    service_distance_m: Literal[1000] = 1000
    grid_size_m: Literal[200] = 200

    @model_validator(mode="after")
    def categories_are_unique(self) -> "DiagnosisRequest":
        object.__setattr__(self, "facility_types", list(dict.fromkeys(self.facility_types)))
        return self


class FacilityMetric(ContractModel):
    facility_type: FacilityType = Field(strict=False)
    poi_count: int | None = Field(ge=0)
    confirmed_reachable_15m_count: int | None = Field(ge=0)
    unresolved_route_count: int | None = Field(ge=0)
    nearest_walk_distance_m: int | None = Field(ge=0)
    nearest_walk_duration_s: int | None = Field(ge=0)
    covered_ratio: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    blind_ratio: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    unknown_ratio: float | None = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def related_values_match(self) -> "FacilityMetric":
        if (self.nearest_walk_distance_m is None) != (self.nearest_walk_duration_s is None):
            raise ValueError("最近步行距离和时间必须同时存在或同时为空。")
        ratios = (self.covered_ratio, self.blind_ratio, self.unknown_ratio)
        if any(item is None for item in ratios) and not all(item is None for item in ratios):
            raise ValueError("三种空间比例必须同时存在或同时为空。")
        if all(item is not None for item in ratios) and abs(sum(ratios) - 1) > 1e-4:
            raise ValueError("三种空间比例之和必须为 1。")
        return self


class DiagnosisStages(ContractModel):
    location: StageStatus = Field(strict=False)
    poi: StageStatus = Field(strict=False)
    routing: StageStatus = Field(strict=False)
    isochrone: StageStatus = Field(strict=False)
    blindspot: StageStatus = Field(strict=False)


class DiagnosisQuality(ContractModel):
    confidence: IsochroneConfidence = Field(strict=False)
    method: Literal["sampled_isochrone_and_grid_walking_distance"]


class DiagnosisData(ContractModel):
    center: CenterPoint
    location_label: str | None = Field(default=None, min_length=1)
    poi_search_radius_m: int = Field(gt=0)
    poi: POISearchData | None
    facility_routes: RoutingData | None
    isochrone: IsochroneData
    blindspot: BlindspotData | None
    metrics: list[FacilityMetric] = Field(min_length=1, max_length=3)
    stages: DiagnosisStages
    quality: DiagnosisQuality

    @model_validator(mode="after")
    def stages_and_sources_match(self) -> "DiagnosisData":
        if self.isochrone.center != self.center or self.isochrone.time_limit_s != 900:
            raise ValueError("Diagnosis 等时圈中心和时限必须一致。")
        if self.poi is not None and self.poi.center != self.center:
            raise ValueError("Diagnosis POI 中心必须一致。")
        if self.facility_routes is not None and self.facility_routes.origin != self.center:
            raise ValueError("设施路由起点必须为中心。")
        if self.blindspot is not None and (
            self.blindspot.center != self.center or self.blindspot.analysis_geometry != self.isochrone.geometry
        ):
            raise ValueError("盲区中心和分析域必须与等时圈一致。")
        if self.stages.location is not StageStatus.COMPLETE or self.stages.isochrone in {
            StageStatus.UNAVAILABLE, StageStatus.SKIPPED
        }:
            raise ValueError("成功 Diagnosis 必须有位置和等时圈。")
        if (self.poi is None and self.stages.poi is not StageStatus.UNAVAILABLE) or (
            self.poi is not None and self.stages.poi not in {StageStatus.COMPLETE, StageStatus.PARTIAL}
        ):
            raise ValueError("POI 阶段状态与数据不一致。")
        if (self.facility_routes is None and self.stages.routing not in {StageStatus.UNAVAILABLE, StageStatus.SKIPPED}) or (
            self.facility_routes is not None and self.stages.routing not in {StageStatus.COMPLETE, StageStatus.PARTIAL}
        ):
            raise ValueError("Routing 阶段状态与数据不一致。")
        if self.blindspot is None and self.stages.blindspot not in {StageStatus.UNAVAILABLE, StageStatus.SKIPPED}:
            raise ValueError("Blindspot 阶段状态与数据不一致。")
        if self.blindspot is not None and self.stages.blindspot in {StageStatus.UNAVAILABLE, StageStatus.SKIPPED}:
            raise ValueError("Blindspot 阶段状态与数据不一致。")
        return self


class DiagnosisErrorDetail(ErrorDetail):
    code: DiagnosisErrorCode = Field(strict=False)


class DiagnosisSuccess(ContractModel):
    ok: Literal[True]
    data: DiagnosisData
    warnings: list[WarningItem]
    meta: Meta

    @model_validator(mode="after")
    def warnings_are_unique(self) -> "DiagnosisSuccess":
        if len({(item.code, item.message) for item in self.warnings}) != len(self.warnings):
            raise ValueError("Diagnosis warnings 必须去重。")
        return self


class DiagnosisFailure(ContractModel):
    ok: Literal[False]
    error: DiagnosisErrorDetail
    warnings: list[WarningItem]
    meta: Meta


DiagnosisEnvelope = Annotated[
    Union[DiagnosisSuccess, DiagnosisFailure], Field(discriminator="ok")
]


class DiagnosisResult(RootModel[DiagnosisEnvelope]):
    pass


def validate_diagnosis_exchange(request: DiagnosisRequest, result: DiagnosisResult) -> None:
    """Check cross-envelope facts that the result alone cannot know."""
    response = result.root
    if not isinstance(response, DiagnosisSuccess):
        return
    data = response.data
    if [item.facility_type for item in data.metrics] != request.facility_types:
        raise ValueError("指标类别必须按请求顺序排列。")
    minimum = minimum_poi_search_radius_m(data.center, data.isochrone.geometry)
    if data.poi_search_radius_m < minimum:
        raise ValueError("Diagnosis POI 半径未覆盖等时圈与 1 公里缓冲。")
    counts = data.poi.counts if data.poi is not None else None
    coverage = {item.facility_type: item for item in data.blindspot.coverage} if data.blindspot else {}
    if data.blindspot and list(coverage) != request.facility_types:
        raise ValueError("盲区类别与请求不一致。")
    for metric in data.metrics:
        expected = getattr(counts, metric.facility_type.value) if counts is not None else None
        if metric.poi_count != expected:
            raise ValueError("poi_count 必须沿用 POI 的整数或 null。")
        spatial = coverage.get(metric.facility_type)
        ratios = (
            (spatial.covered_ratio, spatial.blind_ratio, spatial.unknown_ratio)
            if spatial else (None, None, None)
        )
        if (metric.covered_ratio, metric.blind_ratio, metric.unknown_ratio) != ratios:
            raise ValueError("覆盖比例必须沿用 Blindspot，不能自行重算。")
    if data.poi is None:
        if data.facility_routes is not None or any(metric.confirmed_reachable_15m_count is not None for metric in data.metrics):
            raise ValueError("POI 失败不能伪造设施路线或确认数量。")
    elif data.facility_routes is not None:
        expected_ids = {poi.poi_id for poi in data.poi.pois}
        actual_ids = {item.target_id for item in data.facility_routes.routes}
        if expected_ids != actual_ids or len(actual_ids) != len(data.facility_routes.routes):
            raise ValueError("设施路线必须按唯一 poi_id 覆盖本次检索到的 POI。")
    if data.quality.confidence.value == "high" and any(
        status in {StageStatus.PARTIAL, StageStatus.UNAVAILABLE}
        for status in (data.stages.poi, data.stages.routing, data.stages.blindspot)
    ):
        raise ValueError("存在部分/不可用阶段时整体质量不能 high。")
