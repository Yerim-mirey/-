"""Generalized POI contracts for the version 2 API."""

from typing import Annotated, Literal, Union

from pydantic import Field, RootModel, model_validator

from app.schemas.common import CenterPoint, ContractModel


class FacilityCountV2(ContractModel):
    """A facility count with explicit evidence completeness."""

    facility_id: str = Field(min_length=1)
    count: int | None = Field(default=None, ge=0)
    status: Literal["complete", "partial", "unavailable"]

    @model_validator(mode="after")
    def status_matches_count(self) -> "FacilityCountV2":
        if (self.status == "unavailable") != (self.count is None):
            raise ValueError("unavailable must use count=null; complete/partial require a count")
        return self


class POISearchRequestV2(ContractModel):
    """Search request that accepts catalog and exploratory facility IDs."""

    schema_version: Literal["2.0"]
    center: CenterPoint
    facility_ids: list[str] = Field(min_length=1, max_length=100)
    search_radius_m: int = Field(gt=0, le=5000)


class POIExploreRequestV2(ContractModel):
    """Explicit out-of-catalog facility lookup; never a diagnosis input."""

    schema_version: Literal["2.0"]
    query: str = Field(min_length=2, max_length=40)
    center: CenterPoint
    search_radius_m: int = Field(gt=0, le=5000)


class POIResultV2(ContractModel):
    """A map result; exploratory matches remain visibly distinct."""

    poi_id: str = Field(min_length=1)
    facility_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    location: CenterPoint
    address: str | None = Field(default=None, min_length=1)
    source: Literal["baidu"]
    exploratory: bool = False
    query_term: str | None = Field(default=None, min_length=1)
    classification_confidence: Literal["high", "medium", "low"]


class POISearchDataV2(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    provider: Literal["baidu"] = "baidu"
    center: CenterPoint
    pois: list[POIResultV2]
    counts: list[FacilityCountV2]
    exploratory_query: str | None = Field(default=None, min_length=1)
    failed_queries: list["FacilityQueryFailureV2"] = Field(default_factory=list)


class FacilityQueryFailureV2(ContractModel):
    facility_id: str = Field(min_length=1)
    query_term: str = Field(min_length=1)
    error_code: str = Field(min_length=1)
    retryable: bool


class POISearchSuccessV2(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    ok: Literal[True]
    data: POISearchDataV2


class POISearchFailureV2(ContractModel):
    schema_version: Literal["2.0"] = "2.0"
    provider: Literal["baidu"] = "baidu"
    ok: Literal[False]
    error_code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool
    failed_queries: list[FacilityQueryFailureV2] = Field(default_factory=list)


POISearchEnvelopeV2 = Annotated[
    Union[POISearchSuccessV2, POISearchFailureV2],
    Field(discriminator="ok"),
]


class POISearchResultV2(RootModel[POISearchEnvelopeV2]):
    """A V2 POI result that is exactly one of success or failure."""

    pass
