"""Version 2 diagnosis contracts with explicit per-facility evidence quality."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import ContractModel


class DiagnosisRequestV2(ContractModel):
    schema_version: Literal["2.0"]
    facility_ids: list[str] = Field(min_length=1)


class FacilityEvidenceV2(ContractModel):
    """Offline evidence supplied by upstream POI/routing/coverage stages."""

    status: Literal["complete", "partial", "unavailable"] = "complete"
    poi_count: int | None = Field(default=None, ge=0)
    route_durations_s: list[int] | None = None
    covered_ratio: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def evidence_counts_are_consistent(self) -> "FacilityEvidenceV2":
        if self.route_durations_s is not None and any(value < 0 for value in self.route_durations_s):
            raise ValueError("route durations must be non-negative")
        if (
            self.status == "complete"
            and self.route_durations_s is not None
            and self.poi_count is not None
            and len(self.route_durations_s) != self.poi_count
        ):
            raise ValueError("complete evidence must contain one route duration per POI")
        return self


class FacilityMetricV2(ContractModel):
    facility_id: str = Field(min_length=1)
    metric_profile: str | None = None
    status: Literal["complete", "partial", "unavailable"]
    poi_count: int | None = Field(default=None, ge=0)
    reachable_count: int | None = Field(default=None, ge=0)
    covered_ratio: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    time_limit_s: int | None = Field(default=None, gt=0)
    service_distance_m: int | None = Field(default=None, gt=0)
    unsupported_dimensions: list[str] = Field(default_factory=list)
    reason_code: str | None = None


class DiagnosisDataV2(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    metrics: list[FacilityMetricV2] = Field(min_length=1)
    quality: Literal["complete", "partial", "mixed", "unavailable"]
