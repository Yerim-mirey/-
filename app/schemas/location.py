"""Pydantic v2 contracts for the Location tool."""

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


class LocationErrorCode(str, Enum):
    LOCATION_NOT_FOUND = "LOCATION_NOT_FOUND"
    INVALID_COORDINATE = "INVALID_COORDINATE"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    INVALID_REQUEST = "INVALID_REQUEST"


class LocationSource(str, Enum):
    INPUT_COORDINATE = "input_coordinate"
    BAIDU_GEOCODING = "baidu_geocoding"
    BAIDU_REVERSE_GEOCODING = "baidu_reverse_geocoding"


class AddressInput(ContractModel):
    type: Literal["address"]
    query: str = Field(min_length=1)
    city: str | None = Field(default=None, min_length=1)


class CoordinateInput(CenterPoint):
    type: Literal["coordinate"]


LocationInput = Annotated[
    Union[AddressInput, CoordinateInput],
    Field(discriminator="type"),
]


class LocationRequest(ContractModel):
    schema_version: SchemaVersion
    input: LocationInput


class LocationData(ContractModel):
    center: CenterPoint
    label: str | None = Field(default=None, min_length=1)
    formatted_address: str | None = Field(default=None, min_length=1)
    source: LocationSource = Field(strict=False)


class LocationErrorDetail(ErrorDetail):
    code: LocationErrorCode = Field(strict=False)


class LocationSuccess(ContractModel):
    ok: Literal[True]
    data: LocationData
    warnings: list[WarningItem]
    meta: Meta


class LocationFailure(ContractModel):
    ok: Literal[False]
    error: LocationErrorDetail
    warnings: list[WarningItem]
    meta: Meta


LocationEnvelope = Annotated[
    Union[LocationSuccess, LocationFailure],
    Field(discriminator="ok"),
]


class LocationResult(RootModel[LocationEnvelope]):
    """A result that is exactly one of success or failure."""

    pass
