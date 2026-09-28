"""Generic V2 walking-route contracts for facility lookup results."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import CenterPoint, ContractModel


class RouteTargetV2(ContractModel):
    target_id: str = Field(min_length=1)
    facility_id: str = Field(min_length=1)
    location: CenterPoint


class RoutingRequestV2(ContractModel):
    schema_version: Literal["2.0"]
    origin: CenterPoint
    targets: list[RouteTargetV2] = Field(min_length=1, max_length=20)
    travel_mode: Literal["walking"]

    @model_validator(mode="after")
    def target_ids_unique(self) -> "RoutingRequestV2":
        ids = [item.target_id for item in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("target_id must be unique")
        return self


class RouteResultV2(ContractModel):
    target_id: str
    facility_id: str
    location: CenterPoint
    status: Literal["success", "no_route", "unavailable"]
    distance_m: int | None = Field(default=None, ge=0)
    duration_s: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def values_match_status(self) -> "RouteResultV2":
        has_values = self.distance_m is not None and self.duration_s is not None
        has_any_value = self.distance_m is not None or self.duration_s is not None
        if (self.status == "success" and not has_values) or (
            self.status != "success" and has_any_value
        ):
            raise ValueError("route values must agree with status")
        return self


class RoutingSummaryV2(ContractModel):
    requested: int = Field(gt=0)
    success: int = Field(ge=0)
    no_route: int = Field(ge=0)
    unavailable: int = Field(ge=0)

    @model_validator(mode="after")
    def counts_add_up(self) -> "RoutingSummaryV2":
        if self.success + self.no_route + self.unavailable != self.requested:
            raise ValueError("routing summary counts must add up")
        return self


class RoutingResponseV2(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    provider: Literal["baidu"] = "baidu"
    ok: bool
    routes: list[RouteResultV2] = Field(default_factory=list)
    summary: RoutingSummaryV2 | None = None
    error_code: str | None = None
    message: str | None = None
    retryable: bool | None = None

    @model_validator(mode="after")
    def success_and_failure_are_exclusive(self) -> "RoutingResponseV2":
        if self.ok:
            if self.error_code is not None or self.message is not None or self.retryable is not None:
                raise ValueError("successful routing response cannot contain error fields")
            if self.summary is None or self.summary.requested != len(self.routes):
                raise ValueError("successful routing response requires a matching summary")
            statuses = {name: 0 for name in ("success", "no_route", "unavailable")}
            for route in self.routes:
                statuses[route.status] += 1
            if any(getattr(self.summary, name) != count for name, count in statuses.items()):
                raise ValueError("routing summary must match returned routes")
        elif (
            self.routes or self.summary is not None or self.error_code is None
            or self.message is None or self.retryable is None
        ):
            raise ValueError("failed routing response requires only a complete error payload")
        return self
