"""Pydantic v2 contracts for the POI tool.

This module uses the shared models from the Location contract package.
"""

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import Field, RootModel

from app.schemas.common import (
    CenterPoint,
    ContractModel,
    ErrorDetail,
    Meta,
    SchemaVersion,
    WarningItem,
)


class FacilityType(str, Enum):
    MARKET = "market"
    PHARMACY = "pharmacy"
    PRIMARY_SCHOOL = "primary_school"


class POIErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"


class POISearchRequest(ContractModel):
    schema_version: SchemaVersion
    center: CenterPoint
    facility_types: list[Annotated[FacilityType, Field(strict=False)]] = Field(
        min_length=1
    )
    search_radius_m: int = Field(gt=0)


class POI(ContractModel):
    poi_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    facility_type: FacilityType = Field(strict=False)
    location: CenterPoint
    address: str | None = Field(default=None, min_length=1)
    source: Literal["baidu"]


class POICounts(ContractModel):
    market: int | None = Field(ge=0)
    pharmacy: int | None = Field(ge=0)
    primary_school: int | None = Field(ge=0)


class POISearchData(ContractModel):
    center: CenterPoint
    pois: list[POI]
    counts: POICounts


class POIErrorDetail(ErrorDetail):
    code: POIErrorCode = Field(strict=False)


class POISearchSuccess(ContractModel):
    ok: Literal[True]
    data: POISearchData
    warnings: list[WarningItem]
    meta: Meta


class POISearchFailure(ContractModel):
    ok: Literal[False]
    error: POIErrorDetail
    warnings: list[WarningItem]
    meta: Meta


POISearchEnvelope = Annotated[
    Union[POISearchSuccess, POISearchFailure],
    Field(discriminator="ok"),
]


class POISearchResult(RootModel[POISearchEnvelope]):
    """A POI result that is exactly one of success or failure."""

    pass
