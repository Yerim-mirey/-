"""Pydantic v2 contracts for point-to-many walking routing."""

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


class TravelMode(str, Enum):
    WALKING = "walking"


class RouteStatus(str, Enum):
    SUCCESS = "success"
    NO_ROUTE = "no_route"
    UNAVAILABLE = "unavailable"


class RoutingErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"


class RouteTarget(ContractModel):
    target_id: str = Field(min_length=1)
    location: CenterPoint


class RoutingRequest(ContractModel):
    schema_version: SchemaVersion
    origin: CenterPoint
    targets: list[RouteTarget] = Field(min_length=1)
    travel_mode: TravelMode = Field(strict=False)

    @model_validator(mode="after")
    def target_ids_are_unique(self) -> "RoutingRequest":
        ids = [target.target_id for target in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("target_id 在同一请求中必须唯一。")
        return self


class RouteResult(ContractModel):
    target_id: str = Field(min_length=1)
    location: CenterPoint
    status: RouteStatus = Field(strict=False)
    distance_m: int | None = Field(default=None, ge=0)
    duration_s: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def values_match_status(self) -> "RouteResult":
        has_values = self.distance_m is not None and self.duration_s is not None
        if self.status is RouteStatus.SUCCESS and not has_values:
            raise ValueError("success 路线必须包含距离和时间。")
        if self.status is not RouteStatus.SUCCESS and (
            self.distance_m is not None or self.duration_s is not None
        ):
            raise ValueError("非 success 路线的距离和时间必须为空。")
        return self


class RoutingSummary(ContractModel):
    requested: int = Field(ge=1)
    success: int = Field(ge=0)
    no_route: int = Field(ge=0)
    unavailable: int = Field(ge=0)

    @model_validator(mode="after")
    def counts_add_up(self) -> "RoutingSummary":
        if self.success + self.no_route + self.unavailable != self.requested:
            raise ValueError("Routing summary 数量不一致。")
        return self


class RoutingData(ContractModel):
    origin: CenterPoint
    travel_mode: TravelMode = Field(strict=False)
    routes: list[RouteResult] = Field(min_length=1)
    summary: RoutingSummary

    @model_validator(mode="after")
    def summary_matches_routes(self) -> "RoutingData":
        actual = {status: 0 for status in RouteStatus}
        for route in self.routes:
            actual[route.status] += 1
        if self.summary.requested != len(self.routes) or (
            self.summary.success != actual[RouteStatus.SUCCESS]
            or self.summary.no_route != actual[RouteStatus.NO_ROUTE]
            or self.summary.unavailable != actual[RouteStatus.UNAVAILABLE]
        ):
            raise ValueError("Routing summary 与 routes 不一致。")
        return self


class RoutingErrorDetail(ErrorDetail):
    code: RoutingErrorCode = Field(strict=False)


class RoutingSuccess(ContractModel):
    ok: Literal[True]
    data: RoutingData
    warnings: list[WarningItem]
    meta: Meta


class RoutingFailure(ContractModel):
    ok: Literal[False]
    error: RoutingErrorDetail
    warnings: list[WarningItem]
    meta: Meta


RoutingEnvelope = Annotated[
    Union[RoutingSuccess, RoutingFailure],
    Field(discriminator="ok"),
]


class RoutingResult(RootModel[RoutingEnvelope]):
    pass
