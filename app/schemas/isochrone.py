"""Pydantic v2 contracts for approximate walking isochrones."""

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


class IsochroneConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IsochroneErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    ROUTING_FAILED = "ROUTING_FAILED"
    INSUFFICIENT_ROUTING_DATA = "INSUFFICIENT_ROUTING_DATA"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"


class IsochroneRequest(ContractModel):
    schema_version: SchemaVersion
    center: CenterPoint
    time_limit_s: int = Field(gt=0)


class GeoJSONPolygon(ContractModel):
    type: Literal["Polygon"]
    coordinates: list[list[list[float]]]

    @model_validator(mode="after")
    def valid_single_outer_ring(self) -> "GeoJSONPolygon":
        if len(self.coordinates) != 1:
            raise ValueError("Isochrone v1 仅支持一个无孔外环。")
        ring = self.coordinates[0]
        if len(ring) < 4 or ring[0] != ring[-1]:
            raise ValueError("Polygon 外环必须至少三个顶点并闭合。")
        if len({tuple(point) for point in ring[:-1]}) < 3:
            raise ValueError("Polygon 至少需要三个不同顶点。")
        for point in ring:
            if (
                len(point) != 2
                or not all(math.isfinite(value) for value in point)
                or not -180 <= point[0] <= 180
                or not -90 <= point[1] <= 90
            ):
                raise ValueError("GeoJSON 坐标必须是合法的 [lng, lat]。")
        return self


class BoundaryPoint(ContractModel):
    boundary_id: str = Field(min_length=1)
    bearing_deg: float = Field(ge=0, lt=360, allow_inf_nan=False)
    location: CenterPoint
    radial_distance_m: float = Field(ge=0, allow_inf_nan=False)


class IsochroneQuality(ContractModel):
    requested_directions: int = Field(ge=3)
    valid_boundary_directions: int = Field(ge=3)
    routing_sample_count: int = Field(ge=1)
    routing_success_count: int = Field(ge=0)
    sample_success_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    confidence: IsochroneConfidence = Field(strict=False)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> "IsochroneQuality":
        if self.valid_boundary_directions > self.requested_directions:
            raise ValueError("有效边界方向不能超过请求方向。")
        if self.routing_success_count > self.routing_sample_count:
            raise ValueError("成功样本数不能超过总样本数。")
        expected = self.routing_success_count / self.routing_sample_count
        if abs(self.sample_success_rate - expected) > 0.0001:
            raise ValueError("sample_success_rate 与样本数量不一致。")
        return self


class IsochroneData(ContractModel):
    center: CenterPoint
    time_limit_s: int = Field(gt=0)
    geometry: GeoJSONPolygon
    boundary_points: list[BoundaryPoint] = Field(min_length=3)
    quality: IsochroneQuality

    @model_validator(mode="after")
    def boundary_and_geometry_are_consistent(self) -> "IsochroneData":
        points = self.boundary_points
        if len(points) != self.quality.valid_boundary_directions:
            raise ValueError("boundary_points 与有效方向数不一致。")
        if len({point.boundary_id for point in points}) != len(points):
            raise ValueError("boundary_id 必须唯一。")
        bearings = [point.bearing_deg for point in points]
        if bearings != sorted(bearings) or len(set(bearings)) != len(bearings):
            raise ValueError("boundary_points 必须按唯一 bearing 排序。")
        expected_ring = [[point.location.lng, point.location.lat] for point in points]
        expected_ring.append(expected_ring[0])
        if self.geometry.coordinates[0] != expected_ring:
            raise ValueError("Polygon 外环必须由 boundary_points 按顺序闭合生成。")
        return self


class IsochroneErrorDetail(ErrorDetail):
    code: IsochroneErrorCode = Field(strict=False)


class IsochroneSuccess(ContractModel):
    ok: Literal[True]
    data: IsochroneData
    warnings: list[WarningItem]
    meta: Meta


class IsochroneFailure(ContractModel):
    ok: Literal[False]
    error: IsochroneErrorDetail
    warnings: list[WarningItem]
    meta: Meta


IsochroneEnvelope = Annotated[
    Union[IsochroneSuccess, IsochroneFailure],
    Field(discriminator="ok"),
]


class IsochroneResult(RootModel[IsochroneEnvelope]):
    pass
